from __future__ import annotations
import random, hashlib
from datetime import date, timedelta
from typing import Any, Dict, List
from dgf_bench.azure_architecture import generate_architecture_profile
from dgf_bench.semantic_profiles import enrich_profile

REFERENCE_DATE = date(2026, 9, 21)

FIRST = ["Nadia","Jeremy","Sofia","Omar","Leila","Thomas","Marc","Elena","David","Amira","Lucas","Maya","Noor","Adam","Ines","Victor","Daria","Samir","Clara","Yusuf"]
LAST = ["Martin","Canale","Ivanov","Moreau","Benali","Rossi","Smith","Petrescu","Dubois","Khan","Garcia","Nguyen","Haddad","Laurent","Meyer","Santos","Volkov","Rahman","Costa","Bennett"]
UNITS = ["Finance","Retail","HR","Operations","Sales","Risk","Procurement","Customer Service","Technology","Treasury","Data Office","Corporate Services"]
PROJECT_DOMAINS = [
    "Customer 360 CRM", "Payments API Hub", "AI Service Desk", "Regulatory Reporting Modernization",
    "Data Governance Platform", "Secure Vendor Portal", "Digital Treasury Hub", "Enterprise Workflow Platform",
    "Identity Modernization", "Risk Analytics Platform", "Supply Chain Integration", "Claims Automation",
    "Employee Experience Portal", "Fraud Detection Platform", "Digital Onboarding", "Observability Platform",
    "Contract Intelligence", "Enterprise Search", "Cloud Landing Zone Extension", "Data Quality Modernization",
]
CODENAMES = [
    "Aster", "Boreal", "Cobalt", "Delta", "Ember", "Falcon", "Gaia", "Helix", "Ion", "Juno",
    "Kepler", "Lumen", "Meridian", "Nova", "Orion", "Pulse", "Quartz", "Raven", "Solstice", "Titan",
    "Umbra", "Vega", "Willow", "Xenon", "Yotta", "Zephyr", "Atlas", "Nimbus", "Saffron", "Vertex",
]
REGIONS = ["UAE North","UAE Central","West Europe","North Europe","UK South","France Central","East US","West US 2"]


def _stable_code(*parts:str, n:int=8) -> str:
    raw='|'.join(map(str,parts)).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:n].upper()


def person(rng, unique_tag:str|None=None):
    # Human-readable synthetic identity. unique_tag is deliberately part of the
    # visible name so two generated DGF dossiers never accidentally reuse the
    # same person and leak a cross-case shortcut to an evaluated model.
    name=f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    return f"{name}-{unique_tag}" if unique_tag else name


def w(rng, pairs):
    vals, weights = zip(*pairs)
    return rng.choices(vals, weights=weights, k=1)[0]


def boolp(rng,p): return rng.random() < p


def _project(seed:int, route_key:str) -> Dict[str,Any]:
    rng=random.Random(seed)
    start=REFERENCE_DATE + timedelta(days=rng.randint(-20,90))
    ptype={"buy":"new_platform","integrate":"ma_integration","build":"new_project","full_lifecycle":"new_project"}[route_key]
    route_code={"buy":"BUY","integrate":"INT","build":"BLD","full_lifecycle":"FUL"}[route_key]
    project_id=f"DGF-{route_code}-{seed:06d}"
    code=_stable_code(route_key,seed,n=5)
    codename=CODENAMES[(seed*7 + len(route_key)*11) % len(CODENAMES)]
    domain=PROJECT_DOMAINS[(seed*13 + len(route_key)*5) % len(PROJECT_DOMAINS)]
    if route_key=="integrate":
        name=f"{codename} Acquisition Integration — {domain} [{code}]"
    else:
        name=f"Project {codename} — {domain} [{code}]"
    criticality=w(rng,[("Low",1),("Medium",3),("High",5),("Critical",3)])
    classification=w(rng,[("Internal",3),("Confidential",5),("Restricted",3),("Public",1)])
    primary=rng.choice(REGIONS)
    return {
        "project_id":project_id,
        "generation_seed":seed,
        "project_name":name,
        "project_code":code,
        "project_type":ptype,
        "route":route_key,
        "business_unit":rng.choice(UNITS),
        "sponsor":person(rng,f"{seed}-SP"),
        "project_manager":person(rng,f"{seed}-PM"),
        "business_owner":person(rng,f"{seed}-BO"),
        "service_owner":person(rng,f"{seed}-SO"),
        "risk_owner":person(rng,f"{seed}-RO"),
        "start_date":start.isoformat(),
        "target_go_live":(start+timedelta(days=rng.randint(120,420))).isoformat(),
        "users_impacted":rng.choice([80,250,500,1000,2000,5000,12000]),
        "budget_requested_eur":rng.choice([250_000,500_000,900_000,1_500_000,3_000_000,7_000_000]),
        "data_classification":classification,
        "business_criticality":criticality,
        "primary_region":primary,
        "personal_data":boolp(rng,.72),
        "payment_data":boolp(rng,.25),
        "employee_data":boolp(rng,.28),
        "ai_enabled":boolp(rng,.35),
        "external_users":boolp(rng,.48),
    }

def _architecture(seed:int, project:Dict[str,Any], difficulty:int, architecture_attempt:int=0) -> Dict[str,Any]:
    # architecture_attempt is used by the balanced dataset generator for
    # deterministic rejection-sampling when a visual topology signature has
    # already occurred in the same dataset.
    arch_seed=seed+10_000+(architecture_attempt*104_729)
    profile=generate_architecture_profile(arch_seed,project,difficulty,max(.06,.04*difficulty))
    profile=enrich_profile(profile,arch_seed+100,difficulty)
    # Topology and semantic ledgers are derived from the generated configuration.
    profile["source_of_truth"]="synthetic_resource_inventory"
    return profile


def _general(seed:int, project:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+20_000)
    requested=project["budget_requested_eur"]
    approved=int(requested*rng.choice([.70,.85,1.0,1.0,1.10]))
    roi=round(rng.uniform(-.03,.42),3)
    return {
        "strategic_alignment":rng.choice(["strong","strong","partial","weak"]),
        "portfolio_priority":rng.choice(["P0","P1","P1","P2","P3"]),
        "budget_requested_eur":requested,
        "budget_approved_eur":approved,
        "roi":roi,
        "npv_eur":int(requested*rng.uniform(-.15,1.25)),
        "payback_months":rng.randint(9,60),
        "schedule_slip_weeks":rng.choice([0,0,1,2,4,8]),
        "fte_needed":round(rng.uniform(3,30),1),
        "fte_available":round(rng.uniform(2,28),1),
        "change_plan_status":rng.choice(["complete","partial","draft","missing"]),
        "benefits_kpi":rng.choice(["availability","processing_time","cost_to_serve","NPS","conversion_rate"]),
        "benefits_owner":person(rng,f"{seed}-BEN"),
    }


def _it(seed:int, project:Dict[str,Any], arch:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+30_000)
    return {
        "catalog_status":rng.choice(["standard","standard","approved_exception","non_standard"]),
        "waiver_present":boolp(rng,.55),
        "waiver_expiry_days":rng.choice([30,90,180,365]),
        "duplicate_capability":boolp(rng,.22),
        "existing_alternative":rng.choice(["none","legacy_crm","shared_api_platform","enterprise_data_platform"]),
        "license_compliant":boolp(rng,.88),
        "annual_license_cost_eur":rng.randint(20_000,650_000),
        "cmdb_record_present":boolp(rng,.86),
        "run_owner_present":boolp(rng,.90),
        "support_model":rng.choice(["24x7_internal","business_hours","vendor_only","product_team"]),
        "patch_sla_days":rng.choice([7,14,30,90]),
        "capacity_headroom_pct":rng.choice([8,15,20,30,45]),
        "technology_eol_months":rng.choice([4,8,18,36,60]),
        "change_record_status":rng.choice(["approved","scheduled","draft","missing"]),
        "service_continuity_class":rng.choice(["gold","silver","bronze"]),
        "hosting_model":arch["compute_profile"],
    }


def _architecture_facts(seed:int, project:Dict[str,Any], arch:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+40_000)
    return {
        "hld_version":f"HLD-{project['project_code']}-v{rng.randint(1,4)}.{rng.randint(0,9)}",
        "lld_version":f"LLD-{project['project_code']}-v{rng.randint(1,4)}.{rng.randint(0,9)}",
        "api_gateway_required":project["external_users"] or arch["solution_type"] in ("api_platform","integration_platform"),
        "api_gateway_present":bool(arch.get("api_management")),
        "ip_overlap":boolp(rng,.08 + .03*difficulty),
        "data_owner_present":boolp(rng,.88),
        "data_lineage_complete":boolp(rng,.74),
        "latency_target_ms":rng.choice([20,50,100,200,500]),
        "measured_latency_ms":rng.choice([15,30,55,95,180,350,700]),
        "load_test_peak_pct":rng.choice([50,75,100,125,150]),
        "architecture_debt_items":rng.randint(0,7),
        "adr_count":rng.randint(2,16),
        "reversibility_status":rng.choice(["tested","documented","draft","missing"]),
        "data_export_supported":boolp(rng,.86),
        "private_cidrs":[arch.get("semantic_network",{}).get("hub_cidr"),arch.get("semantic_network",{}).get("app_cidr"),arch.get("semantic_network",{}).get("data_cidr")],
        "dns_zone":f"{project['project_id'].lower()}.corp.example",
    }


def _security(seed:int, project:Dict[str,Any], arch:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+50_000)
    semc=arch.get("semantic_compute",{})
    seme=arch.get("semantic_edge",{})
    return {
        "internet_exposed":arch["internet_facing"],
        "waf_present":seme.get("waf_present", "waf" in arch["edge_pattern"]),
        "waf_mode":seme.get("waf_mode","Prevention"),
        "azure_firewall":arch["azure_firewall"],
        "private_endpoints":arch["private_endpoints"],
        "managed_identity":arch["managed_identity"],
        "mfa":"mandatory" if arch["conditional_access"] else rng.choice(["optional","missing"]),
        "pim":arch["pim"],
        "shared_service_principal":boolp(rng,.10+.03*difficulty),
        "encryption_in_transit":boolp(rng,.97),
        "encryption_at_rest":boolp(rng,.96),
        "key_rotation_days":rng.choice([30,60,90,180,365,None]),
        "logs_to_siem":arch["logs_to_siem"],
        "sentinel":arch["sentinel"],
        "defender":arch["defender_for_cloud"],
        "critical_vulns_open":rng.choice([0,0,0,1,2,3]),
        "high_vulns_open":rng.randint(0,9),
        "pentest_age_days":rng.choice([14,30,60,90,180,365,None]),
        "pentest_status":rng.choice(["pass","pass_with_findings","pending","missing"]),
        "secrets_in_keyvault":boolp(rng,.87),
        "edr_enabled":semc.get("edr", True),
        "network_policy":semc.get("network_policy","N/A"),
        "image_scanning":semc.get("image_scanning",True),
        "residual_risk_acceptance":boolp(rng,.28),
    }


def _tech(seed:int, project:Dict[str,Any], arch:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+60_000)
    target_rto=arch["rto_hours"]
    target_rpo=arch["rpo_minutes"]
    measured_restore=round(target_rto*rng.choice([.55,.85,1.0,1.25,1.75]),2)
    measured_data_loss=max(0,int(target_rpo*rng.choice([0,.5,1,1.4,2])))
    return {
        "test_completion_pct":rng.randint(65,100),
        "pilot_completed":boolp(rng,.82),
        "load_test_pct_of_peak":rng.choice([50,75,100,125,150]),
        "p95_ms":rng.randint(35,1800),
        "unit_test_coverage_pct":rng.randint(35,95),
        "critical_static_findings":rng.choice([0,0,1,2,3]),
        "observability_metrics":boolp(rng,.92),
        "observability_logs":boolp(rng,.92),
        "observability_traces":boolp(rng,.72),
        "backup_enabled":arch["backup_enabled"],
        "restore_tested":arch["restore_tested"],
        "target_rto_hours":target_rto,
        "measured_restore_hours":measured_restore,
        "target_rpo_minutes":target_rpo,
        "measured_data_loss_minutes":measured_data_loss,
        "dr_required":arch["multi_region"],
        "dr_tested":arch["dr_tested"],
        "runbook_status":rng.choice(["approved","draft","missing"]),
        "on_call_defined":boolp(rng,.86),
        "handover_signed":boolp(rng,.82),
        "rollback_tested":boolp(rng,.80),
        "open_blockers":rng.choice([0,0,0,1,2,3]),
    }


def _vendors(seed:int, project:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+70_000)
    roots=["Asterion Cloud","BlueMesa Systems","CedarPoint Digital","DeltaForge Technologies","Everline Software","Fluxbridge Labs","GranitePeak Systems","HelioStack Cloud"]
    code=project.get("project_code",str(seed))
    offset=seed % len(roots)
    names=[f"{roots[(offset+i)%len(roots)]} {code}-{i+1}" for i in range(4)]
    offers=[]
    for i,n in enumerate(names):
        mandatory_fail=rng.randint(0,2 if difficulty>=3 else 1)
        sanctions=rng.choice(["clear","clear","clear","pending","hit"] if difficulty>=4 else ["clear","clear","pending"])
        security_score=rng.randint(55,98)
        functional=rng.randint(60,98)
        tco=rng.randint(250_000,2_800_000)
        references=boolp(rng,.82)
        due_diligence=rng.choice(["complete","complete","partial","pending"])
        weighted_score=round(.35*functional+.30*security_score+.20*(100-min(tco/30000,100))+.15*(90 if references else 50),1)
        offers.append({"vendor":n,"functional_score":functional,"security_score":security_score,"tco_3y_eur":tco,"mandatory_criteria_failed":mandatory_fail,"sanctions":sanctions,"references_checked":references,"due_diligence":due_diligence,"weighted_score":weighted_score})
    eligible=[o for o in offers if o["mandatory_criteria_failed"]==0 and o["sanctions"]!="hit"]
    selected=max(eligible or offers,key=lambda x:x["weighted_score"])["vendor"]
    # Occasionally simulate a procurement mistake in canonical selection.
    if difficulty>=4 and boolp(rng,.20): selected=rng.choice(offers)["vendor"]
    return {"offers":offers,"selected_vendor":selected,"competition":"open_tender","rfp_requirements":rng.randint(35,180),"purchasing_budget_eur":project["budget_requested_eur"]}


def _legal(seed:int, project:Dict[str,Any], procurement:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+80_000)
    return {
        "contract_version":f"MSA-{project['project_code']}-{rng.randint(3,12)}.{rng.randint(0,9)}",
        "governing_law":rng.choice(["UAE","England & Wales","France","Ireland"]),
        "dpa_status":rng.choice(["signed","signed","draft","missing"]) if project["personal_data"] else "not_required",
        "liability_cap_multiplier":rng.choice([.5,1,1,2,5,"unlimited"]),
        "security_carveout":boolp(rng,.66),
        "audit_right":rng.choice(["full","limited","reports_only","none"]),
        "termination_for_convenience":boolp(rng,.62),
        "exit_assistance_days":rng.choice([0,30,60,90,180]),
        "ip_ownership":rng.choice(["customer","vendor","shared","ambiguous"]),
        "insurance_valid":boolp(rng,.88),
        "cyber_insurance_m":rng.choice([0,1,2,5,10,20]),
        "signing_authority_valid":boolp(rng,.90),
        "data_transfer_clause":rng.choice(["adequate","scc","contractual_only","missing"]),
        "sla_penalties":boolp(rng,.72),
        "log_export_clause":rng.choice(["included","limited","missing"]),
    }


def _compliance(seed:int, project:Dict[str,Any], arch:Dict[str,Any], difficulty:int):
    rng=random.Random(seed+90_000)
    regs=[]
    if project["personal_data"] and project["primary_region"] in ("West Europe","North Europe","France Central","UK South"): regs.append("GDPR")
    if project["business_unit"] in ("Finance","Risk") and project["business_criticality"] in ("High","Critical"): regs.append("DORA")
    if project["business_criticality"]=="Critical": regs.append("NIS2")
    if project["payment_data"]: regs.append("PCI-DSS")
    if project["ai_enabled"]: regs.append("EU AI Act / internal AI policy")
    if not regs: regs=["Internal policy baseline"]
    required_residency="EU" if "GDPR" in regs and boolp(rng,.45) else rng.choice(["No restriction","UAE","EU","UK"])
    actual=project["primary_region"]
    def residence_ok():
        if required_residency=="No restriction": return True
        if required_residency=="EU": return actual in ("West Europe","North Europe","France Central")
        if required_residency=="UAE": return actual in ("UAE North","UAE Central")
        if required_residency=="UK": return actual=="UK South"
        return True
    dpia_req=project["personal_data"] and (project["ai_enabled"] or project["business_criticality"] in ("High","Critical"))
    return {
        "applicable_regulations":regs,
        "dpia_required":dpia_req,
        "dpia_status":rng.choice(["complete","complete","draft","missing"]) if dpia_req else "not_required",
        "required_residency":required_residency,
        "residency_compliant":residence_ok(),
        "retention_years":rng.choice([1,3,5,7,10]),
        "legal_hold_supported":boolp(rng,.82),
        "deletion_enforced":boolp(rng,.82),
        "audit_trail":rng.choice(["complete","complete","partial","missing"]),
        "accessibility_status":rng.choice(["pass","minor_findings","major_findings","not_assessed"]),
        "ethics_review":rng.choice(["passed","conditional","not_required","missing"]),
        "regulatory_mapping":rng.choice(["complete","partial","missing"]),
        "third_party_monitoring":rng.choice(["continuous","annual","onboarding_only","none"]),
        "export_control":rng.choice(["clear","clear","pending","restricted"]),
    }


def generate_canonical_case(seed:int, route_key:str="build", difficulty:int=3, architecture_attempt:int=0, fact_attempts=None) -> Dict[str,Any]:
    fact_attempts=fact_attempts or {}
    allowed={'general','it','architecture','security','tech_readiness','procurement','legal','compliance'}
    if set(fact_attempts)-allowed or any(type(v) is not int or v<0 for v in fact_attempts.values()):
        raise ValueError('Invalid fact sampling attempts')
    def draw(gate): return seed+1_000_003*fact_attempts.get(gate,0)
    project=_project(seed,route_key)
    arch=_architecture(seed,project,difficulty,architecture_attempt)
    general=_general(draw('general'),project,difficulty)
    it=_it(draw('it'),project,arch,difficulty)
    architecture=_architecture_facts(draw('architecture'),project,arch,difficulty)
    security=_security(draw('security'),project,arch,difficulty)
    tech=_tech(draw('tech_readiness'),project,arch,difficulty)
    procurement=_vendors(draw('procurement'),project,difficulty)
    legal=_legal(draw('legal'),project,procurement,difficulty)
    compliance=_compliance(draw('compliance'),project,arch,difficulty)
    return {
        "schema_version":"2.0-facts-first",
        "seed":seed,
        "difficulty":difficulty,
        "project":project,
        "architecture_profile":arch,
        "general":general,
        "it":it,
        "architecture":architecture,
        "security":security,
        "tech_readiness":tech,
        "procurement":procurement,
        "legal":legal,
        "compliance":compliance,
    }
