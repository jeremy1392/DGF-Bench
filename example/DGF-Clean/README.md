# DGF-Clean — example clean dossiers

One clean dossier per process type. Each holds the full evidence of a governance review: the
public files `00_`–`06_`, `shared/`, `gate_evidence/<gate>/` for all eight gate domains (Word,
CSV, JSON, YAML, and the Azure architecture diagram as SVG + PNG), `phase_history/`, and the
evaluator-only `99_hidden_ground_truth.json` (canonical truth and reference decisions).

| Process type | Dossier | Reviewed gates |
|---|---|---|
| buy | `buy/` | procurement, legal, compliance, security, it, general |
| integrate | `integrate/` | it, architecture, security, legal, compliance, general |
| build | `build/` | it, architecture, security, tech_readiness, general |

Open `build/DGF-BLD-*/gate_evidence/architecture/Architecture_Diagram_Detailed.png` for the
generated diagram, and any `gate_evidence/<gate>/*.docx|csv|json` for the evidence a gate reads.

Regenerate byte-for-byte: `python generate_examples.py`.
