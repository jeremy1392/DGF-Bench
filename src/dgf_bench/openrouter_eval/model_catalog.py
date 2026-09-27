from __future__ import annotations
import argparse, json, re
from pathlib import Path
from typing import Any
from .openrouter_client import OpenRouterClient


def supports(model: dict[str, Any], parameter: str) -> bool:
    return parameter in set(model.get("supported_parameters") or [])


def input_modalities(model: dict[str, Any]) -> set[str]:
    arch = model.get("architecture") or {}
    vals = arch.get("input_modalities") or []
    if isinstance(vals, str):
        vals = [vals]
    return set(vals)


def price_per_million(model: dict[str, Any], key: str) -> float | None:
    raw = (model.get("pricing") or {}).get(key)
    if raw in (None, ""):
        return None
    try:
        # OpenRouter catalog prices are normally per token.
        return float(raw) * 1_000_000
    except Exception:
        return None


def compact(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": model.get("id"),
        "name": model.get("name"),
        "context_length": model.get("context_length"),
        "supported_parameters": model.get("supported_parameters") or [],
        "input_modalities": sorted(input_modalities(model)),
        "prompt_usd_per_million": price_per_million(model, "prompt"),
        "completion_usd_per_million": price_per_million(model, "completion"),
        "created": model.get("created"),
    }


def discover(client: OpenRouterClient, tools_only=True, provider=None, search=None, min_context=0):
    models = client.list_models()
    out = []
    rx = re.compile(search, re.I) if search else None
    for m in models:
        mid = str(m.get("id") or "")
        if provider and not mid.startswith(provider.rstrip("/") + "/"):
            continue
        if tools_only and not supports(m, "tools"):
            continue
        if int(m.get("context_length") or 0) < min_context:
            continue
        if rx and not rx.search(mid + " " + str(m.get("name") or "")):
            continue
        out.append(compact(m))
    out.sort(key=lambda x: ((x.get("created") or 0), x.get("id") or ""), reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser(description="Discover OpenRouter models suitable for DGF-Bench agents")
    ap.add_argument("--all", action="store_true", help="Include models without tool support")
    ap.add_argument("--provider", default=None, help="Provider prefix, e.g. openai, anthropic, z-ai")
    ap.add_argument("--search", default=None, help="Regex over model id/name")
    ap.add_argument("--min-context", type=int, default=65536)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--json", type=Path, default=None)
    ns = ap.parse_args()
    client = OpenRouterClient()
    rows = discover(client, tools_only=not ns.all, provider=ns.provider, search=ns.search, min_context=ns.min_context)
    rows = rows[:ns.limit]
    if ns.json:
        ns.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"{'MODEL':70} {'CTX':>9} {'TOOLS':>5} {'VISION':>6} {'$/M IN':>10} {'$/M OUT':>10}")
    for r in rows:
        print(f"{(r['id'] or '')[:70]:70} {int(r.get('context_length') or 0):9d} {'yes' if 'tools' in r['supported_parameters'] else 'no':>5} {'yes' if 'image' in r['input_modalities'] else 'no':>6} {str(r.get('prompt_usd_per_million'))[:10]:>10} {str(r.get('completion_usd_per_million'))[:10]:>10}")

if __name__ == "__main__":
    main()
