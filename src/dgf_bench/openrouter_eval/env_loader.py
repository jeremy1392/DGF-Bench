from __future__ import annotations
import os
from pathlib import Path


def load_dotenv(path: Path | str = ".env") -> None:
    """Tiny .env reader so the harness has no dotenv dependency."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def require_api_key() -> str:
    load_dotenv()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or "REPLACE_ME" in key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your OpenRouter key, "
            "or export OPENROUTER_API_KEY in the shell."
        )
    return key
