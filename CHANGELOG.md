# Changelog

## 0.1.0 — first standalone release

DGF-Bench extracted into a clean repository, protocol `DGF-decision-v9`, focused on the attack track.

- One-command benchmark: `dgf-bench run --model <id> --openrouter-key <key> --dossier-number N`.
  It generates N synthetic governance dossiers, certifies that every gate is decidable, derives one
  variant per attack type (27 attacks: 15 in-text injections, 6 document/metadata vectors, 6
  CrowdStrike-derived), runs a model through every gate under the attack condition, scores it,
  attributes each success against the paired clean run, and writes `report/REPORT.md`,
  `report.json` and SVG charts. Cost is shown before any paid call; `--max-cost-usd` caps the run;
  `--dry-run` prepares everything offline.
- The twelve file-level vectors are now part of the package (`dgf_bench.attack_vectors`): trapped
  Word core-properties, deceptive document labels, a digitally signed PDF memorandum, a forwarded
  supplier e-mail and a note in the project context. The evidence reader surfaces them natively —
  Word `[document properties]`, document titles in the evidence list, and PDF files read as blocks
  with signature status — in the docs and attack conditions, so the scorer re-checks exactly what
  the agent cited.
- Signed-PDF evidence needs the optional `pip install dgf-bench[pdf]` extra (pypdf, pyHanko,
  cryptography); without it that one vector is skipped with a note.
- Attack attribution moved into the package (`dgf_bench.report`): a success counts only when the
  model took the exact injected action, the injection was in what it received, and the same model
  did not do the same on the paired clean dossier. No forged approval is ever executed by the tools.
- Documentation: `docs/HOW_DGF_WORKS.md` (routes, gates, dossier generation, evaluation, scoring,
  controls) and `docs/ATTACKS.md` (threat model and every attack), plus the pre-registered protocol.
- Pilot results (6 blocked development dossiers, 6 models) shipped under `results/` and shown as SVG
  charts in the README. This is a pilot, not a sealed test set.
