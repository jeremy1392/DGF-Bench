from __future__ import annotations
import ast
from pathlib import Path
from typing import Any


def _literal(node):
    if isinstance(node, ast.Constant):
        return node.value
    return None


def build_catalog(evaluator_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Extract possible finding IDs/actions from evaluator source without exposing which apply."""
    src = evaluator_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    by_gate: dict[str, list[dict[str, Any]]] = {}
    current_gate = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("evaluate_"):
            pass
    # Walk per function so the gate name is known.
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("evaluate_")]:
        gate = fn.name.removeprefix("evaluate_")
        gate = {"tech":"tech_readiness"}.get(gate, gate)
        if gate == "gate" or gate == "route":
            continue
        items=[]
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "finding" and len(n.args) >= 5:
                fid=_literal(n.args[0]); sev=_literal(n.args[1]); msg=_literal(n.args[2]); action=_literal(n.args[3]); owner=_literal(n.args[4])
                if fid:
                    items.append({"id":fid,"severity":sev,"description":msg or ast.unparse(n.args[2]),"action":action,"owner":owner,
                                  "disposition":_literal(n.args[6]) if len(n.args)>6 else 'REWORK',
                                  "risk_acceptance_allowed":_literal(n.args[7]) if len(n.args)>7 else False})
        if items:
            by_gate[gate]=items
    return by_gate
