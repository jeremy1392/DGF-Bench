from __future__ import annotations
from typing import Any, Dict, List

PRIORITY={"GO":0,"GO_WITH_RESERVATIONS":1,"REWORK":2,"SUSPENSION":3,"NO_GO":4}

def finding(fid, severity, message, action, owner, evidence, disposition="REWORK", risk_acceptance_allowed=False):
    return {
        "id":fid,"severity":severity,"message":message,"required_action":action,
        "action_owner":owner,"evidence_required":evidence,"disposition":disposition,
        "risk_acceptance_allowed":risk_acceptance_allowed,
    }

def _result(gate,phase,findings,risk_owner,upstream=None):
    if findings:
        disp=max((f["disposition"] for f in findings), key=lambda x:PRIORITY[x])
        if disp=="NO_GO": opinion="UNFAVORABLE"
        elif disp in ("REWORK","SUSPENSION"): opinion="FAVORABLE_WITH_RESERVATIONS" if all(f["risk_acceptance_allowed"] for f in findings) else "UNFAVORABLE"
        else: opinion="FAVORABLE_WITH_RESERVATIONS"
    else:
        disp="GO"; opinion="FAVORABLE"
    return {
        "gate":gate,"phase":phase,"expert_opinion":opinion,"disposition":disp,
        "findings":findings,
        "blocking_findings":[f["id"] for f in findings if f["disposition"] in ("NO_GO","SUSPENSION","REWORK")],
        "required_actions":[{"action":f["required_action"],"owner":f["action_owner"],"finding_id":f["id"]} for f in findings],
        "risk_owner":risk_owner,
        "authorization_required":disp in ("GO_WITH_RESERVATIONS","SUSPENSION","NO_GO"),
        "approval_reference":None,
        "upstream_context":upstream or [],
    }

def evaluate_it(case:Dict[str,Any], phase:str):
    f=case["it"]; p=case["project"]; out=[]
    if f["catalog_status"]=="non_standard" and not f["waiver_present"]:
        out.append(finding("IT-CATALOG-001","high","Non-standard technology has no approved waiver.","SUBMIT_TECHNOLOGY_WAIVER","IT Architecture","Technology catalog + waiver","REWORK"))
    if f["duplicate_capability"]:
        out.append(finding("IT-RATIONAL-001","medium","An existing enterprise capability may already satisfy the need.","ASSESS_EXISTING_CAPABILITY","Enterprise Architecture","CMDB / application portfolio","REWORK",True))
    if not f["license_compliant"]:
        out.append(finding("IT-LICENSE-001","high","Licence position is not compliant.","REMEDIATE_LICENSE_POSITION","IT Asset Management","Licence entitlement report","REWORK"))
    if phase in ("design","build_acceptance","deployment_closure") and not f["cmdb_record_present"]:
        out.append(finding("IT-CMDB-001","medium","Required CMDB record is missing.","CREATE_CMDB_RECORD","ITSM","CMDB record","GO_WITH_RESERVATIONS",True))
    if phase in ("build_acceptance","deployment_closure") and not f["run_owner_present"]:
        out.append(finding("IT-RUN-001","high","No accountable run owner is recorded.","ASSIGN_RUN_OWNER","IT Operations","Support RACI","REWORK"))
    if f["technology_eol_months"]<12:
        out.append(finding("IT-EOL-001","high","Technology reaches end-of-life within 12 months.","CREATE_CONVERGENCE_PLAN","Technology Owner","Lifecycle register","REWORK",True))
    if phase in ("build_acceptance","deployment_closure") and f["capacity_headroom_pct"]<15:
        out.append(finding("IT-CAPACITY-001","medium","Production capacity headroom is below 15%.","INCREASE_CAPACITY_HEADROOM","Platform Operations","Capacity report","GO_WITH_RESERVATIONS",True))
    if phase=="deployment_closure" and f["change_record_status"] in ("draft","missing"):
        out.append(finding("IT-CHANGE-001","high","Production change is not approved.","OBTAIN_CHANGE_APPROVAL","Change Manager","ITSM change record","SUSPENSION"))
    return _result("it",phase,out,p["risk_owner"])

def evaluate_architecture(case,phase):
    f=case["architecture"]; p=case["project"]; a=case["architecture_profile"]; out=[]
    if f["ip_overlap"]:
        out.append(finding("ARCH-IP-001","critical","Address space overlaps an existing network range.","REDESIGN_IP_PLAN","Network Architect","IPAM / network plan","NO_GO"))
    if f["api_gateway_required"] and not f["api_gateway_present"]:
        out.append(finding("ARCH-API-001","high","External/API architecture requires a governed API gateway but none is present.","ADD_API_GATEWAY","Solution Architect","HLD + API design","REWORK"))
    if not f["data_owner_present"]:
        out.append(finding("ARCH-DATA-001","high","No accountable owner is defined for governed data domains.","ASSIGN_DATA_OWNER","Data Architect","Data model / ownership register","REWORK"))
    if f["measured_latency_ms"]>f["latency_target_ms"]:
        out.append(finding("ARCH-PERF-001","medium","Measured latency exceeds the architecture target.","REMEDIATE_LATENCY","Solution Architect","Performance test","GO_WITH_RESERVATIONS",True))
    if p["business_criticality"] in ("High","Critical") and not a["multi_az"] and not a["multi_region"]:
        out.append(finding("ARCH-HA-001","high","High-criticality workload has no zone or regional redundancy.","REDESIGN_FOR_HIGH_AVAILABILITY","Solution Architect","HLD / resilience design","REWORK"))
    if f["reversibility_status"] in ("draft","missing"):
        out.append(finding("ARCH-REV-001","medium","Reversibility design is incomplete.","COMPLETE_EXIT_ARCHITECTURE","Enterprise Architect","Exit plan","GO_WITH_RESERVATIONS",True))
    return _result("architecture",phase,out,p["risk_owner"])

def evaluate_security(case,phase):
    f=case["security"]; p=case["project"]; out=[]
    if f["internet_exposed"] and not f["waf_present"]:
        out.append(finding("SEC-WAF-001","critical","Internet-facing workload has no WAF control.","IMPLEMENT_WAF","Security Architect","WAF policy / HLD","NO_GO"))
    if f["internet_exposed"] and f["waf_present"] and str(f["waf_mode"]).lower()=="detection":
        out.append(finding("SEC-WAF-002","high","WAF is in detection-only mode.","ENABLE_WAF_PREVENTION","Network Security","WAF policy","REWORK",True))
    if p["data_classification"] in ("Confidential","Restricted") and not f["private_endpoints"]:
        out.append(finding("SEC-NET-001","critical","Sensitive data services are reachable without private endpoints.","ENABLE_PRIVATE_ENDPOINTS","Cloud Platform","Azure Resource Graph","NO_GO"))
    if f["mfa"]!="mandatory":
        out.append(finding("SEC-IAM-001","high","MFA is not mandatory for the service.","ENFORCE_MFA","IAM Team","Conditional Access policy","REWORK"))
    if f["shared_service_principal"]:
        out.append(finding("SEC-IAM-002","high","A shared service principal is used across workloads/environments.","CREATE_DEDICATED_WORKLOAD_IDENTITY","IAM Team","Role assignment export","REWORK"))
    if not f["logs_to_siem"]:
        out.append(finding("SEC-LOG-001","high","Security-relevant logs are not exported to the SIEM.","CONNECT_LOGS_TO_SIEM","SOC","Sentinel connector state","REWORK"))
    if f["critical_vulns_open"]>0:
        out.append(finding("SEC-VULN-001","critical",f"{f['critical_vulns_open']} critical vulnerability findings remain open.","REMEDIATE_CRITICAL_VULNERABILITIES","Product Team","Vulnerability scan","NO_GO"))
    if phase=="build_acceptance" and f["pentest_status"] in ("pending","missing"):
        out.append(finding("SEC-PENTEST-001","high","Required penetration test evidence is not complete.","COMPLETE_PENTEST","Security Testing","Pentest report","REWORK"))
    if f["key_rotation_days"] in (None,365):
        out.append(finding("SEC-KEY-001","medium","Key rotation is absent or too infrequent for the target profile.","IMPROVE_KEY_ROTATION","Cloud Security","Key Vault configuration","GO_WITH_RESERVATIONS",True))
    return _result("security",phase,out,p["risk_owner"])

def evaluate_tech(case,phase):
    f=case["tech_readiness"]; p=case["project"]; out=[]
    if not f["backup_enabled"]:
        out.append(finding("TR-BACKUP-001","critical","Production backup is not enabled.","ENABLE_BACKUP","Operations","Backup job report","NO_GO"))
    if not f["restore_tested"]:
        out.append(finding("TR-RESTORE-001","high","No successful restore test is available.","RUN_RESTORE_TEST","Production Lead","Restore test report","REWORK"))
    elif f["measured_restore_hours"]>f["target_rto_hours"]:
        out.append(finding("TR-RTO-001","high","Measured restore time exceeds the committed RTO.","REMEDIATE_RECOVERY_TIME","SRE","Recovery test report","REWORK",True))
    if f["measured_data_loss_minutes"]>f["target_rpo_minutes"]:
        out.append(finding("TR-RPO-001","high","Measured data loss exceeds the committed RPO.","REMEDIATE_RECOVERY_POINT","SRE","Recovery test report","REWORK",True))
    if f["dr_required"] and not f["dr_tested"]:
        out.append(finding("TR-DR-001","high","Multi-region design has not passed a failover test.","RUN_DR_FAILOVER_TEST","SRE","Failover test report","REWORK"))
    if f["load_test_pct_of_peak"]<100:
        out.append(finding("TR-LOAD-001","high","Load test did not reach expected production peak.","RERUN_LOAD_TEST_AT_PEAK","Performance Engineering","Load test results","REWORK"))
    if f["critical_static_findings"]>0:
        out.append(finding("TR-CODE-001","high","Critical static-analysis findings remain open.","REMEDIATE_BUILD_FINDINGS","Engineering","CI pipeline report","REWORK"))
    if f["runbook_status"]!="approved" or not f["on_call_defined"] or not f["handover_signed"]:
        out.append(finding("TR-OPS-001","medium","Operational handover/runbook/on-call package is incomplete.","COMPLETE_OPERATIONAL_HANDOVER","Operations","Runbook + handover","GO_WITH_RESERVATIONS",True))
    if not f["rollback_tested"]:
        out.append(finding("TR-ROLLBACK-001","medium","Rollback procedure has not been tested.","TEST_ROLLBACK","Release Manager","Rollback test","GO_WITH_RESERVATIONS",True))
    if f["open_blockers"]>0:
        out.append(finding("TR-BLOCKER-001","high",f"{f['open_blockers']} production blockers remain open.","CLOSE_PRODUCTION_BLOCKERS","Product Team","Open actions register","REWORK"))
    return _result("tech_readiness",phase,out,p["risk_owner"])

def evaluate_procurement(case,phase):
    f=case["procurement"]; p=case["project"]; out=[]
    sel=next(x for x in f["offers"] if x["vendor"]==f["selected_vendor"])
    if sel["mandatory_criteria_failed"]>0:
        out.append(finding("PROC-MAND-001","critical","Selected supplier failed mandatory RFP criteria.","REOPEN_VENDOR_SELECTION","Procurement","RFP scoring matrix","NO_GO"))
    if sel["sanctions"]=="hit":
        out.append(finding("PROC-SAN-001","critical","Sanctions screening returned a hit for the selected supplier.","STOP_VENDOR_ONBOARDING","Compliance / Procurement","Sanctions screening","NO_GO"))
    elif sel["sanctions"]=="pending":
        out.append(finding("PROC-SAN-002","high","Sanctions screening is pending.","COMPLETE_SANCTIONS_SCREENING","Procurement","Sanctions screening","SUSPENSION"))
    if sel["due_diligence"] in ("partial","pending"):
        out.append(finding("PROC-DD-001","high","Third-party due diligence is incomplete.","COMPLETE_VENDOR_DUE_DILIGENCE","Vendor Risk","Due diligence report","REWORK"))
    if sel["tco_3y_eur"]>f["purchasing_budget_eur"]*1.5:
        out.append(finding("PROC-BUDGET-001","high","Selected supplier TCO materially exceeds the purchasing envelope.","OBTAIN_BUDGET_ARBITRATION","CFO / Sponsor","TCO comparison","SUSPENSION",True))
    if not sel["references_checked"]:
        out.append(finding("PROC-REF-001","medium","Supplier references have not been checked.","CHECK_VENDOR_REFERENCES","Procurement","Reference check","GO_WITH_RESERVATIONS",True))
    return _result("procurement",phase,out,p["risk_owner"])

def evaluate_legal(case,phase):
    f=case["legal"]; p=case["project"]; out=[]
    if p["personal_data"] and f["dpa_status"] in ("draft","missing"):
        out.append(finding("LEGAL-DPA-001","critical","DPA is not executed for processing of personal data.","EXECUTE_DPA","Legal / Privacy","DPA","REWORK"))
    if f["liability_cap_multiplier"]==.5 and not f["security_carveout"]:
        out.append(finding("LEGAL-LIAB-001","high","Liability cap is low and has no security/data carve-out.","REDLINE_LIABILITY_CLAUSE","Legal","MSA clause 12","REWORK",True))
    if f["audit_right"] in ("reports_only","none"):
        out.append(finding("LEGAL-AUDIT-001","high","Audit rights are insufficient for the target service.","REDLINE_AUDIT_RIGHT","Legal","MSA audit clause","REWORK",True))
    if f["exit_assistance_days"]==0:
        out.append(finding("LEGAL-EXIT-001","medium","No contractual exit assistance is provided.","ADD_EXIT_ASSISTANCE","Legal / Procurement","Exit clause","GO_WITH_RESERVATIONS",True))
    if f["ip_ownership"]=="ambiguous":
        out.append(finding("LEGAL-IP-001","high","IP ownership is ambiguous.","CLARIFY_IP_OWNERSHIP","Legal","IP clause","REWORK"))
    if not f["insurance_valid"]:
        out.append(finding("LEGAL-INS-001","high","Required insurance certificate is invalid or expired.","OBTAIN_VALID_INSURANCE","Supplier","Insurance certificate","SUSPENSION"))
    if not f["signing_authority_valid"]:
        out.append(finding("LEGAL-SIGN-001","critical","Proposed signatory lacks recorded authority.","OBTAIN_AUTHORIZED_SIGNATORY","Corporate Secretary","Authority matrix","SUSPENSION"))
    if f["log_export_clause"]=="missing" and p["business_criticality"] in ("High","Critical"):
        out.append(finding("LEGAL-LOG-001","high","Contract lacks required log-export commitment.","ADD_LOG_EXPORT_CLAUSE","Legal","Security schedule","REWORK",True))
    return _result("legal",phase,out,p["risk_owner"])

def evaluate_compliance(case,phase):
    f=case["compliance"]; p=case["project"]; out=[]
    if f["dpia_required"] and f["dpia_status"]!="complete":
        out.append(finding("COMP-DPIA-001","high","Required DPIA is not complete.","COMPLETE_DPIA","DPO","DPIA","REWORK"))
    if not f["residency_compliant"]:
        out.append(finding("COMP-RESID-001","critical","Actual data region violates the required residency constraint.","RELOCATE_DATA_OR_OBTAIN_VALID_BASIS","DPO / Architecture","Data inventory + hosting evidence","NO_GO"))
    if f["audit_trail"]=="missing":
        out.append(finding("COMP-AUDIT-001","high","Required compliance audit trail is missing.","IMPLEMENT_AUDIT_TRAIL","Product Team","Audit evidence","REWORK"))
    if f["regulatory_mapping"]=="missing":
        out.append(finding("COMP-MAP-001","high","Applicable regulatory obligations are not mapped to controls.","COMPLETE_CONTROL_MAPPING","GRC","Regulatory mapping","REWORK"))
    if f["accessibility_status"]=="major_findings":
        out.append(finding("COMP-A11Y-001","high","Accessibility assessment contains major findings.","REMEDIATE_ACCESSIBILITY","Product Team","Accessibility report","REWORK",True))
    if f["export_control"]=="restricted":
        out.append(finding("COMP-EXPORT-001","critical","Export-control review indicates a restricted condition.","OBTAIN_EXPORT_CONTROL_CLEARANCE","Compliance","Export-control screening","NO_GO"))
    elif f["export_control"]=="pending":
        out.append(finding("COMP-EXPORT-002","high","Export-control screening is pending.","COMPLETE_EXPORT_CONTROL_REVIEW","Compliance","Export-control screening","SUSPENSION"))
    return _result("compliance",phase,out,p["risk_owner"])

def evaluate_general(case,phase,upstream_results):
    f=case["general"]; p=case["project"]; out=[]
    if f["strategic_alignment"]=="weak":
        out.append(finding("GEN-STRAT-001","high","Project has weak strategic alignment.","OBTAIN_STRATEGIC_ARBITRATION","Sponsor","Portfolio decision","SUSPENSION",True))
    if f["budget_requested_eur"]>f["budget_approved_eur"]:
        out.append(finding("GEN-BUDGET-001","high","Requested funding exceeds the approved envelope.","OBTAIN_BUDGET_APPROVAL","CFO / Sponsor","Budget approval","SUSPENSION",True))
    if phase not in ("deployment_closure",) and f["roi"]<.05:
        out.append(finding("GEN-BC-001","medium","Business-case ROI is below the governance threshold.","REVISE_BUSINESS_CASE","Business Owner","Business case","REWORK",True))
    # Consolidate specialist outcomes. A general gate cannot silently override an upstream blocker.
    upstream_no=[r for r in upstream_results if r["disposition"]=="NO_GO"]
    upstream_suspend=[r for r in upstream_results if r["disposition"]=="SUSPENSION"]
    upstream_rework=[r for r in upstream_results if r["disposition"]=="REWORK"]
    if upstream_no:
        out.append(finding("GEN-UPSTREAM-NOGO","critical",f"{len(upstream_no)} upstream gate(s) returned NO_GO.","RESOLVE_UPSTREAM_NO_GO","Project Manager","Upstream gate opinions","NO_GO"))
    elif upstream_suspend:
        out.append(finding("GEN-UPSTREAM-SUSPEND","high",f"{len(upstream_suspend)} upstream gate(s) are suspended.","RESOLVE_SUSPENDED_PREREQUISITES","Project Manager","Upstream gate opinions","SUSPENSION"))
    elif upstream_rework:
        out.append(finding("GEN-UPSTREAM-REWORK","high",f"{len(upstream_rework)} upstream gate(s) require rework.","CLOSE_UPSTREAM_REWORK","Project Manager","Upstream gate opinions","REWORK"))
    if phase=="deployment_closure" and f["change_plan_status"]=="missing":
        out.append(finding("GEN-BENEFITS-001","medium","Benefits/change ownership is incomplete at closure.","COMPLETE_BENEFITS_TRACKING","Business Owner","Benefits plan","GO_WITH_RESERVATIONS",True))
    return _result("general",phase,out,p["risk_owner"],[{"gate":r["gate"],"disposition":r["disposition"]} for r in upstream_results])

EVALUATORS={
    "it":evaluate_it,"architecture":evaluate_architecture,"security":evaluate_security,
    "tech_readiness":evaluate_tech,"procurement":evaluate_procurement,
    "legal":evaluate_legal,"compliance":evaluate_compliance,
}

def evaluate_gate(case,gate,phase,upstream_results=None):
    if gate=="general": return evaluate_general(case,phase,upstream_results or [])
    return EVALUATORS[gate](case,phase)


def evaluate_route(case, occurrences):
    results=[]
    phase_results=[]
    since_last_general=[]
    current_phase=None
    for occ in occurrences:
        if current_phase is None:
            current_phase=occ["phase"]
        if occ["phase"]!=current_phase:
            phase_results=[]
            current_phase=occ["phase"]
        upstream = phase_results if phase_results else since_last_general
        r=evaluate_gate(case,occ["gate"],occ["phase"],upstream)
        r["occurrence_id"]=occ["occurrence_id"]
        r["position"]=occ["position"]
        results.append(r)
        phase_results.append(r)
        if occ["gate"]=="general":
            since_last_general=[]
        else:
            since_last_general.append(r)
    return results
