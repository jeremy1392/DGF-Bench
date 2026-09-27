#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List

from dgf_bench.azure_icon_registry import AzureIconRegistry

AZURE_REGIONS = [
    "UAE North", "UAE Central", "West Europe", "North Europe", "UK South",
    "France Central", "Germany West Central", "East US", "East US 2",
    "West US 2", "Southeast Asia", "Australia East",
]

EDGE_PATTERNS = {
    "frontdoor_waf_cdn": ["Azure Front Door Premium", "WAF + CDN"],
    "cdn_then_waf": ["Azure CDN", "Application Gateway WAF"],
    "waf_then_cdn": ["Application Gateway WAF", "Azure CDN"],
    "waf_only": ["Application Gateway WAF"],
    "frontdoor_only": ["Azure Front Door"],
    "private_only": ["Private Access Only"],
}

COMPUTE_PROFILES = {
    "vm_single": ["Azure VM"],
    "vm_ha": ["Azure VM AZ1", "Azure VM AZ2"],
    "vmss": ["VM Scale Set AZ1", "VM Scale Set AZ2", "VM Scale Set AZ3"],
    "dedicated_host": ["Azure Dedicated Host", "VM Group A", "VM Group B"],
    "aks": ["AKS System Pool", "AKS App Pool", "Ingress Controller", "Worker Pods"],
    "container_apps": ["Azure Container Apps Environment", "Frontend Container", "API Container", "Worker Container"],
    "app_service": ["Azure App Service", "Deployment Slot"],
    "functions": ["Azure Functions", "Function App Workers"],
    "mixed": ["Azure App Service", "AKS App Pool", "Azure Functions", "Batch VM"],
}

DATA_PROFILES = {
    "transactional": ["Azure SQL Database", "Azure Storage Account", "Azure Cache for Redis"],
    "enterprise_sql": ["Azure SQL Managed Instance", "Azure Storage Account", "Azure Key Vault"],
    "postgres": ["Azure Database for PostgreSQL", "Azure Storage Account", "Azure Cache for Redis"],
    "event_driven": ["Azure Cosmos DB", "Azure Service Bus", "Azure Event Hubs", "Azure Storage Account"],
    "analytics": ["Azure Data Lake Storage", "Azure Synapse / Fabric Endpoint", "Azure SQL Database"],
    "ai": ["Azure AI Search", "Azure Storage Account", "Azure Cosmos DB", "Azure Key Vault"],
}

SOLUTION_PLATFORM_SERVICES = {
    "web_platform": [],
    "api_platform": ["Azure API Management"],
    "enterprise_backoffice": [],
    "data_platform": ["Azure Data Factory"],
    "agentic_ai": ["Azure AI Foundry", "Azure OpenAI Service"],
    "integration_platform": ["Azure Service Bus", "Azure Event Hubs"],
}

RESILIENCE_PROFILES = [
    "single_region_single_az",
    "single_region_multi_az",
    "multi_region_active_passive",
    "multi_region_active_active",
    "backup_only",
]


def weighted(rng, items):
    vals, weights = zip(*items)
    return rng.choices(vals, weights=weights, k=1)[0]


def b(rng, p=.5):
    return rng.random() < p


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def architecture_signature(profile: Dict[str, Any]) -> str:
    """Structural/visual signature, deliberately excluding case-specific names.

    The balanced dataset generator rejects duplicate signatures so two DGF cases
    never receive the same overall architecture configuration just because the RNG
    happened to collide.
    """
    keys = [
        "solution_type", "edge_pattern", "compute_profile", "data_profile", "network_profile",
        "resilience_profile", "primary_region", "secondary_region", "multi_az", "internet_facing",
        "private_endpoints", "azure_firewall", "bastion", "ddos_protection", "expressroute", "vpn",
        "nat_gateway", "api_management", "messaging", "sentinel", "logs_to_siem", "backup_enabled",
        "azure_site_recovery", "data_replication",
    ]
    payload = {k: profile.get(k) for k in keys}
    payload["platform_services"] = list(profile.get("platform_services") or [])
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def generate_architecture_profile(seed: int, project: Dict[str, Any], difficulty: int = 3,
                                  inconsistency_rate: float = .12) -> Dict[str, Any]:
    rng = random.Random(seed)
    solution_type = weighted(rng, [
        ("web_platform", 3), ("api_platform", 3), ("enterprise_backoffice", 2),
        ("data_platform", 2), ("agentic_ai", 2), ("integration_platform", 2),
    ])

    # Keep the architecture plausible while retaining enough cross-case diversity.
    edge_weights = {
        "enterprise_backoffice": [("private_only", 6), ("waf_only", 2), ("frontdoor_waf_cdn", 1)],
        "data_platform": [("private_only", 4), ("frontdoor_waf_cdn", 2), ("waf_only", 2), ("frontdoor_only", 1)],
        "web_platform": [("frontdoor_waf_cdn", 6), ("cdn_then_waf", 2), ("waf_only", 2), ("frontdoor_only", 1)],
    }.get(solution_type, [
        ("frontdoor_waf_cdn", 4), ("cdn_then_waf", 2), ("waf_then_cdn", 1),
        ("waf_only", 2), ("frontdoor_only", 2), ("private_only", 1),
    ])
    edge = weighted(rng, edge_weights)

    compute = weighted(rng, [
        ("vm_single", 1), ("vm_ha", 2), ("vmss", 3), ("dedicated_host", 1),
        ("aks", 4), ("container_apps", 4), ("app_service", 3), ("functions", 2), ("mixed", 3),
    ])
    data = weighted(rng, [
        ("transactional", 4), ("enterprise_sql", 2), ("postgres", 2),
        ("event_driven", 3), ("analytics", 2), ("ai", 2),
    ])
    net = weighted(rng, [("hub_spoke", 5), ("single_vnet", 3), ("virtual_wan", 3)])
    resilience = weighted(rng, [
        ("single_region_single_az", 1 + difficulty),
        ("single_region_multi_az", 5),
        ("multi_region_active_passive", 4),
        ("multi_region_active_active", 2),
        ("backup_only", 1 + difficulty // 2),
    ])

    primary = project.get("primary_region")
    if primary not in AZURE_REGIONS:
        primary = rng.choice(AZURE_REGIONS)
    secondary = rng.choice([x for x in AZURE_REGIONS if x != primary])
    multi_region = resilience.startswith("multi_region")
    multi_az = resilience in ("single_region_multi_az", "multi_region_active_passive", "multi_region_active_active")
    dr_mode = {
        "single_region_single_az": "No dedicated DR",
        "single_region_multi_az": "Zone-resilient / same-region",
        "multi_region_active_passive": "Warm standby",
        "multi_region_active_active": "Active-active",
        "backup_only": "Backup restore only",
    }[resilience]

    internet_facing = edge != "private_only"
    private_endpoints = b(rng, .75 if difficulty <= 3 else .55)
    firewall = b(rng, .78)
    bastion = b(rng, .65)
    ddos = b(rng, .55 if internet_facing else .25)
    expressroute = b(rng, .45)
    vpn = b(rng, .40)
    nat = b(rng, .35)
    apim = solution_type in ("api_platform", "integration_platform", "agentic_ai") or b(rng, .35)
    messaging = solution_type in ("api_platform", "integration_platform", "agentic_ai") or b(rng, .45)
    managed_identity = b(rng, .80)
    pim = b(rng, .60)
    ca = b(rng, .78)
    sentinel = b(rng, .80)
    defender = b(rng, .78)
    appinsights = b(rng, .75)
    logs_to_siem = sentinel and b(rng, .88)
    backup_enabled = b(rng, .90)
    restore_tested = b(rng, .70)
    dr_tested = multi_region and b(rng, .62)
    asr = compute in ("vm_single", "vm_ha", "vmss", "dedicated_host", "mixed") and multi_region and b(rng, .75)

    if multi_region:
        data_replication = rng.choice(["Geo-replication", "GRS/RA-GRS", "Cross-region replica", "Active geo-replication"])
    elif multi_az:
        data_replication = rng.choice(["Zone redundant", "ZRS", "Synchronous zone replica"])
    else:
        data_replication = rng.choice(["LRS only", "Single-zone replica", "None"])

    rto = rng.choice([.25, .5, 1, 2, 4, 8, 24])
    rpo = rng.choice([0, 5, 15, 30, 60, 240, 1440])
    if resilience in ("single_region_single_az", "backup_only"):
        rto = max(rto, rng.choice([4, 8, 24]))
        rpo = max(rpo, rng.choice([60, 240, 1440]))

    project_code = project.get("project_code") or str(project.get("project_id", "dgf"))[-6:]
    resource_prefix = _slug(f"{project_code}-{seed % 100000:05d}")[:22]
    architecture_id = f"ARCH-{project_code}-{seed % 100000:05d}"
    platform_services = list(SOLUTION_PLATFORM_SERVICES.get(solution_type, []))
    if apim and "Azure API Management" not in platform_services:
        platform_services.insert(0, "Azure API Management")
    if messaging:
        for svc in ("Azure Service Bus", "Azure Event Hubs"):
            if svc not in platform_services:
                platform_services.append(svc)

    defects = []
    candidates = [
        ("public_database", "Data service exposes a public endpoint despite private-network design."),
        ("shared_service_principal", "A shared service principal is used across environments."),
        ("no_waf", "Internet-facing application has no effective WAF policy."),
        ("single_point_compute", "Only one compute instance exists in production."),
        ("backup_not_restored", "Backups exist but no successful restore test is available."),
        ("no_siem_export", "Application logs are retained locally and not exported to SIEM."),
        ("dr_not_tested", "Secondary-region failover has not been tested."),
        ("region_mismatch", "HLD names a DR region that differs from the recovery plan."),
        ("overprivileged_identity", "Workload identity has broader Graph / Azure permissions than required."),
        ("missing_private_endpoint", "A data service expected to be private is reachable through a public endpoint."),
        ("stale_diagram", "Architecture diagram version is older than the security review package."),
    ]
    p = min(.08 + .05 * difficulty, .45)
    seen = set()

    def add_defect(k, desc, severity=None):
        if k not in seen:
            defects.append({"id": k, "description": desc, "severity": severity or rng.choice(["medium", "high", "critical"])})
            seen.add(k)

    # Defects are derived from the actual topology, never sampled independently.
    derived = {
        'public_database': not private_endpoints,
        'missing_private_endpoint': not private_endpoints,
        'no_waf': internet_facing and edge == 'frontdoor_only',
        'single_point_compute': compute == 'vm_single',
        'backup_not_restored': backup_enabled and not restore_tested,
        'no_siem_export': not logs_to_siem,
        'dr_not_tested': multi_region and not dr_tested,
    }
    for k, desc in candidates:
        if derived.get(k, False):
            add_defect(k, desc, 'high')
    if internet_facing and edge == "frontdoor_only":
        add_defect("edge_control_gap", "Internet entry path has incomplete WAF protection.", "high")
    if compute == "vm_single":
        add_defect("single_point_compute", "Single VM is a production compute single point of failure.", "high")
    if backup_enabled and not restore_tested:
        add_defect("backup_not_restored", "Backup policy exists but restore evidence is missing.", "high")
    if multi_region and not dr_tested:
        add_defect("dr_not_tested", "Cross-region DR architecture exists but failover test evidence is absent.", "high")

    inconsistencies = []
    for d in defects:
        if rng.random() < inconsistency_rate:
            inconsistencies.append({
                "type": "cross_document_conflict",
                "fact": d["id"],
                "diagram_claim": "appears compliant / present",
                "authoritative_truth": "non-compliant / incomplete",
                "source_of_truth": rng.choice(["Azure Resource Graph", "SIEM", "IAM export", "Backup report", "Recovery test report"]),
            })

    profile = {
        "seed": seed,
        "case_seed": int(project.get("generation_seed", seed)),
        "architecture_id": architecture_id,
        "resource_prefix": resource_prefix,
        "solution_type": solution_type,
        "edge_pattern": edge,
        "edge_services": EDGE_PATTERNS[edge],
        "compute_profile": compute,
        "compute_services": COMPUTE_PROFILES[compute],
        "data_profile": data,
        "data_services": DATA_PROFILES[data],
        "platform_services": platform_services,
        "network_profile": net,
        "resilience_profile": resilience,
        "primary_region": primary,
        "secondary_region": secondary if multi_region else None,
        "multi_region": multi_region,
        "multi_az": multi_az,
        "availability_zones": [1, 2, 3] if multi_az else [1],
        "dr_mode": dr_mode,
        "rto_hours": rto,
        "rpo_minutes": rpo,
        "internet_facing": internet_facing,
        "private_endpoints": private_endpoints,
        "azure_firewall": firewall,
        "bastion": bastion,
        "ddos_protection": ddos,
        "expressroute": expressroute,
        "vpn": vpn,
        "nat_gateway": nat,
        "api_management": apim,
        "messaging": messaging,
        "entra_id": True,
        "managed_identity": managed_identity,
        "pim": pim,
        "conditional_access": ca,
        "key_vault": True,
        "sentinel": sentinel,
        "defender_for_cloud": defender,
        "application_insights": appinsights,
        "logs_to_siem": logs_to_siem,
        "backup_enabled": backup_enabled,
        "restore_tested": restore_tested,
        "dr_tested": dr_tested,
        "azure_site_recovery": asr,
        "data_replication": data_replication,
        "defects": defects,
        "inconsistencies": inconsistencies,
        "version": f"{rng.randint(1, 4)}.{rng.randint(0, 9)}",
        "diagram_age_days": rng.randint(0, 120),
    }
    profile["architecture_signature"] = architecture_signature(profile)
    return profile


def architecture_flows(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    flows = []

    def add(src, dst, proto, purpose, trust="internal"):
        flows.append({"source": src, "destination": dst, "protocol": proto, "purpose": purpose, "trust": trust})

    if profile["internet_facing"]:
        add("Internet Users", profile["edge_services"][0], "HTTPS 443", "User / API traffic", "external")
        for a, c in zip(profile["edge_services"], profile["edge_services"][1:]):
            add(a, c, "HTTPS 443", "Edge forwarding", "external")
        ingress = profile["edge_services"][-1]
    else:
        ingress = "Corporate Network"

    if profile["network_profile"] == "hub_spoke":
        if profile["azure_firewall"]:
            add(ingress, "Azure Firewall", "HTTPS / routed", "Controlled ingress / routing")
            ingress = "Azure Firewall"
        if profile["expressroute"] or profile["vpn"]:
            add("Corporate Network", "Hub VNet", "ExpressRoute/VPN", "Private hybrid connectivity")
    elif profile["network_profile"] == "virtual_wan":
        add("Corporate Network", "Azure Virtual WAN Hub", "ExpressRoute/VPN/SD-WAN", "Branch and hybrid transit")
        add("Azure Virtual WAN Hub", "Workload Spoke", "VNet connection", "Transit to application")
        add("Azure Virtual WAN Hub", "Data Spoke", "VNet connection", "Transit to data")
    else:
        add("Corporate Network", "Application VNet", "Private routing", "Single-VNet access")

    if profile["api_management"]:
        add(ingress, "Azure API Management", "HTTPS 443", "API gateway / policy enforcement")
        ingress = "Azure API Management"

    first_compute = profile["compute_services"][0]
    add(ingress, first_compute, "HTTPS 443", "Application request")
    for c in profile["compute_services"][1:]:
        add(first_compute, c, "Internal TCP/HTTPS", "Workload communication")

    for svc in profile.get("platform_services", []):
        if svc not in {"Azure API Management"}:
            add(first_compute, svc, "TLS", "Platform service interaction")

    for d in profile["data_services"]:
        add(first_compute, d, "Private Link / TLS" if profile["private_endpoints"] else "TLS / public service endpoint", "Application data")

    add(first_compute, "Azure Key Vault", "HTTPS 443", "Secrets / keys")
    if profile["sentinel"]:
        add(first_compute, "Log Analytics / Sentinel", "HTTPS 443", "Logs and detections")
    if profile["multi_region"]:
        add("Primary Region", "Secondary Region", "Replication", "DR replication / failover")
    return flows


_ICON_REGISTRY = None
_ICON_HITS = 0
_ICON_MISSES = 0

COLORS = {
    "edge": "#E9F3FF", "compute": "#E9F8F1", "data": "#FFF0F3", "security": "#EEF3FF",
    "network": "#E7FAFA", "dr": "#F5EDFF", "external": "#F4F4F4", "identity": "#F2ECFF",
    "azure": "#0078D4", "text": "#17365D", "platform": "#FFF8E7",
}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rect(x, y, w, h, fill, stroke="#7FA9D8", rx=10, sw=2):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def text(x, y, s, size=16, weight="normal", anchor="start", fill="#17365D"):
    return f'<text x="{x}" y="{y}" font-family="Arial,Segoe UI,sans-serif" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{fill}">{esc(s)}</text>'


def badge(x, y, n, color="#2F5597"):
    return f'<circle cx="{x}" cy="{y}" r="12" fill="#FFFFFF" stroke="{color}" stroke-width="3"/><text x="{x}" y="{y+4}" font-family="Arial,Segoe UI,sans-serif" font-size="11" font-weight="bold" text-anchor="middle" fill="{color}">{n}</text>'


def service(x, y, w, h, name, category="compute", subtitle=None):
    global _ICON_HITS, _ICON_MISSES
    out = rect(x, y, w, h, COLORS.get(category, "#FFF"))
    uri = _ICON_REGISTRY.data_uri(name) if _ICON_REGISTRY and _ICON_REGISTRY.available else None
    if uri:
        _ICON_HITS += 1
        size = min(34, h - 12)
        out += f'<image x="{x+10}" y="{y+(h-size)/2}" width="{size}" height="{size}" preserveAspectRatio="xMidYMid meet" href="{uri}"/>'
    else:
        _ICON_MISSES += 1
        out += f'<circle cx="{x+24}" cy="{y+22}" r="12" fill="#0078D4"/>'
        out += text(x + 24, y + 27, "Az", 9, "bold", "middle", "white")
    label = name if len(name) <= 31 else name[:30] + "…"
    out += text(x + w / 2 + 10, y + 27, label, 12, "bold", "middle")
    if subtitle:
        sub = subtitle if len(str(subtitle)) <= 38 else str(subtitle)[:37] + "…"
        out += text(x + w / 2 + 10, y + h - 9, sub, 9, "normal", "middle", "#51677F")
    return out


def orth_arrow(points, label="", color="#2F5597", dash=False, label_pos=0.5):
    ds = ' stroke-dasharray="8 6"' if dash else ''
    pts = ' '.join(f"{x},{y}" for x, y in points)
    out = f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"{ds} marker-end="url(#arrow)"/>'
    if label:
        segs = []
        total = 0
        for (x1, y1), (x2, y2) in zip(points[:-1], points[1:]):
            d = ((x2-x1)**2 + (y2-y1)**2) ** .5
            segs.append((x1, y1, x2, y2, d)); total += d
        target = total * label_pos; cur = 0; lx, ly = points[0]
        for x1, y1, x2, y2, d in segs:
            if cur + d >= target and d > 0:
                r = (target-cur)/d; lx = x1 + (x2-x1)*r; ly = y1 + (y2-y1)*r; break
            cur += d
        out += f'<rect x="{lx-54}" y="{ly-14}" width="108" height="18" rx="7" fill="white" opacity="0.94"/>'
        out += text(lx, ly-1, label, 10, "bold", "middle", color)
    return out


def _rid(profile: Dict[str, Any], role: str, i: int) -> str:
    prefix = profile.get("resource_prefix", "dgf")
    return f"{role}-{prefix}-{i:02d}"[:38]


def _solution_titles(solution_type: str) -> tuple[str, str]:
    return {
        "web_platform": ("Web / Application Tier", "Transactional / Cache Tier"),
        "api_platform": ("API / Microservice Tier", "API Data Services"),
        "enterprise_backoffice": ("Internal Business Application", "Enterprise Data Tier"),
        "data_platform": ("Ingestion / Processing Tier", "Data Lake / Analytics Tier"),
        "agentic_ai": ("Agent / AI Runtime", "Knowledge / State Tier"),
        "integration_platform": ("Integration / Orchestration Tier", "Integration State / Targets"),
    }.get(solution_type, ("Application / Compute", "Data Services"))


def _ops_services(profile: Dict[str, Any]) -> list[str]:
    ops = []
    if profile.get("application_insights"):
        ops.append("Application Insights")
    ops += ["Azure Monitor", "Log Analytics"]
    if profile.get("sentinel"):
        ops.append("Microsoft Sentinel")
    if profile.get("defender_for_cloud"):
        ops.append("Defender for Cloud")
    if profile.get("backup_enabled"):
        ops.append("Azure Backup / RSV")
    return ops[:6]


def _render_cards(s: list[str], services: list[str], x: int, y: int, w: int, h: int,
                  category: str, profile: Dict[str, Any], role: str, cols: int = 1) -> list[tuple]:
    cards = []
    if not services:
        return cards
    gap = 14
    card_h = 62
    card_w = int((w - gap*(cols+1)) / cols)
    for i, svc in enumerate(services):
        col = i % cols
        row = i // cols
        cx = x + gap + col * (card_w + gap)
        cy = y + 42 + row * (card_h + 14)
        if cy + card_h > y + h - 10:
            break
        s.append(service(cx, cy, card_w, card_h, svc, category, _rid(profile, role, i+1)))
        cards.append((svc, cx, cy, card_w, card_h))
    return cards


def render_architecture(profile: Dict[str, Any], project: Dict[str, Any], out_svg: Path, out_png: Path, icon_root: Path | None = None):
    """Render a topology-aware HLD.

    The previous renderer always drew Hub -> Compute Spoke -> Data Spoke even for
    single_vnet and virtual_wan profiles. This version changes the actual geometry,
    labels, connections and resilience boundary based on network_profile,
    solution_type and resilience_profile.
    """
    global _ICON_REGISTRY, _ICON_HITS, _ICON_MISSES
    _ICON_REGISTRY = AzureIconRegistry(icon_root)
    _ICON_HITS = 0; _ICON_MISSES = 0
    W, H = 1980, 1320
    s = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#2F5597"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#FBFDFF"/>',
    ]

    project_title = project["project_name"]
    s += [
        rect(22, 22, 1935, 94, "#FFFFFF", "#C9D8EA", 12, 1),
        text(30, 47, f"Azure HLD — {project_title}", 28, "bold"),
        text(30, 74, f"{project['project_id']} | {profile['architecture_id']} | {profile['solution_type']} | {profile['network_profile']} | v{profile['version']}", 14),
        text(30, 99, f"Primary {profile['primary_region']} | {profile['resilience_profile']} | Compute {profile['compute_profile']} | Signature {profile['architecture_signature']}", 13),
    ]

    # External / identity zone. Its composition changes for public vs private solutions.
    s += [rect(24, 155, 230, 430, COLORS["external"]), text(40, 186, "External / Identity", 18, "bold")]
    ext_cards = []
    if profile["internet_facing"]:
        s.append(service(45, 215, 188, 70, "Internet Users", "external", "Public consumers")); ext_cards.append(("Internet Users",45,215,188,70))
    else:
        s.append(service(45, 215, 188, 70, "Corporate Users", "external", "Private consumers")); ext_cards.append(("Corporate Users",45,215,188,70))
    s.append(service(45, 310, 188, 70, "Microsoft Entra ID", "identity", _rid(profile,"id",1))); ext_cards.append(("Microsoft Entra ID",45,310,188,70))
    if profile["expressroute"] or profile["vpn"] or profile["network_profile"] == "virtual_wan":
        s.append(service(45, 405, 188, 70, "Corporate Network", "external", "Hybrid / branch source")); ext_cards.append(("Corporate Network",45,405,188,70))

    # Edge / ingress zone. Private-only is rendered as private entry rather than a public edge stack.
    s += [rect(275, 155, 235, 430, COLORS["edge"]), text(290, 186, "Ingress / Edge", 18, "bold")]
    edge_services = list(profile["edge_services"])
    edge_cards = []
    ey = 215
    for i, svc in enumerate(edge_services):
        sub = _rid(profile, "edge", i+1)
        s.append(service(298, ey, 190, 70, svc, "edge", sub)); edge_cards.append((svc,298,ey,190,70)); ey += 92
    if profile["ddos_protection"] and profile["internet_facing"]:
        s.append(service(298, ey, 190, 64, "DDoS Protection", "security", _rid(profile,"ddos",1))); edge_cards.append(("DDoS Protection",298,ey,190,64))

    # Azure primary region shell.
    s += [rect(535, 135, 985, 875, "#F6FBFF", "#3C9CE0", 14, 3), text(555, 166, f"Primary Azure Region — {profile['primary_region']}", 21, "bold")]
    s.append(text(1490, 166, "Multi-AZ" if profile["multi_az"] else "Single-AZ / no AZ guarantee", 12, "bold", "end", "#2E75B6" if profile["multi_az"] else "#C65911"))

    app_title, data_title = _solution_titles(profile["solution_type"])
    platform = [x for x in profile.get("platform_services", []) if x not in profile["compute_services"] and x not in profile["data_services"]]
    compute_services = list(profile["compute_services"])
    data_services = list(profile["data_services"])
    ops = _ops_services(profile)

    anchors = {}
    network = profile["network_profile"]

    if network == "hub_spoke":
        # Real hub-and-spoke geometry.
        s += [rect(560, 205, 245, 610, COLORS["network"]), text(575, 235, "Hub VNet / Shared Services", 16, "bold")]
        hub_svcs = []
        if profile["azure_firewall"]: hub_svcs.append("Azure Firewall")
        if profile["bastion"]: hub_svcs.append("Azure Bastion")
        hub_svcs.append("Private DNS")
        if profile["nat_gateway"]: hub_svcs.append("NAT Gateway")
        if profile["expressroute"] or profile["vpn"]: hub_svcs.append("ER / VPN Gateway")
        hub_cards = _render_cards(s, hub_svcs, 560, 205, 245, 610, "network", profile, "hub", 1)

        s += [rect(830, 205, 315, 610, COLORS["compute"]), text(845, 235, f"Workload Spoke — {app_title}", 15, "bold")]
        app_services = (["Azure API Management"] if profile["api_management"] else []) + compute_services + [x for x in platform if x != "Azure API Management"]
        app_cards = _render_cards(s, app_services[:7], 830, 205, 315, 610, "compute", profile, "app", 1)

        s += [rect(1170, 205, 325, 610, COLORS["data"]), text(1185, 235, f"Data Spoke — {data_title}", 15, "bold")]
        data_cards = _render_cards(s, data_services[:7], 1170, 205, 325, 610, "data", profile, "data", 1)

        s += [rect(830, 845, 665, 145, COLORS["security"]), text(845, 875, "Operations / Security", 16, "bold")]
        ops_cards = _render_cards(s, ops, 830, 845, 665, 145, "security", profile, "ops", 3)

        if hub_cards:
            hy = hub_cards[0][2] + hub_cards[0][4]/2
            s.append(orth_arrow([(510, 300), (540,300), (540,hy), (hub_cards[0][1],hy)], "", "#2F5597"))
        if app_cards:
            ay = app_cards[0][2] + app_cards[0][4]/2
            if hub_cards:
                hx = hub_cards[0][1] + hub_cards[0][3]
                hy = hub_cards[0][2] + hub_cards[0][4]/2
                s.append(orth_arrow([(hx,hy),(817,hy),(817,ay),(app_cards[0][1],ay)], "", "#2F5597"))
            else:
                s.append(orth_arrow([(510,300),(810,300),(810,ay),(app_cards[0][1],ay)], "", "#2F5597"))
        if app_cards and data_cards:
            a=app_cards[min(1,len(app_cards)-1)]; d=data_cards[0]
            ay=a[2]+a[4]/2; dy=d[2]+d[4]/2
            s.append(orth_arrow([(a[1]+a[3],ay),(1157,ay),(1157,dy),(d[1],dy)], "", "#B03A5B"))
        anchors = {"app": app_cards, "data": data_cards, "ops": ops_cards, "hub": hub_cards}

    elif network == "single_vnet":
        # One VNet with subnets: no fake hub or spokes.
        net = profile.get("semantic_network", {})
        s += [rect(565, 205, 930, 610, COLORS["network"], "#159A9C", 14, 3), text(585, 238, f"Single Application VNet — {net.get('app_cidr','')}", 18, "bold")]
        s += [rect(590, 270, 410, 250, COLORS["compute"], "#5CA77B", 10, 2), text(605, 298, f"Application Subnet — {app_title}", 14, "bold")]
        app_services = (["Azure API Management"] if profile["api_management"] else []) + compute_services
        app_cards = _render_cards(s, app_services[:6], 590, 270, 410, 250, "compute", profile, "app", 2)

        s += [rect(1025, 270, 440, 250, COLORS["data"], "#CA7189", 10, 2), text(1040, 298, f"Data Subnet — {data_title}", 14, "bold")]
        data_cards = _render_cards(s, data_services[:6], 1025, 270, 440, 250, "data", profile, "data", 2)

        s += [rect(590, 550, 875, 235, COLORS["security"], "#8299D6", 10, 2), text(605, 580, "Management / Platform Subnet", 14, "bold")]
        mgmt_services = []
        if profile["azure_firewall"]: mgmt_services.append("Azure Firewall")
        if profile["bastion"]: mgmt_services.append("Azure Bastion")
        mgmt_services += platform + ops
        mgmt_cards = _render_cards(s, mgmt_services[:8], 590, 550, 875, 235, "security", profile, "mgmt", 4)

        if app_cards:
            ay=app_cards[0][2]+app_cards[0][4]/2
            s.append(orth_arrow([(510,300),(565,300),(565,ay),(app_cards[0][1],ay)], "", "#2F5597"))
        if app_cards and data_cards:
            a=app_cards[0]; d=data_cards[0]; ay=a[2]+a[4]/2; dy=d[2]+d[4]/2
            s.append(orth_arrow([(a[1]+a[3],ay),(1012,ay),(1012,dy),(d[1],dy)], "", "#B03A5B"))
        if app_cards and mgmt_cards:
            a=app_cards[-1]; m=mgmt_cards[0]
            s.append(orth_arrow([(a[1]+a[3]/2,a[2]+a[4]),(a[1]+a[3]/2,535),(m[1]+m[3]/2,535),(m[1]+m[3]/2,m[2])], "", "#2F855A"))
        anchors = {"app": app_cards, "data": data_cards, "ops": mgmt_cards, "hub": []}

    else:  # virtual_wan
        # Virtual WAN transit hub with true star-spoke geometry.
        s += [rect(830, 205, 360, 160, COLORS["network"], "#159A9C", 14, 3), text(850, 238, "Azure Virtual WAN Hub", 18, "bold")]
        vwan_services = ["Azure Virtual WAN", "ER / VPN Gateway"]
        if profile["azure_firewall"]: vwan_services.append("Azure Firewall")
        vwan_cards = _render_cards(s, vwan_services[:3], 830, 205, 360, 160, "network", profile, "vwan", 3)

        s += [rect(575, 420, 425, 390, COLORS["compute"], "#5CA77B", 12, 2), text(592, 450, f"Connected Workload Spoke — {app_title}", 15, "bold")]
        app_services = (["Azure API Management"] if profile["api_management"] else []) + compute_services + [x for x in platform if x != "Azure API Management"]
        app_cards = _render_cards(s, app_services[:8], 575, 420, 425, 390, "compute", profile, "app", 2)

        s += [rect(1070, 420, 425, 390, COLORS["data"], "#CA7189", 12, 2), text(1087, 450, f"Connected Data Spoke — {data_title}", 15, "bold")]
        data_cards = _render_cards(s, data_services[:8], 1070, 420, 425, 390, "data", profile, "data", 2)

        s += [rect(575, 845, 920, 145, COLORS["security"]), text(592, 875, "Central Operations / Security", 16, "bold")]
        ops_cards = _render_cards(s, ops, 575, 845, 920, 145, "security", profile, "ops", 3)

        hub_center=(1010,365)
        if vwan_cards:
            s.append(orth_arrow([(510,450),(780,450),(780,285),(vwan_cards[0][1],285)], "", "#0E7490"))
        if app_cards:
            a=app_cards[0]; ay=a[2]+a[4]/2
            s.append(orth_arrow([(1010,365),(1010,390),(790,390),(790,ay),(a[1]+a[3],ay)], "", "#2F5597"))
        if data_cards:
            d=data_cards[0]; dy=d[2]+d[4]/2
            s.append(orth_arrow([(1010,365),(1010,390),(1282,390),(1282,dy),(d[1],dy)], "", "#B03A5B"))
        if app_cards and data_cards:
            a=app_cards[-1]; d=data_cards[-1]; ay=a[2]+a[4]/2; dy=d[2]+d[4]/2
            s.append(orth_arrow([(a[1]+a[3],ay),(1035,ay),(1035,dy),(d[1],dy)], "", "#B03A5B", True))
        anchors = {"app": app_cards, "data": data_cards, "ops": ops_cards, "hub": vwan_cards}

    # Identity flow to workload is present in all topologies.
    if anchors.get("app"):
        a=anchors["app"][0]; ay=a[2]+a[4]/2
        s.append(orth_arrow([(233,345),(525,345),(525,ay),(a[1],ay)], "", "#7A4FB0", True))

    # Edge chain and edge -> primary entry.
    if edge_cards:
        source=ext_cards[0]; e0=edge_cards[0]
        sy=source[2]+source[4]/2; ey0=e0[2]+e0[4]/2
        s.append(orth_arrow([(source[1]+source[3],sy),(265,sy),(265,ey0),(e0[1],ey0)], "", "#2F5597"))
        for a, c in zip(edge_cards[:-1], edge_cards[1:]):
            ay=a[2]+a[4]/2; cy=c[2]+c[4]/2
            s.append(orth_arrow([(a[1]+a[3],ay),(500,ay),(500,cy),(c[1]+c[3],cy)], "", "#2F5597"))
        if anchors.get("app"):
            last=edge_cards[-1]; app=anchors["app"][0]
            ly=last[2]+last[4]/2; ay=app[2]+app[4]/2
            s.append(orth_arrow([(last[1]+last[3],ly),(525,ly),(525,ay),(app[1],ay)], "", "#2F5597"))

    # Resilience is also topology-aware. Active-active visually mirrors an active stack.
    rx, ry, rw = 1540, 135, 405
    res = profile["resilience_profile"]
    if res == "multi_region_active_active":
        s += [rect(rx, ry, rw, 875, COLORS["dr"], "#9673C5", 14, 3), text(rx+18, ry+31, "Secondary Region — ACTIVE", 18, "bold"), text(rx+18, ry+56, profile["secondary_region"], 13)]
        sec_services = ["Active Workload", "Active Data Replica", "Global Routing / Health", "DR Health / Failover"]
        sec_cards = _render_cards(s, sec_services, rx+10, ry+70, rw-20, 500, "dr", profile, "dr", 1)
        s.append(text(rx+20, 700, f"Replication: {profile['data_replication']}", 12, "bold"))
        s.append(text(rx+20, 725, "Bidirectional traffic/failover", 12))
        s.append(orth_arrow([(1510,365),(1535,365)], "", "#7030A0", True))
        s.append(orth_arrow([(1535,760),(1510,760)], "", "#7030A0", True))
    elif res == "multi_region_active_passive":
        s += [rect(rx, ry, rw, 875, COLORS["dr"], "#9673C5", 14, 3), text(rx+18, ry+31, "Secondary Region — WARM STANDBY", 17, "bold"), text(rx+18, ry+56, profile["secondary_region"], 13)]
        sec_services = ["Standby Workload", "Replicated Data"]
        if profile["azure_site_recovery"]: sec_services.append("Azure Site Recovery")
        if profile["backup_enabled"]: sec_services.append("Recovery Vault")
        sec_services.append("DR Health / Failover")
        _render_cards(s, sec_services, rx+10, ry+70, rw-20, 620, "dr", profile, "dr", 1)
        s.append(text(rx+20, 795, f"Replication: {profile['data_replication']}", 12, "bold"))
        s.append(text(rx+20, 820, "Failover tested" if profile["dr_tested"] else "FAILOVER NOT TESTED", 12, "bold", "start", "#2F855A" if profile["dr_tested"] else "#C65911"))
        s.append(orth_arrow([(1510,430),(1535,430)], "", "#7030A0", True))
    elif res == "single_region_multi_az":
        s += [rect(rx, ry, rw, 610, "#EDF8FF", "#4A90C2", 14, 3), text(rx+18, ry+31, "Same-Region Zone Resilience", 18, "bold")]
        for i, zone in enumerate([1,2,3]):
            y=215+i*135
            s.append(rect(rx+28,y,rw-56,105,"#FFFFFF","#7FA9D8",10,2))
            s.append(text(rx+45,y+31,f"Availability Zone {zone}",14,"bold"))
            s.append(text(rx+45,y+58,"Compute/data placement" if profile["multi_az"] else "No guaranteed placement",11))
            s.append(text(rx+45,y+82,_rid(profile,"az",zone),10,"normal","start","#51677F"))
        s.append(text(rx+25, 660, "No cross-region failover", 12, "bold", "start", "#C65911"))
    elif res == "backup_only":
        s += [rect(rx, ry, rw, 510, "#FFF8E7", "#D6A64A", 14, 3), text(rx+18, ry+31, "Backup-Restore Recovery", 18, "bold")]
        _render_cards(s, ["Azure Backup / RSV", "Recovery Vault"], rx+10, ry+65, rw-20, 230, "dr", profile, "backup", 1)
        s.append(text(rx+25, 480, "No live secondary compute", 12, "bold"))
        s.append(text(rx+25, 510, "Restore tested" if profile["restore_tested"] else "RESTORE NOT TESTED", 12, "bold", "start", "#2F855A" if profile["restore_tested"] else "#C65911"))
        s.append(orth_arrow([(1510,390),(1535,390)], "", "#A16207", True))
    else:
        s += [rect(rx, ry, rw, 430, "#FFF4F0", "#D07050", 14, 3), text(rx+18, ry+31, "Single-Region / Single-Zone", 18, "bold")]
        s.append(service(rx+28,220,rw-56,78,"Primary Workload Only","dr",_rid(profile,"single",1)))
        if profile["backup_enabled"]:
            s.append(service(rx+28,325,rw-56,78,"Azure Backup / RSV","dr",_rid(profile,"backup",1)))
        s.append(text(rx+25, 455, "No dedicated DR region", 12, "bold", "start", "#C65911"))

    # Summary / legend.
    s.append(rect(25, 1030, 1920, 260, "#F7F9FC", "#B8C6D8", 8, 1))
    s.append(text(45, 1060, "Case-specific architecture summary", 16, "bold"))
    facts = [
        f"Architecture ID: {profile['architecture_id']} | signature: {profile['architecture_signature']}",
        f"Solution: {profile['solution_type']} | Network: {profile['network_profile']} | Edge: {profile['edge_pattern']}",
        f"Compute: {profile['compute_profile']} | Data: {profile['data_profile']} | Platform: {', '.join(profile.get('platform_services',[])) or 'none'}",
        f"Private endpoints: {profile['private_endpoints']} | Firewall: {profile['azure_firewall']} | ER: {profile['expressroute']} | VPN: {profile['vpn']}",
        f"Resilience: {profile['resilience_profile']} | RTO/RPO: {profile['rto_hours']}h / {profile['rpo_minutes']}m | DR tested: {profile['dr_tested']}",
        f"Resource prefix: {profile['resource_prefix']}",
    ]
    for i, f in enumerate(facts):
        s.append(text(45, 1090 + i*25, f, 12))
    lx=1130; ly=1060
    s.append(text(lx, ly, "Visual grammar", 15, "bold"))
    grammar = {
        "hub_spoke": "Hub VNet + workload/data spokes",
        "single_vnet": "Single VNet + application/data/management subnets",
        "virtual_wan": "Virtual WAN transit hub + connected spokes",
    }[profile["network_profile"]]
    lines = [
        grammar,
        f"{app_title} → {data_title}",
        "Dashed links = identity / transit / replication",
        "Every card carries a case-specific resource instance",
    ]
    for i, line in enumerate(lines):
        s.append(text(lx, ly+32+i*28, line, 12))
    icon_mode = "Official Azure Architecture Icons" if (_ICON_REGISTRY and _ICON_REGISTRY.available) else "Fallback icons"
    s.append(text(1925, 1280, f"{icon_mode} | mapped {_ICON_HITS} | fallback {_ICON_MISSES}", 10, "normal", "end", "#66788A"))
    s.append("</svg>")

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text("\n".join(s), encoding="utf-8", newline="\n")
    svg_to_png(out_svg.read_bytes(), out_png, W, H)
    return out_svg, out_png


def svg_to_png(svg: bytes, out_png: Path, width: int, height: int) -> None:
    # Imported here so that running and scoring existing datasets never needs the native Cairo library.
    try:
        import cairosvg
    except (ImportError, OSError) as exc:
        raise RuntimeError("Rendering architecture diagrams requires CairoSVG and the Cairo graphics "
                           "library. Run `dgf-bench doctor` for installation guidance.") from exc
    cairosvg.svg2png(bytestring=svg, write_to=str(out_png), output_width=width, output_height=height)


def write_profile(profile, project, out_json: Path):
    out_json.write_text(json.dumps({"project": project, "architecture_profile": profile, "flows": architecture_flows(profile)}, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return out_json
