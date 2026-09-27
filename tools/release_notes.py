"""Print the CHANGELOG.md section of one version, to use as its GitHub release notes.

    python tools/release_notes.py 0.1.1 > notes.md
    gh release create v0.1.1 --title "DGF-Bench 0.1.1" --notes-file notes.md
"""
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def section(version):
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not match:
        raise SystemExit(f"no '## {version}' section in {CHANGELOG.name}")
    return match.group(1).strip() + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.stdout.write(section(sys.argv[1]))
