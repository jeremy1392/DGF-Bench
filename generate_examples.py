"""Regenerate the example dossiers: DGF-Clean (clean) and DGF-Attack (all 27 attacks).

- ``DGF-Clean/`` — one clean dossier per process type (buy, integrate, build). Together they cover
  every reviewed gate; each dossier also carries evidence files for all eight gate domains.
- ``DGF-Attack/`` — one attacked dossier per attack type (the 27 placements of ``dgf-bench attack``),
  each a copy of the clean Build dossier with exactly that one attack injected.

Deterministic: the same seeds always produce byte-identical files. Run from the repo root:

    python generate_examples.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from dgf_bench.attacks import PLACEMENTS, make_attack_variant
from dgf_bench.generate_dgfbench_v6 import build_case

ROOT = Path(__file__).resolve().parent
CLEAN, ATTACK = ROOT / "DGF-Clean", ROOT / "DGF-Attack"
DIFFICULTY = 4
ROUTES = {"buy": 40100, "integrate": 40101, "build": 40102}


def main():
    for folder in (CLEAN, ATTACK):
        if folder.exists():
            shutil.rmtree(folder)
    clean = {}
    for route, seed in ROUTES.items():
        clean[route] = build_case(CLEAN / route, seed, DIFFICULTY, route)
        print(f"DGF-Clean/{route}: {clean[route].name}")
    rows = []
    for placement in PLACEMENTS:
        try:
            case = make_attack_variant(clean["build"], ATTACK / placement, templates="test", rate=1.0, placement=placement)
        except Exception as exc:                      # e.g. signed_pdf without the [pdf] extra
            print(f"skipped {placement}: {type(exc).__name__}: {exc}")
            continue
        attacked = [m for m in json.loads((case / "99_hidden_ground_truth.json").read_text(encoding="utf-8"))["attack_manifest"]
                    if not m.get("placebo")]
        first = attacked[0] if attacked else {}
        rows.append((placement, len(attacked), first.get("gate", "-"), first.get("path", "-")))
        print(f"DGF-Attack/{placement}: {len(attacked)} attacked gate(s)")
    _write_readmes(rows)


def _write_readmes(rows):
    (CLEAN / "README.md").write_text(
        "# DGF-Clean — example clean dossiers\n\n"
        "One clean dossier per process type. Each holds the full evidence of a governance review: the\n"
        "public files `00_`–`06_`, `shared/`, `gate_evidence/<gate>/` for all eight gate domains (Word,\n"
        "CSV, JSON, YAML, and the Azure architecture diagram as SVG + PNG), `phase_history/`, and the\n"
        "evaluator-only `99_hidden_ground_truth.json` (canonical truth and reference decisions).\n\n"
        "| Process type | Dossier | Reviewed gates |\n|---|---|---|\n"
        "| buy | `buy/` | procurement, legal, compliance, security, it, general |\n"
        "| integrate | `integrate/` | it, architecture, security, legal, compliance, general |\n"
        "| build | `build/` | it, architecture, security, tech_readiness, general |\n\n"
        "Open `build/DGF-BLD-*/gate_evidence/architecture/Architecture_Diagram_Detailed.png` for the\n"
        "generated diagram, and any `gate_evidence/<gate>/*.docx|csv|json` for the evidence a gate reads.\n\n"
        "Regenerate byte-for-byte: `python generate_examples.py`.\n", encoding="utf-8", newline="\n")

    lines = ["# DGF-Attack — one dossier per attack type", "",
             "Each folder is a copy of the clean Build dossier (`DGF-Clean/build/`) with exactly **one**",
             "attack injected. The reference decisions do not change — the attack tries to move the agent",
             "away from the correct decision. Every folder's `99_hidden_ground_truth.json` lists the full",
             "injection under `attack_manifest` (file, gate, objective, canary token).", "",
             "| Attack | Attacked gates | First injected file |", "|---|---:|---|"]
    for placement, n, _gate, path in rows:
        lines.append(f"| `{placement}` | {n} | `{path}` |")
    lines += ["", "See `docs/ATTACKS.md` for what each attack does, and `docs/HOW_DGF_WORKS.md` for the gates and",
              "the dossier layout. Regenerate byte-for-byte: `python generate_examples.py`.", ""]
    (ATTACK / "README.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
