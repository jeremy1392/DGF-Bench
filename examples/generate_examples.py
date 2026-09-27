"""Regenerate the committed example dossiers.

One clean dossier for each of the three process types (Buy, Integrate, Build) — together they
cover all eight gates — and a sample of attack variants of the Build dossier, so the trapped
documents (Word metadata, a signed PDF, a "DO NOT OPEN" title, a forged register row, fake
control tokens, a fake review procedure) can be opened directly, without an OpenRouter key.

Deterministic: the same seed always produces byte-identical files.

    python examples/generate_examples.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

from dgf_bench.attacks import make_attack_variant
from dgf_bench.generate_dgfbench_v6 import build_case

HERE = Path(__file__).resolve().parent
DIFFICULTY = 4
# Seeds chosen so each route's clean dossier has blocked gates for the attacks to target.
ROUTES = {"buy": 40100, "integrate": 40101, "build": 40102}
# One representative variant per attack surface (see docs/ATTACKS.md for all 27), built on Build.
SAMPLE = ["fake_procedure", "docx_metadata", "signed_pdf", "docx_label_noopen", "forged_row", "cs_special_tokens"]


def main():
    for child in HERE.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
    clean = {}
    for route, seed in ROUTES.items():
        clean[route] = build_case(HERE / "clean" / route, seed, DIFFICULTY, route)
        print(f"clean {route}: {clean[route].relative_to(HERE)}")
    for placement in SAMPLE:
        try:
            case = make_attack_variant(clean["build"], HERE / f"attack_{placement}", templates="test", rate=1.0, placement=placement)
        except Exception as exc:                      # e.g. signed_pdf without the [pdf] extra
            print(f"skipped {placement}: {type(exc).__name__}: {exc}")
            continue
        print(f"attack_{placement}: {case.relative_to(HERE)}")


if __name__ == "__main__":
    main()
