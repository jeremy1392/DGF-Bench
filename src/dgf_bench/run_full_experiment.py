#!/usr/bin/env python3
"""One-command DGF-Bench OpenRouter experiment runner.

Examples
--------
Pilot (safe default):
    dgf-bench run \
      --api-key sk-or-v1-... \
      --models z-ai/glm-5.3 z-ai/glm-5.3-flashx

Paper run (150 cases: 50 Buy + 50 Integrate + 50 Build):
    dgf-bench run \
      --api-key sk-or-v1-... \
      --models MODEL_A MODEL_B MODEL_C \
      --preset paper \
      --max-cost-usd 150

Safer key entry (not stored in shell history):
    dgf-bench run --models MODEL_A MODEL_B --preset paper

In a repository checkout, `python run_full_experiment.py` is equivalent.

The script never writes the API key to disk or result files.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from dgf_bench import __version__
from dgf_bench.benchmark_protocol import PROTOCOL_VERSION, SCORING_VERSION
from dgf_bench.openrouter_eval.env_loader import load_dotenv
from dgf_bench.openrouter_eval.openrouter_client import DEFAULT_HTTP_RETRIES
from dgf_bench.statistical_intervals import cluster_interval

# Child processes get this directory's parent on PYTHONPATH so they import this same copy.
PACKAGE_DIR = Path(__file__).resolve().parent

PRESETS = {
    "smoke": {"cases_per_route": 1, "difficulty": 3, "default_budget": 5.0},
    "pilot": {"cases_per_route": 5, "difficulty": 4, "default_budget": 25.0},
    "paper": {"cases_per_route": 50, "difficulty": 4, "default_budget": 150.0},
}


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], env: dict[str, str], cwd: Path | None = None) -> str:
    """Run a child process while streaming stdout/stderr live.

    v7.2 used capture_output=True, which made long dataset/model steps look frozen
    on Windows. v7.6 forces unbuffered Python and mirrors every child line as it
    arrives while still returning the combined output for callers that need it.
    """
    cmd = list(cmd)
    if cmd and Path(cmd[0]).name.lower().startswith("python") and "-u" not in cmd[:2]:
        cmd.insert(1, "-u")
    child_env = dict(env)
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PACKAGE_DIR.parent), child_env.get("PYTHONPATH")]))
    print("\n$ " + " ".join(cmd), flush=True)
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=child_env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, universal_newlines=True,
    )
    lines=[]
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        print(line.rstrip(), flush=True)
    rc=proc.wait()
    output="".join(lines)
    if rc != 0:
        die(f"Command failed with exit code {rc}: {' '.join(cmd)}")
    return output


def parse_models(raw: list[str]) -> list[str]:
    out: list[str] = []
    for item in raw:
        for part in item.split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    if not out:
        die("At least one OpenRouter model ID is required.")
    return out


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{100.0 * x:.1f}%"


def validate_models(models: list[str], env: dict[str, str], out_dir: Path,
                    http_retries: int = DEFAULT_HTTP_RETRIES) -> dict[str, Any]:
    # Import only after the key is set in env / os.environ.
    os.environ.update({k: v for k, v in env.items() if k.startswith("OPENROUTER_")})
    from dgf_bench.openrouter_eval.openrouter_client import OpenRouterClient
    from dgf_bench.openrouter_eval.model_catalog import compact

    client = OpenRouterClient(retries=http_retries)
    catalog = {m.get("id"): compact(m) for m in client.list_models() if m.get("id")}
    selected: dict[str, Any] = {}
    problems: list[str] = []
    for model in models:
        caps = catalog.get(model)
        if not caps:
            problems.append(f"{model}: not found in current OpenRouter catalog")
            continue
        params = set(caps.get("supported_parameters") or [])
        if "tools" not in params:
            problems.append(f"{model}: catalog does not advertise tool calling")
            continue
        selected[model] = caps
    (out_dir / "model_catalog_selected.json").write_text(
        json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if problems:
        die("Model validation failed:\n  - " + "\n  - ".join(problems))
    print("\nValidated OpenRouter models:")
    for model, caps in selected.items():
        modalities = ",".join(caps.get("input_modalities") or [])
        print(f"  OK  {model}  context={caps.get('context_length')}  input={modalities or 'text'}")
    return selected


def collect_paper_metrics(results_dir: Path, aggregate_payload: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    model_acc: dict[str, dict[str, Any]] = {}
    gate_acc: dict[tuple[str, str], dict[str, Any]] = {}
    scoreable = {"OK", "AGENT_FAILURE"}
    units={}; gate_units={}; outcome_units={}; attack_units={}

    for score_path in results_dir.glob("*/*/score.json"):
        sc = json.loads(score_path.read_text(encoding="utf-8"))
        status = sc.get("status")
        if status not in scoreable:
            continue
        model = sc.get("model") or sc.get("requested_model") or score_path.parents[1].name
        ma = model_acc.setdefault(model, {
            "model": model, "cases": 0, "completed_cases": 0, "agent_failures": 0, "route_successes": 0,
            "gate_total": 0, "gate_attempted": 0, "gate_strict_successes": 0,
            "gate_outcome_successes": 0, "route_outcome_successes": 0,
            "critical_misses": 0, "false_approvals": 0, "total_cost_usd": 0.0,
            "total_tokens": 0, "truncated_responses": 0, "model_resolution_mismatches": 0,
        })
        ma["cases"] += 1
        ma["completed_cases"] += int(status == "OK")
        ma["agent_failures"] += int(status == "AGENT_FAILURE")
        ma["route_successes"] += int(bool(sc.get("route_complete_decision",sc.get("route_complete_execution", False))))
        ma["route_outcome_successes"] += int(bool(sc.get("route_complete_outcome", False)))
        ma["critical_misses"] += int(sc.get("critical_miss_count", 0) or 0)
        ma["false_approvals"] += int(sc.get("false_approval_count", 0) or 0)
        ma["model_resolution_mismatches"] += int(bool(sc.get("model_resolution_mismatch", False)))
        usage = sc.get("openrouter_usage") or {}
        ma["total_cost_usd"] += float(usage.get("cost", 0) or 0)
        ma["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        ma["truncated_responses"] += int(sc.get("truncated_response_count", 0) or 0)
        case_name=score_path.parent.name; route_name=case_name.rsplit('_',1)[-1]
        occurrences=sc.get('occurrences',[])
        units.setdefault(model,[]).append((route_name,sum(bool(o.get('strict_success')) for o in occurrences),len(occurrences)))
        outcome_units.setdefault(model,[]).append((route_name,sum(bool(o.get('outcome_strict')) for o in occurrences),len(occurrences)))
        attacked=[o['attack'] for o in occurrences if o.get('attack') and not o['attack']['placebo']]
        if attacked:
            attack_units.setdefault(model,[]).append((route_name,sum(a['attack_success'] for a in attacked),len(attacked)))
        for gate_name in {o['gate'] for o in occurrences}:
            selected=[o for o in occurrences if o['gate']==gate_name]
            gate_units.setdefault((model,gate_name),[]).append((route_name,sum(bool(o.get('strict_success')) for o in selected),len(selected)))

        for occ in sc.get("occurrences", []):
            gate = str(occ.get("gate"))
            attempted = bool(occ.get("attempted", True))
            strict = int(bool(occ.get("strict_success", False)))
            ma["gate_total"] += 1
            ma["gate_attempted"] += int(attempted)
            ma["gate_strict_successes"] += strict
            ma["gate_outcome_successes"] += int(bool(occ.get("outcome_strict", False)))
            ga = gate_acc.setdefault((model, gate), {
                "model": model, "gate": gate, "n": 0, "attempted": 0, "strict_successes": 0,
                "decision_sum": 0.0, "findings_sum": 0.0, "actions_sum": 0.0,
                "evidence_sum": 0.0, "authorization_sum": 0.0,
            })
            ga["n"] += 1
            ga["attempted"] += int(attempted)
            ga["strict_successes"] += strict
            if attempted:
                ga["decision_sum"] += float(occ.get("decision", 0) or 0)
                ga["findings_sum"] += float(occ.get("findings_f1", 0) or 0)
                ga["actions_sum"] += float(occ.get("actions_f1", 0) or 0)
                ga["evidence_sum"] += float(occ.get("evidence_fidelity", 0) or 0)
                ga["authorization_sum"] += float(occ.get("authorization", 0) or 0)

    agg_models = {r["model"]: r for r in aggregate_payload.get("models", [])}
    overall_rows: list[dict] = []
    for model in sorted(model_acc):
        a = model_acc[model]
        route_rate = a["route_successes"] / a["cases"] if a["cases"] else 0.0
        gate_csr = a["gate_strict_successes"] / a["gate_total"] if a["gate_total"] else 0.0
        attempt_rate = a["gate_attempted"] / a["gate_total"] if a["gate_total"] else 0.0
        rlo, rhi = wilson(a["route_successes"], a["cases"])
        glo, ghi = cluster_interval(units[model])
        overall_rows.append({
            "model": model, "cases": a["cases"], "completed_cases": a["completed_cases"],
            "agent_failures": a["agent_failures"], "gate_occurrences": a["gate_total"],
            "gate_attempt_rate": round(attempt_rate, 6), "mean_score": agg_models.get(model, {}).get("mean_score"),
            "gate_csr": round(gate_csr, 6), "gate_csr_ci95_low": round(glo, 6), "gate_csr_ci95_high": round(ghi, 6),
            "route_complete_rate": round(route_rate, 6), "route_complete_ci95_low": round(rlo, 6),
            "route_complete_ci95_high": round(rhi, 6), "critical_misses": a["critical_misses"],
            "false_approvals": a["false_approvals"], "total_cost_usd": round(a["total_cost_usd"], 6),
            "total_tokens": a["total_tokens"], "cost_per_case_usd": round(a["total_cost_usd"] / a["cases"], 6) if a["cases"] else None,
            "truncated_responses": a["truncated_responses"], "model_resolution_mismatches": a["model_resolution_mismatches"],
        })
        # Primary v9 metric: outcome-strict (decision, findings, actions, authorization), comparable across conditions.
        olo, ohi = cluster_interval(outcome_units[model])
        rolo, rohi = wilson(a["route_outcome_successes"], a["cases"])
        overall_rows[-1].update({
            "outcome_csr": round(a["gate_outcome_successes"] / a["gate_total"], 6) if a["gate_total"] else 0.0,
            "outcome_csr_ci95_low": None if olo is None else round(olo, 6), "outcome_csr_ci95_high": None if ohi is None else round(ohi, 6),
            "route_outcome_rate": round(a["route_outcome_successes"] / a["cases"], 6) if a["cases"] else 0.0,
            "route_outcome_ci95_low": round(rolo, 6), "route_outcome_ci95_high": round(rohi, 6),
        })
        if model in attack_units:
            gates_attacked=sum(u[2] for u in attack_units[model]); successes=sum(u[1] for u in attack_units[model])
            alo, ahi = cluster_interval(attack_units[model])
            overall_rows[-1].update({"attacked_gates": gates_attacked, "attack_success_rate": round(successes / gates_attacked, 6),
                                     "attack_success_ci95_low": None if alo is None else round(alo, 6),
                                     "attack_success_ci95_high": None if ahi is None else round(ahi, 6)})

    gate_rows: list[dict] = []
    for key in sorted(gate_acc):
        a = gate_acc[key]; n=a["n"]; attempted=a["attempted"]
        lo, hi = cluster_interval(gate_units[key])
        gate_rows.append({
            "model": a["model"], "gate": a["gate"], "n": n, "attempted": attempted,
            "attempt_rate": round(attempted/n, 6) if n else 0.0,
            "csr": round(a["strict_successes"] / n, 6) if n else 0.0,
            "csr_ci95_low": round(lo, 6), "csr_ci95_high": round(hi, 6),
            "decision_accuracy": round(a["decision_sum"] / attempted, 6) if attempted else 0.0,
            "findings_f1": round(a["findings_sum"] / attempted, 6) if attempted else 0.0,
            "actions_f1": round(a["actions_sum"] / attempted, 6) if attempted else 0.0,
            "evidence_fidelity": round(a["evidence_sum"] / attempted, 6) if attempted else 0.0,
            "authorization_accuracy": round(a["authorization_sum"] / attempted, 6) if attempted else 0.0,
        })
    by_model={r['model']:r for r in overall_rows}
    paired={r['model']:r for r in aggregate_payload.get('paired_models',[])}
    for model, account in agg_models.items():
        if model not in by_model:
            row={key:None for key in ('gate_attempt_rate','mean_score','gate_csr','gate_csr_ci95_low','gate_csr_ci95_high',
                  'route_complete_rate','route_complete_ci95_low','route_complete_ci95_high','cost_per_case_usd')}
            row.update(model=model,cases=0,completed_cases=0,agent_failures=0,gate_occurrences=0,
                       critical_misses=0,false_approvals=0,total_cost_usd=0,total_tokens=0,truncated_responses=0,model_resolution_mismatches=0)
            overall_rows.append(row); by_model[model]=row
        row=by_model[model]
        row['scoreable_cost_usd']=row['total_cost_usd']
        row['total_cost_usd']=account['total_cost_usd']
        row['planned_cases']=account.get('planned_cases',row['cases'])
        row['excluded_cases']=account.get('excluded_cases',0)
        row['availability_rate']=account.get('availability_rate',1.0)
        row['unknown_cost_calls']=account.get('unknown_cost_calls',0)
        row['unscheduled_cases']=account.get('unscheduled_cases',0)
        row.update({k:v for k,v in paired.get(model,{}).items() if k!='model'})
        for key in ('planned_gates','outcome_rate_all_planned','route_outcome_all_planned_rate','attack'):
            if key in account: row[key]=account[key]
        row['gate_interval_method']='route-stratified case-cluster bootstrap; case-count Wilson boundary fallback'
    return overall_rows, gate_rows

def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


def tex_escape(s: str) -> str:
    repl = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}
    return "".join(repl.get(ch, ch) for ch in s)


def write_paper_outputs(report_dir: Path, overall: list[dict], gates: list[dict], config: dict[str, Any]) -> None:
    write_csv(report_dir / "paper_overall.csv", overall)
    write_csv(report_dir / "paper_by_gate.csv", gates)
    (report_dir / "paper_results.json").write_text(
        json.dumps({"config": config, "overall": overall, "gates": gates}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    md = [
        "# DGF-Bench experiment results\n",
        f"Generated: {datetime.now(timezone.utc).isoformat()}\n",
        f"Scoring: {config.get('scoring_version','recorded version')}\n",
        f"Information condition: {config.get('information_condition','facts')}\n",
        "## Primary results (outcome-strict)\n",
        "Outcome-strict requires the decision, findings, actions and authorization to match; it is comparable across information conditions. "
        "The all-planned rates count every scheduled gate and route, with excluded runs as failures.\n",
        "| Model | Outcome-strict gates (95% CI) | Complete routes, outcome (95% CI) | Gates, all planned | Routes, all planned |",
        "|---|---:|---:|---:|---:|",
        *[f"| `{r['model']}` | {pct(r.get('outcome_csr'))} [{pct(r.get('outcome_csr_ci95_low'))}, {pct(r.get('outcome_csr_ci95_high'))}] | "
          f"{pct(r.get('route_outcome_rate'))} [{pct(r.get('route_outcome_ci95_low'))}, {pct(r.get('route_outcome_ci95_high'))}] | "
          f"{pct(r.get('outcome_rate_all_planned'))} | {pct(r.get('route_outcome_all_planned_rate'))} |" for r in overall],
        *(["\n## Attack outcomes\n",
           "| Model | Attacked gates | Injection observed | Attack success (95% CI) | Success given observed | Forged approvals executed |",
           "|---|---:|---:|---:|---:|---:|",
           *[f"| `{r['model']}` | {r['attack']['gates']} | {r['attack']['injection_observed']} | {pct(r.get('attack_success_rate'))} "
             f"[{pct(r.get('attack_success_ci95_low'))}, {pct(r.get('attack_success_ci95_high'))}] | "
             f"{r['attack']['attack_success_given_observed']}/{r['attack']['injection_observed']} | {r['attack']['forged_authority_executed']} |"
             for r in overall if r.get('attack')]] if any(r.get('attack') for r in overall) else []),
        "\n## Strict results including evidence\n",
        "| Model | Cases | Agent failures | Gate attempt | Gate CSR (95% CI) | Route decision complete (95% CI) | Critical misses | False approvals | Truncated | Model mismatch | Cost (USD) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in overall:
        md.append(
            f"| `{r['model']}` | {r['cases']} | {r['agent_failures']} | {pct(r['gate_attempt_rate'])} | {pct(r['gate_csr'])} "
            f"[{pct(r['gate_csr_ci95_low'])}, {pct(r['gate_csr_ci95_high'])}] | "
            f"{pct(r['route_complete_rate'])} [{pct(r['route_complete_ci95_low'])}, {pct(r['route_complete_ci95_high'])}] | "
            f"{r['critical_misses']} | {r['false_approvals']} | {r['truncated_responses']} | {r['model_resolution_mismatches']} | {r['total_cost_usd']:.4f} |"
        )
    md += ["\n## Per-gate CSR\n", "| Model | Gate | Expected | Attempted | Attempt rate | CSR (95% CI) | Decision* | Findings F1* | Actions F1* | Evidence* | Auth* |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in gates:
        md.append(
            f"| `{r['model']}` | {r['gate']} | {r['n']} | {r['attempted']} | {pct(r['attempt_rate'])} | {pct(r['csr'])} "
            f"[{pct(r['csr_ci95_low'])}, {pct(r['csr_ci95_high'])}] | {pct(r['decision_accuracy'])} | "
            f"{r['findings_f1']:.3f} | {r['actions_f1']:.3f} | {r['evidence_fidelity']:.3f} | {pct(r['authorization_accuracy'])} |"
        )
    md += [
        "\n## Interpretation note\n",
        'Gate intervals resample whole cases within routes; boundary or degenerate intervals use a descriptive Wilson fallback with the number of cases. Route intervals use case-level Wilson bounds. These descriptive intervals do not correct protocol bias.',
        'CSR scores decisions, proposed actions and lexical evidence provenance, not completed remediation. Costs include excluded runs; unknown costs are reported separately.',
        "These results measure performance on the declared synthetic DGF-Bench population. Agent-protocol failures are retained as failed decision rather than dropped; infrastructure/budget failures are excluded and reported separately. Per-gate component metrics marked * are conditional on the gate having been attempted. These results do not by themselves demonstrate autonomous real-world enterprise governance or prove the paper's universal long-horizon claim.\n",
    ]
    md += ['\n## Availability and billing\n','| Model | Planned cases | Excluded | Unscheduled | Availability | Known cost, all runs (USD) | Responses with unknown cost |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for r in overall:
        md.append(f"| {r['model']} | {r.get('planned_cases',r['cases'])} | {r.get('excluded_cases',0)} | {r.get('unscheduled_cases',0)} | {pct(r.get('availability_rate'))} | {r['total_cost_usd']:.4f} | {r.get('unknown_cost_calls',0)} |")
    md += ['\n## Matched cases across all models\n','| Model | Common cases | Gate CSR | False approvals |','|---|---:|---:|---:|']
    for r in overall:
        md.append(f"| {r['model']} | {r.get('common_cases',0)} | {pct(r.get('common_gate_csr'))} | {r.get('common_false_approvals',0)} |")
    (report_dir / "PAPER_RESULTS.md").write_text("\n".join(md), encoding="utf-8")

    tex = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{DGF-Bench review decision results. Gate CSR measures strict decision success; Route Decision Complete requires every gate to pass. Remediation execution is not assessed. Cost includes excluded runs.}",
        r"\label{tab:dgfbench-main-results}",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"Model & Cases & Gate CSR & Route Decision Complete & Crit. Misses & False Approvals & Cost (USD) \\",
        r"\midrule",
    ]
    for r in overall:
        tex.append(
            f"{tex_escape(r['model'])} & {r['cases']} & "
            f"{tex_escape(pct(r['gate_csr']))} & {tex_escape(pct(r['route_complete_rate']))} & "
            f"{r['critical_misses']} & {r['false_approvals']} & {r['total_cost_usd']:.2f} \\\\" 
        )
    tex += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    (report_dir / "paper_table_overall.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")


def git_commit() -> str | None:
    # Only a source checkout (src/dgf_bench next to pyproject.toml) has a meaningful commit;
    # an installed package may sit inside an unrelated repository.
    checkout = PACKAGE_DIR.parents[1]
    if PACKAGE_DIR.parent.name != "src" or not (checkout / "pyproject.toml").is_file():
        return None
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PACKAGE_DIR, text=True, capture_output=True)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="One-command DGF-Bench OpenRouter experiment")
    ap.add_argument("--api-key", default=None, help="OpenRouter key. If omitted, a hidden prompt is used. Passing on CLI may leave it in shell history.")
    ap.add_argument("--models", nargs="+", required=True, help="Exact OpenRouter model IDs; space- or comma-separated")
    ap.add_argument("--preset", choices=sorted(PRESETS), default="pilot", help="smoke=3 cases, pilot=15 cases, paper=150 cases")
    ap.add_argument("--cases-per-route", type=int, default=None, help="Override preset case count per Buy/Integrate/Build route")
    ap.add_argument("--difficulty", type=int, choices=range(1, 6), default=None)
    ap.add_argument("--seed", type=int, default=12000)
    ap.add_argument("--max-cost-usd", type=float, default=None, help="Global budget cap. Defaults depend on preset.")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--vision", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default=None)
    ap.add_argument("--policy-form", choices=["code", "prose"], default="code",
                    help="code=rules as the evaluator's Python (default); prose=the same rules as a written review standard")
    ap.add_argument("--no-reading-conventions", action="store_true",
                    help="Omit the record-reading conventions from the docs/attack policy (difficulty probe)")
    ap.add_argument("--provider", action="append", default=[], metavar="MODEL=PROVIDER",
                    help="Pin a model to one OpenRouter provider, without fallbacks (repeatable)")
    ap.add_argument("--handoff-mode", choices=["agent", "oracle", "none"], default="agent")
    ap.add_argument("--information-condition", choices=["facts", "docs", "attack"], default="facts",
                    help="facts=REVIEW_FACTS snapshots available; docs=facts only from documents and records; attack=docs on attack-variant dossiers (requires --dataset)")
    ap.add_argument("--schedule", choices=["round_robin", "model_major"], default="round_robin", help="Queue ordering. round_robin remains the paper-safe default.")
    ap.add_argument("--workers", type=int, default=6, help="Concurrent model×DGF jobs. Gates inside one route remain sequential. Default: 6")
    ap.add_argument("--max-workers-per-model", type=int, default=0, help="Per-model concurrency. 0=auto ceil(workers/models). Default: 0")
    ap.add_argument("--job-budget-reserve-usd", type=float, default=0.50, help="Concurrent budget reservation per in-flight model×DGF job. Default: 0.50")
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--max-tool-calls", type=int, default=40)
    ap.add_argument("--max-output-tokens", "--max-tokens", dest="max_tokens", type=int, default=8192, help="Maximum output/reasoning tokens per model turn. Default: 8192. --max-tokens remains an alias.")
    ap.add_argument("--include-full-lifecycle", action="store_true")
    ap.add_argument("--sampling-policy", choices=["natural","balanced"], default="natural",
                    help="Natural generator distribution or explicit balanced decision coverage")
    ap.add_argument("--generation-workers", type=int, default=1,
                    help="Local document generation processes, independent of model workers")
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--dataset", type=Path, default=None, help="Use an existing dataset instead of generating one")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--http-retries", type=int, default=DEFAULT_HTTP_RETRIES,
                    help="Retries per HTTP request for temporary failures; 0 disables retries. Default: 8")
    ap.add_argument("--dry-run", action="store_true", help="Prepare/validate local configuration without making model calls")
    ap.add_argument("--regenerate-smoke", action="store_true", help="Regenerate the 3-case smoke dataset instead of using the bundled one")
    ns = ap.parse_args()
    if ns.workers < 1:
        die("--workers must be >= 1")
    if ns.generation_workers < 1:
        die('--generation-workers must be >= 1')
    if ns.sampling_policy=='balanced' and ns.include_full_lifecycle and not ns.dataset:
        die('Balanced coverage currently supports Buy/Integrate/Build only')
    if ns.max_workers_per_model < 0:
        die("--max-workers-per-model must be >= 0")
    if ns.job_budget_reserve_usd < 0:
        die("--job-budget-reserve-usd must be >= 0")

    if ns.information_condition == 'attack' and ns.dataset is None:
        die('The attack condition needs an attack-variant dataset: pass --dataset')
    if ns.handoff_mode == 'oracle' and ns.information_condition != 'facts':
        die('Oracle handoffs reveal reference decisions; use them only with --information-condition facts')
    models = parse_models(ns.models)
    preset = PRESETS[ns.preset]
    cases_per_route = ns.cases_per_route if ns.cases_per_route is not None else preset["cases_per_route"]
    difficulty = ns.difficulty if ns.difficulty is not None else preset["difficulty"]
    budget = ns.max_cost_usd if ns.max_cost_usd is not None else preset["default_budget"]

    load_dotenv(Path.cwd() / ".env")
    if cases_per_route < 1 or not math.isfinite(budget) or budget <= 0:
        die("Cases and budget must be positive and finite")
    if ns.http_retries < 0:
        die('HTTP retries must be nonnegative')
    key = ns.api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key and not ns.dry_run:
        key = getpass.getpass("OpenRouter API key (hidden): ").strip()
    if not key and not ns.dry_run:
        die("No OpenRouter API key provided.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    exp_root = ns.output_dir or (Path.cwd() / "experiments" / f"run_{stamp}")
    exp_root = exp_root.resolve()
    bundled_smoke = PACKAGE_DIR / "data" / "smoke_dataset"
    # The package ships a smoke dataset for the current protocol; any other version is regenerated.
    bundled_context = next(bundled_smoke.glob('*/00_project_context.json'), None) if bundled_smoke.is_dir() else None
    bundled_current = bool(bundled_context) and json.loads(bundled_context.read_text(encoding='utf-8')).get('schema_version') == PROTOCOL_VERSION
    if ns.dataset:
        dataset_dir = ns.dataset.resolve()
    elif ns.preset == "smoke" and bundled_current and not ns.regenerate_smoke:
        dataset_dir = bundled_smoke.resolve()
    else:
        dataset_dir = exp_root / "dataset"
    results_dir = exp_root / "results"
    report_dir = exp_root / "paper_outputs"
    if exp_root.exists() and any(exp_root.iterdir()):
        die('Experiment directory already exists. Use a fresh --output-dir; resume explicitly with `dgf-bench resume` (run_openrouter_benchmark.py) and the original dataset/protocol.')
    exp_root.mkdir(parents=True, exist_ok=True); report_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    if key:
        env["OPENROUTER_API_KEY"] = key
        os.environ["OPENROUTER_API_KEY"] = key
    env.setdefault("OPENROUTER_X_TITLE", "DGF-Bench")

    config: dict[str, Any] = {
        "schema": PROTOCOL_VERSION,
        "scoring_version": SCORING_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "preset": ns.preset,
        "models": models,
        "cases_per_route": cases_per_route,
        "difficulty": difficulty,
        "seed": ns.seed,
        "max_cost_usd": budget,
        "http_retries": ns.http_retries,
        "temperature": ns.temperature,
        "vision": ns.vision,
        "reasoning_effort": ns.reasoning_effort,
        "providers": ns.provider,
        "policy_form": ns.policy_form,
        "reading_conventions": not ns.no_reading_conventions,
        "handoff_mode": ns.handoff_mode,
        "information_condition": ns.information_condition,
        "schedule": ns.schedule,
        "workers": ns.workers,
        "max_workers_per_model": ns.max_workers_per_model,
        "job_budget_reserve_usd": ns.job_budget_reserve_usd,
        "max_turns": ns.max_turns,
        "max_tool_calls": ns.max_tool_calls,
        "max_output_tokens": ns.max_tokens,
        "include_full_lifecycle": ns.include_full_lifecycle,
        "sampling_policy": ns.sampling_policy,
        "generation_workers": ns.generation_workers,
        "package_version": __version__,
        "git_commit": git_commit(),
        "python": sys.version,
        "api_key_stored": False,
    }
    (exp_root / "experiment_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    print("=" * 78)
    print("DGF-BENCH ONE-COMMAND EXPERIMENT")
    print("=" * 78)
    print(f"Models: {', '.join(models)}")
    print(f"Preset: {ns.preset} | cases/route={cases_per_route} | difficulty={difficulty}")
    print(f"Information condition: {ns.information_condition} | handoff: {ns.handoff_mode} | vision={ns.vision} | temperature={ns.temperature}")
    auto_per_model = ns.max_workers_per_model if ns.max_workers_per_model > 0 else max(1, math.ceil(ns.workers / max(1, len(models))))
    print(f"Model schedule: {ns.schedule}")
    print(f"Concurrency: workers={ns.workers} | per-model={auto_per_model} | gates within each DGF remain sequential")
    print(f"Concurrent budget reserve/job: ${ns.job_budget_reserve_usd:.2f}")
    print(f"Max output tokens/turn: {ns.max_tokens}")
    print(f"HTTP retries/request: {ns.http_retries}")
    print(f"Global cost cap: ${budget:.2f}")
    print(f"Experiment directory: {exp_root}")
    print("API key will NOT be written to disk.")

    if ns.dry_run:
        print("DRY RUN: no OpenRouter calls made.")
        return

    # Validate models before generating / spending on benchmark calls.
    print("\n[1/5] Validating OpenRouter models...", flush=True)
    validate_models(models, env, exp_root, ns.http_retries)

    if ns.preset == "smoke" and ns.dataset is None and dataset_dir == bundled_smoke.resolve() and bundled_current and not ns.regenerate_smoke:
        print(f"\n[2/5] Using bundled smoke dataset: {dataset_dir}", flush=True)
    elif ns.dataset is None:
        print(f"\n[2/5] Generating balanced dataset in: {dataset_dir}", flush=True)
        cmd = [sys.executable, "-m", "dgf_bench.prepare_openrouter_experiment",
               "--cases-per-route", str(cases_per_route),
               "--seed", str(ns.seed), "--difficulty", str(difficulty),
               "--sampling-policy", ns.sampling_policy,
               "--generation-workers", str(ns.generation_workers),
               "--output-dir", str(dataset_dir)]
        if ns.include_full_lifecycle:
            cmd.append("--include-full-lifecycle")
        run(cmd, env)
    elif not dataset_dir.exists():
        die(f"Dataset does not exist: {dataset_dir}")

    manifest = dataset_dir / "dataset_manifest.json"
    if manifest.exists():
        dataset_manifest=json.loads(manifest.read_text(encoding='utf-8'))
        if isinstance(dataset_manifest,dict):
            dataset_rows=dataset_manifest.get('cases',[])
            from collections import Counter
            route_counts=dict(Counter(row['route'] for row in dataset_rows))
            config['dataset_route_counts']=route_counts
            config['dataset_case_count']=len(dataset_rows)
            config['sampling_policy']=dataset_manifest.get('sampling_policy','legacy_unspecified')
            config['sampling_version']=dataset_manifest.get('sampling_version')
            if ns.dataset:
                config['cases_per_route']=next(iter(route_counts.values())) if len(set(route_counts.values()))==1 else None
                seeds=[row.get('seed') for row in dataset_rows]
                config['seed']=min(seeds) if seeds and all(isinstance(s,int) for s in seeds) else None
                difficulties={row.get('difficulty') for row in dataset_rows}
                config['difficulty']=next(iter(difficulties)) if len(difficulties)==1 else 'mixed'
        from dgf_bench.dataset_diversity import audit_canonical, load_canonical, write_report
        diversity=audit_canonical(load_canonical(dataset_dir))
        write_report(diversity,exp_root/'dataset_diversity_report.json')
        config['dataset_diversity_report']='dataset_diversity_report.json'
        if diversity['concentrated_gates']:
            print('[dataset] decision concentration above 80%: '+', '.join(diversity['concentrated_gates']),flush=True)
        config["dataset_manifest_sha256"] = sha256_file(manifest)
        config["dataset_path"] = str(dataset_dir)
        (exp_root / "experiment_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    print("\n[3/5] Running agent benchmark through OpenRouter...", flush=True)
    print("      This is the paid stage. Progress will be shown per model/case/gate.", flush=True)
    cmd = [sys.executable, "-m", "dgf_bench.openrouter_eval.benchmark_runner",
           "--dataset", str(dataset_dir), "--models", *models,
           "--output-dir", str(results_dir),
           "--max-cost-usd", str(budget),
           "--http-retries", str(ns.http_retries),
           "--max-turns", str(ns.max_turns),
           "--max-tool-calls", str(ns.max_tool_calls),
           "--max-tokens", str(ns.max_tokens),
           "--temperature", str(ns.temperature),
           "--vision", ns.vision,
           "--handoff-mode", ns.handoff_mode,
           "--information-condition", ns.information_condition,
           "--schedule", ns.schedule,
           "--workers", str(ns.workers),
           "--max-workers-per-model", str(ns.max_workers_per_model),
           "--job-budget-reserve-usd", str(ns.job_budget_reserve_usd)]
    if ns.reasoning_effort:
        cmd += ["--reasoning-effort", ns.reasoning_effort]
    for pinned in ns.provider:
        cmd += ["--provider", pinned]
    cmd += ["--policy-form", ns.policy_form]
    if ns.no_reading_conventions:
        cmd += ["--no-reading-conventions"]
    if ns.no_resume:
        cmd.append("--no-resume")
    run(cmd, env)

    print("\n[4/5] Aggregating benchmark results...", flush=True)
    aggregate_path = report_dir / "aggregate.json"
    run([sys.executable, "-m", "dgf_bench.openrouter_eval.aggregate", "--results", str(results_dir), "--output", str(aggregate_path)], env)
    aggregate_payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    print("\n[5/5] Creating paper-ready CSV / JSON / Markdown / LaTeX outputs...", flush=True)
    overall, gates = collect_paper_metrics(results_dir, aggregate_payload)
    write_paper_outputs(report_dir, overall, gates, config)

    print("\n" + "=" * 78)
    print("FINAL RESULTS")
    print("=" * 78)
    if not overall:
        print("No successful model/case results were produced. Check result score.json files.")
    else:
        for r in overall:
            print(
                f"{r['model']}: Gate CSR={pct(r['gate_csr'])} "
                f"Attempted={pct(r['gate_attempt_rate'])} "
                f"Route Decision Complete={pct(r['route_complete_rate'])} "
                f"Agent Failures={r['agent_failures']} "
                f"Cases={r['cases']}/{r.get('planned_cases',r['cases'])} "
                f"Excluded={r.get('excluded_cases',0)} "
                f"Critical Misses={r['critical_misses']} "
                f"False Approvals={r['false_approvals']} "
                f"Truncated={r['truncated_responses']} "
                f"ModelMismatch={r['model_resolution_mismatches']} "
                f"Cost=${r['total_cost_usd']:.4f}"
            )
    print("\nPaper-ready outputs:")
    for name in ["PAPER_RESULTS.md", "paper_overall.csv", "paper_by_gate.csv", "paper_results.json", "paper_table_overall.tex", "aggregate.json"]:
        print(f"  {report_dir / name}")
    print(f"\nRaw model traces and scores: {results_dir}")
    print(f"Experiment config: {exp_root / 'experiment_config.json'}")


if __name__ == "__main__":
    main()
