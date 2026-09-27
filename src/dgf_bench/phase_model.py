from __future__ import annotations
from dgf_bench.routes import PHASE_ORDER

# v9: licence and lifecycle records are known when the IT gate first meets (opportunity).
BASE=["PROJECT_CHARTER","BUSINESS_CASE","BUDGET_APPROVAL","PORTFOLIO_SNAPSHOT","CMDB_EXPORT","TECH_CATALOG","DATA_INVENTORY","REG_MAPPING","LICENSE_POSITION","LIFECYCLE_REGISTER"]
ADDITIONS={
    "opportunity":[],
    "framing":["HLD","THREAT_MODEL","RFP","DPIA","AI_IMPACT","CAPACITY_REPORT","COMPLIANCE_REGISTER"],
    "design":["LLD","FLOW_MATRIX","OPENAPI","DATA_MODEL","DATA_LINEAGE","IP_PLAN","ADR_REGISTER","ARCH_DEBT","VENDOR_OFFERS","SCORING_MATRIX","DUE_DILIGENCE","PRICING_TCO","VENDOR_EVIDENCE_REQUEST","MSA","DPA","SLA_ANNEX","CONTRACT_PLAYBOOK","NEGOTIATION_LOG","AZURE_RESOURCE_GRAPH","IAM_EXPORT","CONDITIONAL_ACCESS","NSG_RULES","WAF_POLICY","FIREWALL_POLICY","KEYVAULT_CONFIG","SENTINEL_STATUS","DEFENDER_FINDINGS","VULN_SCAN","ARCHITECTURE_REGISTER","CONTRACT_REGISTER","PROCUREMENT_AWARD","VENDOR_RESPONSES"],
    "build_acceptance":["PENTEST","BACKUP_JOBS","RESTORE_TEST","FAILOVER_TEST","LOAD_TEST","CI_PIPELINE","RUNBOOK","ROLLBACK_TEST","SLO_SLI","MONITOR_ALERTS","SUPPORT_RACI","OPERATIONS_READINESS","RELEASE_BLOCKERS"],
    "deployment_closure":["ITSM_CHANGE","INSURANCE","SIGNING_AUTHORITY","RETENTION","ACCESSIBILITY","CONTROL_MATRIX","EXPORT_CONTROL","BENEFITS_PLAN","COMMITTEE_BRIEFING","ACTION_REGISTER"],
    "governance":[],
}

def phase_visibility(graph):
    known={n['evidence_id'] for n in graph['nodes'] if n['evidence_id'].startswith('REVIEW_FACTS_')}; out={}
    for phase in PHASE_ORDER:
        if phase=="opportunity": known.update(BASE)
        known.update(ADDITIONS.get(phase,[]))
        if phase=="governance": known.update(n["evidence_id"] for n in graph["nodes"])
        # Keep only IDs present in this case graph.
        ids={n["evidence_id"] for n in graph["nodes"]}
        out[phase]=sorted(known & ids)
    return out
