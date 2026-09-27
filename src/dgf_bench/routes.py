from __future__ import annotations

ROUTES = {
    "buy": {
        "label": "W1 — Buy",
        "trigger": "business_need",
        "occurrences": [
            ("procurement", "design"),
            ("legal", "design"),
            ("compliance", "design"),
            ("security", "design"),
            ("it", "build_acceptance"),
            ("general", "governance"),
        ],
    },
    "integrate": {
        "label": "W2 — Integrate",
        "trigger": "existing_system",
        "occurrences": [
            ("it", "framing"),
            ("architecture", "design"),
            ("security", "design"),
            ("legal", "design"),
            ("compliance", "design"),
            ("general", "governance"),
        ],
    },
    "build": {
        "label": "W3 — Build",
        "trigger": "new_project",
        "occurrences": [
            ("it", "opportunity"),
            ("architecture", "design"),
            ("security", "design"),
            ("tech_readiness", "build_acceptance"),
            ("general", "governance"),
        ],
    },
    "full_lifecycle": {
        "label": "Full five-phase lifecycle",
        "trigger": "enterprise_change",
        "occurrences": [
            ("it", "opportunity"),
            ("compliance", "opportunity"),
            ("general", "opportunity"),
            ("architecture", "framing"),
            ("security", "framing"),
            ("tech_readiness", "framing"),
            ("procurement", "framing"),
            ("compliance", "framing"),
            ("general", "framing"),
            ("it", "design"),
            ("architecture", "design"),
            ("security", "design"),
            ("procurement", "design"),
            ("legal", "design"),
            ("compliance", "design"),
            ("general", "design"),
            ("tech_readiness", "build_acceptance"),
            ("security", "build_acceptance"),
            ("it", "build_acceptance"),
            ("compliance", "build_acceptance"),
            ("general", "build_acceptance"),
            ("it", "deployment_closure"),
            ("procurement", "deployment_closure"),
            ("legal", "deployment_closure"),
            ("compliance", "deployment_closure"),
            ("general", "deployment_closure"),
        ],
    },
}

PHASE_ORDER = ["opportunity", "framing", "design", "build_acceptance", "deployment_closure", "governance"]

GATE_LABELS = {
    "general": "General Gate",
    "it": "IT Gate",
    "architecture": "Architecture Gate",
    "security": "Security Architecture Gate",
    "tech_readiness": "Tech Readiness Gate",
    "procurement": "Procurement Gate",
    "legal": "Legal Gate",
    "compliance": "Compliance Gate",
}

def build_occurrences(route_key: str):
    route = ROUTES[route_key]
    out=[]
    counts={}
    for idx,(gate,phase) in enumerate(route["occurrences"], start=1):
        counts[gate]=counts.get(gate,0)+1
        out.append({
            "occurrence_id": f"{route_key.upper()}-{idx:02d}-{gate.upper()}-{counts[gate]}",
            "gate": gate,
            "gate_label": GATE_LABELS[gate],
            "phase": phase,
            "position": idx,
        })
    return out
