from __future__ import annotations
import random, hashlib
from datetime import date, timedelta
from typing import Any, Dict
from dgf_bench.benchmark_protocol import GATES, facts_id

EVIDENCE_CATALOG = [
    ("PROJECT_CHARTER","shared/project_charter.docx",["general","it","architecture","security","tech_readiness","procurement","legal","compliance"],False),
    ("BUSINESS_CASE","gate_evidence/general/business_case.csv",["general","procurement"],False),
    ("BUDGET_APPROVAL","gate_evidence/general/budget_approval.csv",["general","procurement"],True),
    ("ACTION_REGISTER","shared/action_register.csv",["general","it","architecture","security","tech_readiness","procurement","legal","compliance"],False),
    ("BENEFITS_PLAN","gate_evidence/general/benefits_plan.docx",["general"],False),
    ("PORTFOLIO_SNAPSHOT","gate_evidence/general/portfolio_snapshot.csv",["general"],True),
    ("COMMITTEE_BRIEFING","gate_evidence/general/committee_briefing.docx",["general"],False),
    ("CMDB_EXPORT","gate_evidence/it/cmdb_export.csv",["it","architecture","security"],True),
    ("TECH_CATALOG","gate_evidence/it/technology_catalog.json",["it","architecture"],True),
    ("LICENSE_POSITION","gate_evidence/it/license_position.csv",["it","procurement","legal"],True),
    ("LIFECYCLE_REGISTER","gate_evidence/it/lifecycle_eol.csv",["it","architecture"],True),
    ("SUPPORT_RACI","gate_evidence/it/support_raci.docx",["it","tech_readiness","general"],False),
    ("ITSM_CHANGE","gate_evidence/it/itsm_change.csv",["it","tech_readiness","general"],True),
    ("CAPACITY_REPORT","gate_evidence/it/capacity_report.csv",["it","tech_readiness"],True),
    ("TECH_WAIVER","gate_evidence/it/technology_waiver.docx",["it","architecture"],False),
    ("HLD","gate_evidence/architecture/Architecture_Diagram_Detailed.svg",["architecture","security","it","tech_readiness"],False),
    ("LLD","gate_evidence/architecture/LLD_Architecture_Notes.docx",["architecture","security","tech_readiness"],False),
    ("FLOW_MATRIX","gate_evidence/architecture/flow_matrix.csv",["architecture","security","it"],False),
    ("OPENAPI","gate_evidence/architecture/openapi.yaml",["architecture","security","it"],False),
    ("DATA_MODEL","gate_evidence/architecture/data_model.json",["architecture","security","compliance"],False),
    ("DATA_LINEAGE","gate_evidence/architecture/data_lineage.csv",["architecture","compliance"],False),
    ("IP_PLAN","gate_evidence/architecture/ip_plan.csv",["architecture","security","it"],True),
    ("ADR_REGISTER","gate_evidence/architecture/adr_register.csv",["architecture","it"],False),
    ("ARCH_DEBT","gate_evidence/architecture/architecture_debt.csv",["architecture","general"],False),
    ("AZURE_RESOURCE_GRAPH","gate_evidence/security/azure_resource_graph.json",["security","architecture","it"],True),
    ("IAM_EXPORT","gate_evidence/security/entra_role_assignments.csv",["security","it"],True),
    ("CONDITIONAL_ACCESS","gate_evidence/security/conditional_access.json",["security"],True),
    ("NSG_RULES","gate_evidence/security/nsg_rules.csv",["security","architecture"],True),
    ("WAF_POLICY","gate_evidence/security/waf_policy.json",["security","architecture"],True),
    ("SENTINEL_STATUS","gate_evidence/security/sentinel_connectors.json",["security","tech_readiness"],True),
    ("FIREWALL_POLICY","gate_evidence/security/firewall_policy.json",["security","architecture"],True),
    ("KEYVAULT_CONFIG","gate_evidence/security/keyvault_configuration.json",["security"],True),
    ("DEFENDER_FINDINGS","gate_evidence/security/defender_findings.csv",["security","tech_readiness"],True),
    ("VULN_SCAN","gate_evidence/security/vulnerability_scan.csv",["security","tech_readiness"],True),
    ("PENTEST","gate_evidence/security/pentest_report.docx",["security","tech_readiness"],False),
    ("THREAT_MODEL","gate_evidence/security/threat_model.docx",["security","architecture"],False),
    ("BACKUP_JOBS","gate_evidence/tech_readiness/backup_jobs.csv",["tech_readiness","security","it"],True),
    ("RESTORE_TEST","gate_evidence/tech_readiness/restore_test.csv",["tech_readiness","security","general"],True),
    ("FAILOVER_TEST","gate_evidence/tech_readiness/failover_test.csv",["tech_readiness","architecture","general"],True),
    ("LOAD_TEST","gate_evidence/tech_readiness/load_test.csv",["tech_readiness","architecture"],True),
    ("CI_PIPELINE","gate_evidence/tech_readiness/ci_pipeline.json",["tech_readiness","security"],True),
    ("RUNBOOK","gate_evidence/tech_readiness/production_runbook.docx",["tech_readiness","it","general"],False),
    ("ROLLBACK_TEST","gate_evidence/tech_readiness/rollback_test.csv",["tech_readiness","it"],True),
    ("SLO_SLI","gate_evidence/tech_readiness/slo_sli.csv",["tech_readiness","general"],True),
    ("MONITOR_ALERTS","gate_evidence/tech_readiness/monitor_alerts.csv",["tech_readiness","security"],True),
    ("RFP","gate_evidence/procurement/RFP.docx",["procurement","legal","security"],False),
    ("VENDOR_OFFERS","gate_evidence/procurement/vendor_offers.csv",["procurement","general"],False),
    ("SCORING_MATRIX","gate_evidence/procurement/scoring_matrix.csv",["procurement","general"],False),
    ("DUE_DILIGENCE","gate_evidence/procurement/due_diligence.csv",["procurement","security","compliance"],True),
    ("PRICING_TCO","gate_evidence/procurement/pricing_tco.csv",["procurement","general"],False),
    ("VENDOR_EVIDENCE_REQUEST","gate_evidence/procurement/vendor_evidence_request.docx",["procurement","security"],False),
    ("MSA","gate_evidence/legal/MSA.docx",["legal","procurement","security","compliance"],False),
    ("DPA","gate_evidence/legal/DPA.docx",["legal","compliance","security"],False),
    ("SLA_ANNEX","gate_evidence/legal/SLA_Annex.docx",["legal","procurement","tech_readiness"],False),
    ("INSURANCE","gate_evidence/legal/Insurance_Certificate.docx",["legal","procurement"],False),
    ("SIGNING_AUTHORITY","gate_evidence/legal/signing_authority.csv",["legal","general"],True),
    ("CONTRACT_PLAYBOOK","gate_evidence/legal/contract_playbook.json",["legal"],True),
    ("NEGOTIATION_LOG","gate_evidence/legal/negotiation_log.docx",["legal","procurement"],False),
    ("DATA_INVENTORY","gate_evidence/compliance/data_inventory.csv",["compliance","security","architecture","legal"],True),
    ("ROPA","gate_evidence/compliance/ropa.csv",["compliance","legal"],True),
    ("DPIA","gate_evidence/compliance/DPIA.docx",["compliance","legal","security"],False),
    ("REG_MAPPING","gate_evidence/compliance/regulatory_mapping.csv",["compliance","general"],True),
    ("RETENTION","gate_evidence/compliance/retention_schedule.csv",["compliance","legal"],True),
    ("ACCESSIBILITY","gate_evidence/compliance/accessibility_report.csv",["compliance","general"],False),
    ("CONTROL_MATRIX","gate_evidence/compliance/control_matrix.csv",["compliance","general"],True),
    ("EXPORT_CONTROL","gate_evidence/compliance/export_control.csv",["compliance","procurement"],True),
    ("AI_IMPACT","gate_evidence/compliance/AI_Impact_Assessment.docx",["compliance","security","legal"],False),
    # v9 systems of record: every decisive fact has an authoritative source visible to its gate.
    ("ARCHITECTURE_REGISTER","gate_evidence/architecture/architecture_register.json",["architecture","security","it","tech_readiness"],True),
    ("CONTRACT_REGISTER","gate_evidence/legal/contract_register.json",["legal","procurement","compliance"],True),
    ("COMPLIANCE_REGISTER","gate_evidence/compliance/compliance_register.csv",["compliance","legal","security"],True),
    ("PROCUREMENT_AWARD","gate_evidence/procurement/award_decision.json",["procurement","general","legal"],True),
    ("OPERATIONS_READINESS","gate_evidence/tech_readiness/operational_readiness.csv",["tech_readiness","it","general"],True),
    ("RELEASE_BLOCKERS","gate_evidence/tech_readiness/release_blockers.csv",["tech_readiness","general"],True),
    ("VENDOR_RESPONSES","gate_evidence/procurement/vendor_responses.json",["procurement","security","compliance"],False),
]

# What an older version of a document would say, and what an unverified claim asserts.
# Replacement values stay within each field's vocabulary so no marker reveals the perturbation.
PREVIOUS_STATE = {"approved":"draft","complete":"draft","signed":"draft","tested":"documented","documented":"draft",
                  "full":"limited","included":"limited","pass":"pending","clear":"pending","strong":"partial"}
CONFLICTING_CLAIM = {"complete":"draft","signed":"draft","approved":"draft","pass":"pending","clear":"pending",
                     "full":"limited","included":"missing","draft":"approved","missing":"complete","partial":"complete",
                     "pending":"clear","limited":"full","none":"full","reports_only":"full","ambiguous":"customer",
                     "documented":"tested"}


def _hash_float(seed:int, eid:str, salt:str=""):
    h=hashlib.sha256(f"{seed}:{eid}:{salt}".encode()).digest()
    return int.from_bytes(h[:8],"big")/2**64


def build_evidence_graph(case:Dict[str,Any], difficulty:int):
    seed=case["seed"]
    nodes=[]
    for i,(eid,path,consumers,authoritative) in enumerate(EVIDENCE_CATALOG, start=1):
        x=_hash_float(seed,eid)
        if authoritative:
            mode="truthful" if x>.04*difficulty else "temporarily_unavailable"
        else:
            # Non-authoritative documents can be stale/conflicting/partial.
            t=.04+.018*difficulty
            if x<t: mode="stale"
            elif x<2*t: mode="conflicting_claim"
            elif x<3*t: mode="partial"
            else: mode="truthful"
        version=1+int(_hash_float(seed,eid,"ver")*5)
        age_days=int(_hash_float(seed,eid,"age")*180)
        status="AVAILABLE" if mode!="temporarily_unavailable" else "UNAVAILABLE"
        if eid=="TECH_WAIVER" and case["it"]["catalog_status"]=="standard":
            status="NOT_APPLICABLE"; mode="truthful"
        nodes.append({
            "evidence_id":eid,
            "path":path,
            "consumers":consumers,
            "authoritative":authoritative,
            "version":version,
            "age_days":age_days,
            "public_status":status,
            "_hidden_mode":mode,
        })
    # Authoritative submitted-state exports make every rule observable. Unlike
    # generated reviewer documents these contain no oracle labels or actions.
    for gate in GATES:
        nodes.append({'evidence_id': facts_id(gate), 'path': f'gate_evidence/{gate}/review_facts.json',
                      'consumers': [gate], 'authoritative': True, 'version': 1,
                      'age_days': 0, 'public_status': 'AVAILABLE', '_hidden_mode': 'truthful'})
    edges=[]
    for node in nodes:
        for gate in node["consumers"]:
            edges.append({"from":node["evidence_id"],"to_gate":gate,"relation":"consumed_by"})
    return {"nodes":nodes,"edges":edges}


def public_graph(graph):
    return {
        "nodes":[{k:v for k,v in n.items() if not k.startswith("_hidden")} for n in graph["nodes"]],
        "edges":graph["edges"],
    }


def hidden_modes(graph):
    return {n["evidence_id"]:n["_hidden_mode"] for n in graph["nodes"]}


def node(graph,eid):
    return next(n for n in graph["nodes"] if n["evidence_id"]==eid)


def visible_value(case, graph, eid, key, truth):
    """Deterministically perturb non-authoritative evidence while keeping authoritative truth intact."""
    n=node(graph,eid)
    mode=n["_hidden_mode"]
    if mode in ("truthful","temporarily_unavailable"):
        return truth
    if mode=="partial":
        return None if _hash_float(case["seed"],eid,key)<.45 else truth
    if mode=="stale":
        if isinstance(truth,bool): return truth
        if isinstance(truth,(int,float)): return max(0, truth - (1 if isinstance(truth,int) else .5))
        if isinstance(truth,str): return PREVIOUS_STATE.get(truth, truth)
        return truth
    if mode=="conflicting_claim":
        if isinstance(truth,bool): return not truth
        if isinstance(truth,(int,float)): return truth + (1 if isinstance(truth,int) else .5)
        if isinstance(truth,str): return CONFLICTING_CLAIM.get(truth, truth)
    return truth


def ensure_available(graph, evidence_ids):
    """Make the given authoritative sources available and truthful (decisive sources, protocol v9)."""
    for n in graph["nodes"]:
        if n["evidence_id"] in evidence_ids and n["authoritative"] and n["public_status"]=="UNAVAILABLE":
            n["public_status"]="AVAILABLE"; n["_hidden_mode"]="truthful"
    return graph
