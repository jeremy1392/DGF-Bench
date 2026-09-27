"""``dgf-bench run`` — the one-command attack benchmark.

Generate N synthetic governance dossiers, derive one variant per attack type from each (plus a clean
baseline), run one model through every gate under the attack condition, score it, attribute each
success against the paired clean run, and write a report.

    dgf-bench run --model z-ai/glm-5.3 --openrouter-key $KEY --dossier-number 3 [--max-cost-usd 5]

Nothing is charged until the clean generation and certification succeed and the cost line is shown;
``--dry-run`` stops there. ``--max-cost-usd`` caps the whole run across the clean baseline and all
attack variants.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

from dgf_bench.attacks import PLACEMENTS, make_attack_dataset
from dgf_bench.attack_vectors import MissingPDFSupport
from dgf_bench.certification import certify_dataset
from dgf_bench.dataset_sampling import build_plan
from dgf_bench.evaluator import evaluate_route
from dgf_bench.facts_engine import generate_canonical_case
from dgf_bench.openrouter_eval.env_loader import load_dotenv
from dgf_bench.prepare_openrouter_experiment import prepare_dataset
from dgf_bench.report import build_report
from dgf_bench.routes import build_occurrences

ROUTES = ["buy", "integrate", "build"]


def _die(message):
    print(f"dgf-bench run: {message}", file=sys.stderr)
    raise SystemExit(2)


BLOCKED = ("REWORK", "SUSPENSION", "NO_GO")


def _blocked_gates(row):
    """Specialist gates whose reference decision blocks the project, from the plan's canonical facts."""
    case = generate_canonical_case(row["seed"], row["route"], row["difficulty"], row["architecture_attempt"],
                                   row["fact_attempts"])
    return [r["gate"] for r in evaluate_route(case, build_occurrences(row["route"]))
            if r["gate"] != "general" and r["disposition"] in BLOCKED]


def _generate_clean(dataset_dir, dossier_number, seed, difficulty, routes, workers):
    """Generate exactly ``dossier_number`` clean dossiers over the chosen route(s), rotating the routes.

    The attacks try to make the agent approve a blocked gate or drop a required finding, so a dossier
    whose gates are all GO gives them nothing to aim at. As in the pilot, only dossiers with at least one
    blocked specialist gate are kept; they are chosen at planning time from the canonical facts, before
    any document is written.
    """
    per_route = math.ceil(dossier_number / len(routes))
    plan = build_plan(per_route * 3, seed, difficulty, routes, "balanced")    # a quarter aim at an all-GO dossier
    kept = {route: [row for row in plan["cases"] if row["route"] == route and _blocked_gates(row)][:per_route]
            for route in routes}
    rotation = [kept[route][i] for i in range(per_route) for route in routes if i < len(kept[route])]
    if len(rotation) < dossier_number:
        raise RuntimeError(f"only {len(rotation)} dossiers with a blocked gate in the plan; try another --seed")
    plan["cases"] = rotation[:dossier_number]
    prepare_dataset(dataset_dir, plan, workers)
    return dataset_dir


def _pdf_available():
    try:
        import pypdf  # noqa: F401
        import pyhanko  # noqa: F401
        return True
    except ImportError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dgf-bench run", description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="Exact OpenRouter model id, e.g. z-ai/glm-5.3")
    ap.add_argument("--openrouter-key", "--openrouterkey", dest="key", default=None,
                    help="OpenRouter API key. Falls back to $OPENROUTER_API_KEY or ./.env")
    ap.add_argument("--dossier-number", "--dossiers", dest="dossiers", type=int, default=3,
                    help="Number of clean dossiers to generate (default: 3)")
    ap.add_argument("--route", "--process", dest="route", choices=[*ROUTES, "all"], default="all",
                    help="Process type: buy, integrate, build, or all three in rotation (default: all)")
    ap.add_argument("--attacks", nargs="+", default=None, metavar="ATTACK",
                    help="Restrict to these attack placements (default: all 27)")
    ap.add_argument("--provider", default=None, help="Pin the model to one OpenRouter provider, no fallbacks")
    ap.add_argument("--seed", type=int, default=40000)
    ap.add_argument("--difficulty", type=int, choices=range(1, 6), default=4)
    ap.add_argument("--max-cost-usd", type=float, default=10.0, help="Total budget across all runs (default: 10)")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--max-tool-calls", type=int, default=60)
    ap.add_argument("--max-output-tokens", type=int, default=16384)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--generation-workers", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="Generate and certify locally; make no model calls")
    ns = ap.parse_args(argv)

    if ns.dossiers < 1:
        _die("--dossier-number must be >= 1")
    attacks = ns.attacks or list(PLACEMENTS)
    unknown = [a for a in attacks if a not in PLACEMENTS]
    if unknown:
        _die(f"unknown attack(s): {', '.join(unknown)}. Choose from: {', '.join(PLACEMENTS)}")
    if "signed_pdf" in attacks and not _pdf_available():
        print("note: signed_pdf needs `pip install dgf-bench[pdf]`; it will be skipped.", file=sys.stderr)
        attacks = [a for a in attacks if a != "signed_pdf"]

    load_dotenv(Path.cwd() / ".env")
    key = ns.key or os.environ.get("OPENROUTER_API_KEY")
    if not key and not ns.dry_run:
        _die("no OpenRouter key: pass --openrouter-key, set $OPENROUTER_API_KEY, or run `dgf-bench configure`")
    if key:
        os.environ["OPENROUTER_API_KEY"] = key

    run_dir = (ns.output_dir or (Path.cwd() / "runs" / f"{ns.model.replace('/', '_')}_{ns.dossiers}d")).resolve()
    if run_dir.exists() and any(run_dir.iterdir()):
        _die(f"output directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir = run_dir / "dataset"
    results_dir = run_dir / "results"

    routes = ROUTES if ns.route == "all" else [ns.route]
    print(f"[1/5] Generating {ns.dossiers} clean dossiers on route(s) {', '.join(routes)} "
          f"(seed {ns.seed}, difficulty {ns.difficulty})...", flush=True)
    _generate_clean(dataset_dir / "clean", ns.dossiers, ns.seed, ns.difficulty, routes, ns.generation_workers)

    print("[2/5] Certifying that every gate is decidable...", flush=True)
    cert = certify_dataset(dataset_dir / "clean")
    if cert["certified_occurrences"] != cert["occurrences"]:
        _die(f"certification failed: {cert['occurrences'] - cert['certified_occurrences']} undecidable gates")
    print(f"       {cert['certified_occurrences']}/{cert['occurrences']} gates certified.", flush=True)

    print(f"[3/5] Building {len(attacks)} attack variants (one attack type each)...", flush=True)
    built = []
    for placement in attacks:
        out = dataset_dir / f"attack_{placement}"
        try:
            make_attack_dataset(dataset_dir / "clean", out, templates="test", rate=1.0, placement=placement)
            built.append(placement)
        except MissingPDFSupport as exc:
            print(f"       skipped {placement}: {exc}", file=sys.stderr)
    print(f"       {len(built)} variants built.", flush=True)

    jobs = ns.dossiers * (len(built) + 1)
    print(f"\n[4/5] Model calls. 1 clean baseline + {len(built)} attacks over {ns.dossiers} dossiers "
          f"= {jobs} model x dossier runs. Budget cap ${ns.max_cost_usd:.2f}.", flush=True)
    if ns.dry_run:
        print("DRY RUN: no model calls made. Datasets and certification are ready under", dataset_dir, flush=True)
        return 0

    providers = {ns.model: ns.provider} if ns.provider else None
    _run_condition(dataset_dir / "clean", results_dir / "clean", ns, "docs", providers, ns.max_cost_usd)
    per_attack_budget = ns.max_cost_usd
    for placement in built:
        _run_condition(dataset_dir / f"attack_{placement}", results_dir / f"attack_{placement}", ns, "attack",
                       providers, per_attack_budget)

    print("\n[5/5] Building the report...", flush=True)
    data = build_report(run_dir)
    total = sum(a["attributable"] for a in data["attacks"])
    print(f"\nDone. {total} attributable attack successes across {len(data['attacks'])} attacks; "
          f"clean outcome-strict {data['outcome']['clean']['outcome_strict']}/{data['outcome']['clean']['gates']}.")
    if data["dgf_score"]:
        score = data["dgf_score"]
        print(f"DGF score: {score['score']:.1f} / 100 "
              f"(blocked {score['attacks_blocked']} of {score['attacks_applicable']} attacks)")
    print(f"Report: {run_dir / 'report' / 'REPORT.md'}")
    return 0


def _run_condition(dataset, out_dir, ns, condition, providers, budget):
    from dgf_bench.openrouter_eval.benchmark_runner import run_benchmark
    print(f"    running {condition:6s} on {dataset.name} ...", flush=True)
    run_benchmark(
        dataset, [ns.model], out_dir, None, budget, ns.max_turns, ns.max_tool_calls, ns.max_output_tokens,
        temperature=0.0, vision="auto", reasoning_effort=None, handoff_mode="agent", resume=True,
        workers=ns.workers, job_budget_reserve_usd=0.5, information_condition=condition, providers=providers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
