from __future__ import annotations

import argparse
import csv
import json
import math
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dgf_bench.score_submission import score
from .agent_runner import AgentConfig, AgentRunError, run_occurrence
from .model_catalog import compact
from .openrouter_client import OpenRouterClient, Usage, BudgetStopped, BillingUnknown, DEFAULT_HTTP_RETRIES
from dgf_bench.benchmark_protocol import INFORMATION_CONDITIONS, POLICY_FORMS, PROTOCOL_VERSION, dataset_fingerprint, source_fingerprint, json_hash, make_handoff


_PRINT_LOCK = threading.Lock()


def _log(message: str) -> None:
    with _PRINT_LOCK:
        print(message, flush=True)


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", s)[:160]+'__'+json_hash(s)[:12]


def load_model_caps(client: OpenRouterClient) -> dict[str, dict[str, Any]]:
    return {m.get("id"): compact(m) for m in client.list_models() if m.get("id")}


def iter_cases(dataset: Path):
    dataset = Path(dataset)
    if (dataset / "00_project_context.json").exists():
        return [dataset]
    manifest=dataset/'dataset_manifest.json'
    if manifest.exists():
        payload=json.loads(manifest.read_text(encoding='utf-8'))
        rows=payload if isinstance(payload,list) else payload.get('cases',[])
        cases=[]
        for row in rows:
            p=(dataset/row['case_dir']).resolve()
            if dataset.resolve() not in p.parents or not (p/'00_project_context.json').is_file():
                raise ValueError('Invalid case path in dataset manifest')
            if p in cases: raise ValueError('Duplicate case in dataset manifest')
            cases.append(p)
        return cases
    return sorted([p for p in dataset.iterdir() if p.is_dir() and (p / "00_project_context.json").exists()])


def build_job_schedule(cases: list[Path], models: list[str], schedule: str = "round_robin") -> list[tuple[int, int, Path, str]]:
    """Return (case_index, model_index, case_path, model).

    round_robin rotates model order between cases. With concurrent execution this keeps the
    submission queue balanced, so an interrupted or budget-limited run is not dominated by the
    first model in the command line.
    """
    jobs: list[tuple[int, int, Path, str]] = []
    if schedule == "model_major":
        for mi, model in enumerate(models):
            for ci, case in enumerate(cases):
                jobs.append((ci, mi, case, model))
        return jobs
    n = max(1, len(models))
    for ci, case in enumerate(cases):
        order = list(range(n))
        shift = ci % n
        order = order[shift:] + order[:shift]
        for mi in order:
            jobs.append((ci, mi, case, models[mi]))
    return jobs


def effective_per_model_limit(workers: int, model_count: int, configured: int) -> int:
    if configured > 0:
        return configured
    return max(1, math.ceil(max(1, workers) / max(1, model_count)))


@dataclass(frozen=True)
class Job:
    index: int
    case_index: int
    model_index: int
    case_dir: Path
    model: str

    @property
    def id(self) -> str:
        return f"{self.index}:{self.model}:{self.case_dir.name}"


class CostBudget:
    """Thread-safe concurrent cost ledger with per-job reservations.

    The reservation is deliberately conservative: it prevents all workers from observing the same
    remaining budget and simultaneously starting new paid routes. Actual OpenRouter charges are
    recorded after every API response and checked before every new request. Because provider cost
    is only known after a response, in-flight calls can still cross the cap. Unknown cost stops
    subsequent requests under a finite cap. Reservations constrain admission of new jobs.
    """

    def __init__(self, cap: float | None, initial_spent: float = 0.0, reserve_per_job: float = 0.50):
        self.cap = cap
        self.spent = float(initial_spent)
        self.reserve_per_job = max(0.0, float(reserve_per_job))
        self._reservations: dict[str, float] = {}
        self._lock = threading.Lock()
        self.unknown_cost_calls = 0

    def reserve(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._reservations:
                return True
            if self.cap is None:
                self._reservations[job_id] = 0.0
                return True
            if self.spent >= self.cap or self.unknown_cost_calls:
                return False
            committed = self.spent + sum(self._reservations.values())
            remaining = self.cap - committed
            if self.reserve_per_job > 0 and remaining < self.reserve_per_job:
                return False
            self._reservations[job_id] = min(self.reserve_per_job, max(0.0, remaining))
            return True

    def charge(self, job_id: str, amount: float) -> None:
        amount = max(0.0, float(amount or 0.0))
        if amount == 0:
            return
        with self._lock:
            self.spent += amount
            if job_id in self._reservations:
                self._reservations[job_id] = max(0.0, self._reservations[job_id] - amount)

    def release(self, job_id: str) -> None:
        with self._lock:
            self._reservations.pop(job_id, None)

    def hard_stop(self) -> bool:
        with self._lock:
            return self.cap is not None and (self.spent >= self.cap or self.unknown_cost_calls > 0)

    def mark_unknown(self, count):
        with self._lock: self.unknown_cost_calls += count

    def snapshot(self) -> dict[str, float | int | None]:
        with self._lock:
            reserved = sum(self._reservations.values())
            return {
                "cap": self.cap,
                "spent": self.spent,
                "reserved": reserved,
                "committed": self.spent + reserved,
                "inflight_reservations": len(self._reservations),
                "unknown_cost_calls": self.unknown_cost_calls,
            }


class BudgetedClient:
    """Account at response boundaries, before any subsequent agent API call."""
    def __init__(self, client, budget, job_id, ledger=None):
        self.client, self.budget, self.job_id = client, budget, job_id
        self.charged_cost = 0.0
        self.ledger=ledger
        self.client.before_attempt = self._check_budget

    def _check_budget(self):
        if self.budget.hard_stop():
            raise BudgetStopped('Global budget exhausted or previous response cost unknown')

    def chat(self, body):
        self._check_budget()
        try:
            response=self.client.chat(body)
        except BillingUnknown:
            self._account(Usage(unknown_cost_calls=1))
            raise
        usage=Usage.from_response(response)
        self._account(usage)
        return response

    def _account(self,usage):
        if self.ledger:
            with self.ledger.open('a',encoding='utf-8') as f:
                f.write(json.dumps(usage.as_dict())+'\n')
                f.flush()
        self.budget.charge(self.job_id,usage.cost)
        self.budget.mark_unknown(usage.unknown_cost_calls)
        self.charged_cost += usage.cost

    def stats_snapshot(self):
        return self.client.stats_snapshot()


def _usage_from_record(record: dict[str, Any] | None) -> Usage:
    if not record:
        return Usage()
    u = record.get("usage") or {}
    return Usage(**{k: u.get(k, 0) for k in Usage().__dict__.keys()})


def _resolved_summary(values: list[str], requested: str) -> dict[str, Any]:
    uniq = list(dict.fromkeys([str(x) for x in values if x]))
    return {
        "requested_model": requested,
        "resolved_models": uniq,
        "model_resolution_mismatch": bool(uniq and any(x != requested for x in uniq)),
    }


def _usage_add_dict(target: Usage, payload: dict[str, Any] | None) -> None:
    if not payload:
        return
    target.add(Usage(**{k: payload.get(k, 0) for k in Usage().__dict__.keys()}))


def _checkpoint_records(model_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    successes: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for p in sorted(model_dir.glob("[0-9][0-9]_*.json")):
        try:
            record = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "_ERROR" in p.stem:
            errors.append(record)
            continue
        result = record.get("result")
        if isinstance(result, dict) and result.get("occurrence_id"):
            successes[str(result["occurrence_id"])] = record
    return successes, errors


def _prior_paid_cost(results_dir: Path) -> float:
    """Count paid calls already present in a resumed output tree exactly once.

    Completed cases use score.json. Incomplete cases use per-gate checkpoints, including the latest
    failed occurrence trace, so restarting after Ctrl+C does not pretend previous API calls were free.
    """
    total = 0.0
    for model_dir in [p for p in results_dir.iterdir() if p.is_dir()] if results_dir.exists() else []:
        for case_dir in [p for p in model_dir.iterdir() if p.is_dir()]:
            ledger=case_dir/'usage_ledger.jsonl'
            if ledger.exists():
                total += sum(float(json.loads(line).get('cost',0)) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip())
                continue
            sp = case_dir / "score.json"
            if sp.exists():
                try:
                    sc = json.loads(sp.read_text(encoding="utf-8"))
                    total += float((sc.get("openrouter_usage") or {}).get("cost", 0) or 0)
                except Exception:
                    pass
                continue
            successes, errors = _checkpoint_records(case_dir)
            for rec in successes.values():
                total += float((rec.get("usage") or {}).get("cost", 0) or 0)
            for rec in errors:
                total += float((rec.get("usage") or {}).get("cost", 0) or 0)
    return total


def _resume_row(model: str, case_dir: Path, route_key: str, sc: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": model,
        "case": case_dir.name,
        "route": route_key,
        "status": "RESUMED",
        "original_status": sc.get("status"),
        "overall_score": sc.get("overall_score"),
        "cost": (sc.get("openrouter_usage") or {}).get("cost", 0),
    }


def _run_case_model_job(
    *,
    job: Job,
    total_jobs: int,
    case_count: int,
    model_count: int,
    api_key: str,
    caps: dict[str, Any],
    out_dir: Path,
    budget: CostBudget,
    max_turns: int,
    max_tool_calls: int,
    max_tokens: int,
    temperature: float,
    vision: str,
    reasoning_effort: str | None,
    handoff_mode: str,
    resume: bool,
    http_retries: int = DEFAULT_HTTP_RETRIES,
    information_condition: str = "facts",
    provider_order: list[str] | None = None,
    policy_form: str = "code",
    reading_conventions: bool = True,
) -> dict[str, Any]:
    model = job.model
    case_dir = job.case_dir
    job_started = time.perf_counter()
    use_vision = vision == "on" or (vision == "auto" and "image" in set(caps.get("input_modalities") or []))

    prefix = f"[job {job.index}/{total_jobs} | {model} | {case_dir.name}]"
    _log(f"{prefix} START (case {job.case_index+1}/{case_count}, model {job.model_index+1}/{model_count})")

    case_public = json.loads((case_dir / "00_project_context.json").read_text(encoding="utf-8"))
    route = json.loads((case_dir / "01_route_manifest.json").read_text(encoding="utf-8"))
    oracle_by_oid: dict[str, dict[str, Any]] = {}
    if handoff_mode == "oracle":
        hidden = json.loads((case_dir / "99_hidden_ground_truth.json").read_text(encoding="utf-8"))
        oracle_by_oid = {r["occurrence_id"]: r for r in hidden.get("reference_decisions", [])}

    model_dir = out_dir / slug(model) / case_dir.name
    model_dir.mkdir(parents=True, exist_ok=True)
    ledger=model_dir/'usage_ledger.jsonl'
    client = BudgetedClient(OpenRouterClient(api_key=api_key, retries=http_retries), budget, job.id, ledger)
    score_path = model_dir / "score.json"

    results: list[dict[str, Any]] = []
    upstream: list[dict[str, Any]] = []
    run_usage = Usage()
    if resume and ledger.exists():
        for line in ledger.read_text(encoding='utf-8').splitlines():
            if line.strip(): run_usage.add(Usage(**json.loads(line)))
    occurrences = route.get("occurrences", [])
    case_truncated = 0
    case_finish_reasons: list[Any] = []
    case_resolved: list[str] = []
    case_providers: list[Any] = []
    execution_error = None
    error_kind = None
    failed_occurrence = None
    resumed_gate_count = 0

    # Gate-level checkpoint resume. Successful gates are trusted only when they form a valid prefix;
    # this preserves agent handoff ordering while avoiding repeated paid calls after interruption.
    checkpoint_successes: dict[str, dict[str, Any]] = {}
    checkpoint_errors: list[dict[str, Any]] = []
    if resume:
        checkpoint_successes, checkpoint_errors = _checkpoint_records(model_dir)
        # All previous paid attempts belong to the experiment cost, including a successful trace
        # that cannot be reused because an earlier handoff is missing. Semantic reuse below still
        # requires a contiguous valid prefix.
        for rec in list(checkpoint_successes.values()) + checkpoint_errors:
            if not ledger.exists(): _usage_add_dict(run_usage, rec.get("usage") or {})
            case_truncated += int(rec.get("truncated_response_count", 0) or 0)
            case_finish_reasons.extend(rec.get("finish_reasons") or [])
            case_resolved.extend(rec.get("resolved_models") or [])
            case_providers.extend(rec.get("providers") or [])

    prefix_intact = True
    for oi, occ in enumerate(occurrences, start=1):
        oid = occ["occurrence_id"]
        if prefix_intact and oid in checkpoint_successes:
            rec = checkpoint_successes[oid]
            result = rec.get("result") or {}
            if result.get("occurrence_id") != oid:
                prefix_intact = False
            else:
                resumed_gate_count += 1
                results.append(result)
                if handoff_mode == "oracle":
                    ref = oracle_by_oid.get(oid, {})
                    upstream.append(make_handoff(case_public["case_id"], occ, {**ref, 'finding_ids':[f['id'] for f in ref.get('findings',[])], 'actions':[a['action'] for a in ref.get('required_actions',[])], 'verification_status':'oracle'}, PROTOCOL_VERSION))
                elif handoff_mode == "agent":
                    upstream.append(make_handoff(case_public["case_id"], occ, result, PROTOCOL_VERSION, information_condition == 'facts'))
                _log(f"{prefix} gate {oi}/{len(occurrences)} {occ['gate']} RESUMED")
                continue
        prefix_intact = False

        if budget.hard_stop():
            execution_error = "GLOBAL_COST_BUDGET_REACHED"
            error_kind = "budget"
            failed_occurrence = oid
            break

        _log(f"{prefix} gate {oi}/{len(occurrences)} {occ['gate']} ({occ['phase']}) ...")
        cfg = AgentConfig(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_turns=max_turns,
            max_tool_calls=max_tool_calls,
            reasoning_effort=reasoning_effort,
            use_vision=use_vision,
            state_dir=str(model_dir/'environment'/oid),
            information_condition=information_condition,
            provider_order=provider_order,
            policy_form=policy_form,
            reading_conventions=reading_conventions,
        )
        upstream_for_agent = [] if handoff_mode == "none" else upstream
        record = None
        cost_before=client.charged_cost
        try:
            record = run_occurrence(client, case_dir, occ, caps, cfg, upstream_for_agent)
        except AgentRunError as e:
            record = e.record
            execution_error = f"{type(e).__name__}: {e}"
            error_kind = e.kind
            failed_occurrence = oid
            fail_usage = _usage_from_record(record)
            run_usage.add(fail_usage)
            budget.charge(job.id, max(0,fail_usage.cost-(client.charged_cost-cost_before)))
            case_truncated += int(record.get("truncated_response_count", 0) or 0)
            case_finish_reasons.extend(record.get("finish_reasons") or [])
            case_resolved.extend(record.get("resolved_models") or [])
            case_providers.extend(record.get("providers") or [])
            error_payload = {**record, "error": execution_error, "error_kind": error_kind, "occurrence": occ,
                             "http_stats": client.stats_snapshot()}
            error_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            (model_dir / f"{occ['position']:02d}_{oid}_ERROR_{error_stamp}.json").write_text(
                json.dumps(error_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _log(f"{prefix} gate {oi}/{len(occurrences)} ERROR ({error_kind}): {execution_error} | cost=${fail_usage.cost:.5f}")
            break
        except Exception as e:
            execution_error = f"{type(e).__name__}: {e}"
            error_kind = "harness"
            failed_occurrence = oid
            _log(f"{prefix} gate {oi}/{len(occurrences)} HARNESS ERROR: {execution_error}")
            break

        result = record["result"]
        u = Usage(**record["usage"])
        run_usage.add(u)
        budget.charge(job.id, max(0,u.cost-(client.charged_cost-cost_before)))
        trunc = int(record.get("truncated_response_count", 0) or 0)
        case_truncated += trunc
        case_finish_reasons.extend(record.get("finish_reasons") or [])
        case_resolved.extend(record.get("resolved_models") or [])
        case_providers.extend(record.get("providers") or [])
        resolved = (record.get("resolved_models") or [model])[-1]
        mode = record.get("finalization_mode")
        record["http_stats"] = client.stats_snapshot()
        _log(
            f"{prefix} gate {oi}/{len(occurrences)} -> {result.get('disposition')} | turns={record.get('turns')} "
            f"| tools={record.get('tool_call_count')} | final={mode} | resolved={resolved} "
            f"| finish={record.get('last_finish_reason')} | truncated={trunc} | cost=${u.cost:.5f}"
        )
        results.append(result)

        if handoff_mode == "oracle":
            ref = oracle_by_oid.get(oid, {})
            upstream.append(make_handoff(case_public["case_id"], occ, {**ref, 'finding_ids':[f['id'] for f in ref.get('findings',[])], 'actions':[a['action'] for a in ref.get('required_actions',[])], 'verification_status':'oracle'}, PROTOCOL_VERSION))
        elif handoff_mode == "agent":
            upstream.append(make_handoff(case_public["case_id"], occ, result, PROTOCOL_VERSION, information_condition == 'facts'))

        (model_dir / f"{occ['position']:02d}_{oid}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    submission = {"case_id": case_public["case_id"], "model": model, "gate_results": results}
    (model_dir / "submission.json").write_text(json.dumps(submission, ensure_ascii=False, indent=2), encoding="utf-8")
    identity = _resolved_summary(case_resolved, model)

    common = {
        "openrouter_usage": run_usage.as_dict(),
        "information_condition": information_condition,
        "policy_form": policy_form,
        "reading_conventions": reading_conventions,
        "model": model,
        "requested_model": model,
        "vision_used": use_vision,
        "truncated_response_count": case_truncated,
        "finish_reasons": case_finish_reasons,
        "providers": list(dict.fromkeys(map(str, case_providers))),
        "resumed_gate_count": resumed_gate_count,
        "http_stats": client.stats_snapshot(),
        **identity,
    }

    if error_kind in {"infrastructure", "harness", "budget"}:
        sc = {
            "status": "INFRA_ERROR" if error_kind != "budget" else "BUDGET_STOP",
            "error": execution_error,
            "error_kind": error_kind,
            "failed_occurrence": failed_occurrence,
            "overall_score": None,
            **common,
        }
    elif error_kind == "agent_protocol":
        sc = score(case_dir, submission, tool_records={oid:r for oid,r in _checkpoint_records(model_dir)[0].items()},
                   condition=information_condition)
        sc.update({"status": "AGENT_FAILURE", "error": execution_error, "error_kind": error_kind,
                   "failed_occurrence": failed_occurrence, **common})
    else:
        sc = score(case_dir, submission, tool_records={oid:r for oid,r in _checkpoint_records(model_dir)[0].items()},
                   condition=information_condition)
        sc.update({"status": "OK", **common})

    score_path.write_text(json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
    tool_count = 0
    for p in model_dir.glob("[0-9][0-9]_*.json"):
        if "_ERROR" in p.stem:
            continue
        try:
            tool_count += int(json.loads(p.read_text(encoding="utf-8")).get("tool_call_count", 0) or 0)
        except Exception:
            pass
    row = {
        "model": model,
        "case": case_dir.name,
        "route": route["route"]["key"],
        "information_condition": information_condition,
        "status": sc.get("status"),
        "overall_score": sc.get("overall_score"),
        "strict_gate_success_rate": sc.get("strict_gate_success_rate"),
        "outcome_strict_success_rate": sc.get("outcome_strict_success_rate"),
        "route_complete_execution": sc.get("route_complete_execution"),
        "route_complete_decision": sc.get("route_complete_decision"),
        "critical_miss_count": sc.get("critical_miss_count"),
        "false_approval_count": sc.get("false_approval_count"),
        "gate_attempt_rate": sc.get("gate_attempt_rate"),
        "cost": run_usage.cost,
        "prompt_tokens": run_usage.prompt_tokens,
        "completion_tokens": run_usage.completion_tokens,
        "tool_calls": tool_count,
        "truncated_responses": case_truncated,
        "resolved_models": identity["resolved_models"],
        "model_resolution_mismatch": identity["model_resolution_mismatch"],
        "failed_occurrence": failed_occurrence,
        "resumed_gates": resumed_gate_count,
        "http_retries": client.stats_snapshot().get("retries", 0),
        "rate_limits": client.stats_snapshot().get("rate_limits", 0),
        "elapsed_seconds": round(time.perf_counter() - job_started, 3),
    }
    snap = budget.snapshot()
    _log(f"{prefix} DONE status={row['status']} score={row['overall_score']} cost=${run_usage.cost:.5f} | global_spent=${snap['spent']:.4f}")
    return row


def run_benchmark(
    dataset: Path,
    models: list[str],
    out_dir: Path,
    max_cases: int | None,
    max_cost_usd: float | None,
    max_turns: int,
    max_tool_calls: int,
    max_tokens: int,
    temperature: float,
    vision: str,
    reasoning_effort: str | None,
    handoff_mode: str = "agent",
    resume: bool = True,
    schedule: str = "round_robin",
    workers: int = 6,
    max_workers_per_model: int = 0,
    job_budget_reserve_usd: float = 0.50,
    http_retries: int = DEFAULT_HTTP_RETRIES,
    information_condition: str = "facts",
    providers: dict[str, str] | None = None,
    policy_form: str = "code",
    reading_conventions: bool = True,
):
    benchmark_started = time.perf_counter()
    providers = dict(providers or {})
    if set(providers) - set(models):
        raise ValueError('Pinned providers must name benchmarked models')
    if information_condition not in INFORMATION_CONDITIONS:
        raise ValueError(f'Unknown information condition: {information_condition!r}')
    if handoff_mode == 'oracle' and information_condition != 'facts':
        raise ValueError('Oracle handoffs reveal reference decisions; they are only allowed with the facts condition')
    if policy_form not in POLICY_FORMS:
        raise ValueError(f'Unknown policy form: {policy_form!r}')
    out_dir=Path(out_dir)
    if type(http_retries) is not int or http_retries < 0:
        raise ValueError('HTTP retries must be a nonnegative integer')
    if not models or len(models)!=len(set(models)): raise ValueError('Provide distinct model IDs')
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if max_workers_per_model < 0:
        raise ValueError("max_workers_per_model must be >= 0")
    if job_budget_reserve_usd < 0:
        raise ValueError("job_budget_reserve_usd must be >= 0")

    if max_turns < 1 or max_tokens < 1 or max_tool_calls < 0 or (max_cases is not None and max_cases < 1):
        raise ValueError('Invalid experiment limits')
    if max_cost_usd is not None and (not math.isfinite(max_cost_usd) or max_cost_usd <= 0):
        raise ValueError('Budget must be finite and positive')
    cases=iter_cases(dataset)
    if max_cases: cases=cases[:max_cases]
    if not cases: raise ValueError('Empty dataset')
    for case in cases:
        context=json.loads((case/'00_project_context.json').read_text(encoding='utf-8'))
        if context.get('schema_version') != PROTOCOL_VERSION:
            raise ValueError(f'Legacy dataset: generate a new {PROTOCOL_VERSION} dataset; archived cases must remain unchanged')
        expected_variant = 'attack' if information_condition == 'attack' else 'clean'
        if context.get('variant', 'clean') != expected_variant:
            raise ValueError(f'The {information_condition} condition needs {expected_variant} dossiers; {case.name} is {context.get("variant", "clean")}')
        from dgf_bench.validate_case import validate_case
        # Without snapshots every scheduled gate must be decidable from authoritative public sources.
        errors=validate_case(case, require_certified=information_condition!='facts')
        if errors: raise ValueError(f'Invalid dataset case {case.name}: '+ '; '.join(errors))
    fingerprint={'protocol':PROTOCOL_VERSION,'dataset':dataset_fingerprint(cases),'sources':source_fingerprint(),
                 'models':models,'max_turns':max_turns,'max_tool_calls':max_tool_calls,'max_tokens':max_tokens,
                 'temperature':temperature,'vision':vision,'reasoning_effort':reasoning_effort,'handoff_mode':handoff_mode,
                 'http_retries':http_retries,'information_condition':information_condition,'providers':providers,
                 'policy_form':policy_form,'reading_conventions':reading_conventions}
    identity_path=out_dir/'protocol_identity.json'
    if out_dir.exists() and any(out_dir.iterdir()):
        if not resume: raise ValueError('Nonempty results directory: use a fresh output directory')
        if not identity_path.exists() or json.loads(identity_path.read_text(encoding='utf-8')) != fingerprint:
            raise ValueError('Resume rejected: dataset, sources or protocol differ; use a fresh output directory')
    out_dir.mkdir(parents=True,exist_ok=True)
    identity_path.write_text(json.dumps(fingerprint,indent=2),encoding='utf-8')

    catalog_client = OpenRouterClient(retries=http_retries)
    api_key = catalog_client.api_key
    catalog = load_model_caps(catalog_client)
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_snapshot = {m: catalog.get(m) for m in models}
    (out_dir / "model_catalog_snapshot.json").write_text(json.dumps(selected_snapshot, indent=2), encoding="utf-8")

    active_models: list[str] = []
    summary: list[dict[str, Any]] = []
    for model in models:
        caps = catalog.get(model)
        if not caps:
            summary.append({"model": model, "status": "SKIPPED_NOT_IN_CATALOG"})
            continue
        if "tools" not in set(caps.get("supported_parameters") or []):
            summary.append({"model": model, "status": "SKIPPED_NO_TOOLS"})
            continue
        active_models.append(model)
    if not active_models:
        raise RuntimeError("No selected model supports the required tool-calling protocol")

    jobs_raw = build_job_schedule(cases, active_models, schedule)
    jobs = [Job(i, ci, mi, case_dir, model) for i, (ci, mi, case_dir, model) in enumerate(jobs_raw, start=1)]
    workers = min(workers, max(1, len(jobs)))
    per_model_limit = effective_per_model_limit(workers, len(active_models), max_workers_per_model)

    initial_cost = _prior_paid_cost(out_dir) if resume else 0.0
    budget = CostBudget(max_cost_usd, initial_spent=initial_cost, reserve_per_job=job_budget_reserve_usd)
    if resume:
        for ledger in out_dir.glob('*/*/usage_ledger.jsonl'):
            budget.mark_unknown(sum(json.loads(line).get('unknown_cost_calls',0) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip()))

    started = datetime.now(timezone.utc).isoformat()
    manifest = {
        "started_at": started,
        "dataset": str(Path(dataset).resolve()),
        "models": models,
        "case_count": len(cases),
        "config": {
            "max_cost_usd": max_cost_usd,
            "http_retries": http_retries,
            "max_turns": max_turns,
            "max_tool_calls": max_tool_calls,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "vision": vision,
            "reasoning_effort": reasoning_effort,
            "handoff_mode": handoff_mode,
            "information_condition": information_condition,
            "providers": providers,
            "policy_form": policy_form,
            "reading_conventions": reading_conventions,
            "schedule": schedule,
            "workers": workers,
            "max_workers_per_model": per_model_limit,
            "job_budget_reserve_usd": job_budget_reserve_usd,
            "resume": resume,
        },
    }
    (out_dir / "benchmark_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Completed score files are skipped up front; partial cases remain jobs and resume gate-by-gate.
    pending: list[Job] = []
    for job in jobs:
        route = json.loads((job.case_dir / "01_route_manifest.json").read_text(encoding="utf-8"))
        sp = out_dir / slug(job.model) / job.case_dir.name / "score.json"
        if resume and sp.exists():
            try:
                sc = json.loads(sp.read_text(encoding="utf-8"))
                if sc.get("status") in {"OK", "AGENT_FAILURE"}:
                    summary.append(_resume_row(job.model, job.case_dir, route["route"]["key"], sc))
                    _log(f"[benchmark] resume complete: {job.model} / {job.case_dir.name} status={sc.get('status')}")
                    continue
                _log(f"[benchmark] retrying incomplete result: {job.model} / {job.case_dir.name} status={sc.get('status')}")
            except Exception:
                pass
        pending.append(job)

    _log(
        f"[benchmark] schedule={schedule} | models={len(active_models)} | cases={len(cases)} | jobs={len(jobs)} "
        f"| pending={len(pending)} | workers={workers} | per_model={per_model_limit} | initial_cost=${initial_cost:.4f}"
    )

    inflight: dict[Future, Job] = {}
    inflight_per_model = {m: 0 for m in active_models}
    budget_stopped = False
    completed_new = 0
    resumed_count = sum(1 for r in summary if r.get("status") == "RESUMED")

    def find_eligible_index() -> int | None:
        for idx, job in enumerate(pending):
            if inflight_per_model[job.model] < per_model_limit:
                return idx
        return None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dgfbench") as pool:
        while pending or inflight:
            made_progress = False
            while pending and len(inflight) < workers:
                idx = find_eligible_index()
                if idx is None:
                    break
                job = pending[idx]
                if not budget.reserve(job.id):
                    # Usually temporary: in-flight reservations may be released shortly.
                    # We declare a final budget stop only when no job is left running.
                    break
                pending.pop(idx)
                inflight_per_model[job.model] += 1
                fut = pool.submit(
                    _run_case_model_job,
                    job=job,
                    total_jobs=len(jobs),
                    case_count=len(cases),
                    model_count=len(active_models),
                    api_key=api_key,
                    caps=catalog[job.model],
                    out_dir=out_dir,
                    budget=budget,
                    max_turns=max_turns,
                    max_tool_calls=max_tool_calls,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    vision=vision,
                    reasoning_effort=reasoning_effort,
                    handoff_mode=handoff_mode,
                    resume=resume,
                    http_retries=http_retries,
                    information_condition=information_condition,
                    provider_order=[providers[job.model]] if job.model in providers else None,
                    policy_form=policy_form,
                    reading_conventions=reading_conventions,
                )
                inflight[fut] = job
                made_progress = True

            if not inflight:
                # Remaining jobs cannot acquire a reservation even with no in-flight work.
                # At this point the cost cap / reservation guard is genuinely binding.
                if pending:
                    budget_stopped = True
                break

            done, _ = wait(inflight.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                job = inflight.pop(fut)
                inflight_per_model[job.model] -= 1
                budget.release(job.id)
                completed_new += 1
                try:
                    summary.append(fut.result())
                except Exception as e:
                    # A worker-level crash is harness/infrastructure, never silently discarded.
                    row = {"model": job.model, "case": job.case_dir.name, "status": "WORKER_CRASH",
                           "error": f"{type(e).__name__}: {e}"}
                    summary.append(row)
                    _log(f"[benchmark] WORKER CRASH {job.model} / {job.case_dir.name}: {row['error']}")
                snap = budget.snapshot()
                _log(
                    f"[benchmark] progress new={completed_new}/{len(jobs)-resumed_count} "
                    f"| pending={len(pending)} | inflight={len(inflight)} | spent=${snap['spent']:.4f} "
                    f"| reserved=${snap['reserved']:.4f}"
                )

    snap = budget.snapshot()
    if budget_stopped and pending:
        _log(
            f"[benchmark] GLOBAL COST CAP / reservation guard stopped {len(pending)} unscheduled jobs. "
            f"spent=${snap['spent']:.4f} reserved=${snap['reserved']:.4f} cap=${max_cost_usd:.4f}"
            if max_cost_usd is not None else "[benchmark] scheduling stopped"
        )
        summary.append({"status": "GLOBAL_BUDGET_STOP", "total_cost": snap["spent"],
                        "max_cost_usd": max_cost_usd, "unscheduled_jobs": len(pending)})

    elapsed = time.perf_counter() - benchmark_started
    run_stats = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "workers": workers,
        "max_workers_per_model": per_model_limit,
        "budget": budget.snapshot(),
        "jobs_total": len(jobs),
        "jobs_resumed": resumed_count,
        "jobs_new_completed": completed_new,
        "jobs_unscheduled": len(pending),
    }
    (out_dir / "benchmark_run_stats.json").write_text(json.dumps(run_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = sorted({k for row in summary for k in row})
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary)
    return summary


def main():
    ap = argparse.ArgumentParser(description="Run DGF-Bench agents through OpenRouter")
    ap.add_argument("--dataset", type=Path, required=True, help="Case directory or directory containing multiple cases")
    ap.add_argument("--models", nargs="+", default=None, help="Exact OpenRouter model IDs")
    ap.add_argument("--model-file", type=Path, default=None, help='JSON file with {"models":[...]}')
    ap.add_argument("--output-dir", type=Path, default=Path("openrouter_results") / datetime.now().strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--max-cases", type=int, default=None)
    ap.add_argument("--max-cost-usd", type=float, default=None)
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--max-tool-calls", type=int, default=40)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--vision", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default=None)
    ap.add_argument("--handoff-mode", choices=["agent", "oracle", "none"], default="agent",
                    help="agent=propagate model outputs; oracle=provide reference structured handoffs; none=no upstream handoff")
    ap.add_argument("--schedule", choices=["round_robin", "model_major"], default="round_robin",
                    help="Controls queue order; round_robin is the paper-safe default")
    ap.add_argument("--workers", type=int, default=6,
                    help="Concurrent model×DGF jobs. Gates within one job remain sequential. Default: 6")
    ap.add_argument("--max-workers-per-model", type=int, default=0,
                    help="Per-model concurrency. 0=auto ceil(workers/models). Default: 0")
    ap.add_argument("--job-budget-reserve-usd", type=float, default=0.50,
                    help="Budget reserved before each concurrent job starts. Default: 0.50")
    ap.add_argument("--information-condition", choices=list(INFORMATION_CONDITIONS), default="facts",
                    help="facts=REVIEW_FACTS snapshots available; docs=facts only from documents and records; attack=docs on attack-variant dossiers")
    ap.add_argument("--provider", action="append", default=[], metavar="MODEL=PROVIDER",
                    help="Pin a model to one OpenRouter provider, without fallbacks (repeatable)")
    ap.add_argument("--policy-form", choices=list(POLICY_FORMS), default="code",
                    help="code=rules as the evaluator's Python; prose=the same rules as a written review standard")
    ap.add_argument("--no-reading-conventions", action="store_true",
                    help="Omit the record-reading conventions from the docs/attack policy (difficulty probe)")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--http-retries", type=int, default=DEFAULT_HTTP_RETRIES,
                    help="Retries per HTTP request for temporary failures; 0 disables retries. Default: 8")
    ns = ap.parse_args()

    models = list(ns.models or [])
    if ns.model_file:
        payload = json.loads(ns.model_file.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            models.extend([x.get("id") if isinstance(x, dict) else x for x in payload])
        else:
            models.extend(payload.get("models") or [])
    models = list(dict.fromkeys(models))
    if not models:
        ap.error("Provide --models ... or --model-file models.json")

    run_benchmark(
        ns.dataset,
        models,
        ns.output_dir,
        ns.max_cases,
        ns.max_cost_usd,
        ns.max_turns,
        ns.max_tool_calls,
        ns.max_tokens,
        ns.temperature,
        ns.vision,
        ns.reasoning_effort,
        ns.handoff_mode,
        resume=not ns.no_resume,
        schedule=ns.schedule,
        workers=ns.workers,
        max_workers_per_model=ns.max_workers_per_model,
        job_budget_reserve_usd=ns.job_budget_reserve_usd,
        http_retries=ns.http_retries,
        information_condition=ns.information_condition,
        providers=parse_providers(ns.provider),
        policy_form=ns.policy_form,
        reading_conventions=not ns.no_reading_conventions,
    )


def parse_providers(values):
    pinned = {}
    for value in values:
        model, sep, provider = value.partition('=')
        if not sep or not model.strip() or not provider.strip():
            raise SystemExit(f'--provider expects MODEL=PROVIDER, got {value!r}')
        pinned[model.strip()] = provider.strip()
    return pinned


if __name__ == "__main__":
    main()
