"""Command-line interface: `dgf-bench <command> [options]`.

Each command delegates to the `main()` of one module, which parses its own options.
The one-command benchmark is `run`; the other commands expose its building blocks.
"""
from __future__ import annotations

import importlib
import sys

from dgf_bench import __version__

COMMANDS = {
    "run": ("dgf_bench.run_attack_benchmark",
            "Generate dossiers, derive one variant per attack, evaluate a model and write a report (paid model calls)"),
    "doctor": ("dgf_bench.doctor", "Check that this installation can generate, run and score DGF-Bench"),
    "selftest": ("dgf_bench.selftest", "Run offline self-tests (no model calls)"),
    "configure": ("dgf_bench.configure_openrouter", "Store an OpenRouter API key in ./.env"),
    "models": ("dgf_bench.openrouter_eval.model_catalog", "List OpenRouter models that support tool calling"),
    "generate": ("dgf_bench.prepare_openrouter_experiment", "Generate a dataset of synthetic dossiers"),
    "validate": ("dgf_bench.validate_case", "Validate one generated dossier"),
    "verify": ("dgf_bench.verify_dataset", "Verify a dataset offline against the public-observation baseline"),
    "certify": ("dgf_bench.certification", "Check that every gate is decidable from authoritative public sources"),
    "attack": ("dgf_bench.attacks", "Build the attack variants of a clean dataset (one attack type per variant)"),
    "experiment": ("dgf_bench.run_full_experiment", "Full experiment on one information condition (facts, docs or attack)"),
    "resume": ("dgf_bench.openrouter_eval.benchmark_runner", "Run or resume the benchmark on an existing dataset"),
    "aggregate": ("dgf_bench.openrouter_eval.aggregate", "Aggregate the scores of a results directory"),
    "score": ("dgf_bench.score_submission", "Score one submission against a dossier"),
    "rescore": ("dgf_bench.rescore_experiment", "Rescore a recorded experiment offline into a new directory"),
    "compare": ("dgf_bench.compare_conditions", "Paired comparison of two information conditions on the same dossiers"),
    "tool": ("dgf_bench.synthetic_environment", "Call one synthetic enterprise tool for a dossier"),
    "serve": ("dgf_bench.serve_environment", "Serve the synthetic enterprise tools over HTTP"),
    "icons": ("dgf_bench.install_azure_icons", "Install another Microsoft Azure icon pack"),
}


def usage() -> str:
    width = max(map(len, COMMANDS))
    lines = [f"DGF-Bench {__version__}", "", "usage: dgf-bench <command> [options]", "", "commands:"]
    lines += [f"  {name:<{width}}  {help_text}" for name, (_, help_text) in COMMANDS.items()]
    lines += ["", "Run `dgf-bench <command> --help` for the options of a command."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"dgf-bench {__version__}")
        return 0
    name, rest = argv[0], argv[1:]
    if name not in COMMANDS:
        print(f"dgf-bench: unknown command '{name}'\n\n{usage()}", file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[name][0])
    sys.argv = [f"dgf-bench {name}", *rest]
    result = module.main()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
