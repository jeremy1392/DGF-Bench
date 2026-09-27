#!/usr/bin/env python3
from __future__ import annotations
import random, copy
from typing import Dict, Any

COMPUTE_SERVICE_MAP = {
    "vm_single": ["Azure VM"],
    "vm_ha": ["Azure VM AZ1","Azure VM AZ2"],
    "vmss": ["VM Scale Set AZ1","VM Scale Set AZ2","VM Scale Set AZ3"],
    "dedicated_host": ["Azure Dedicated Host","VM Group A","VM Group B"],
    "aks": ["AKS System Pool","AKS App Pool","Ingress Controller","Worker Pods"],
    "container_apps": ["Azure Container Apps Environment","Frontend Container","API Container","Worker Container"],
    "app_service": ["Azure App Service","Deployment Slot"],
    "functions": ["Azure Functions","Function App Workers"],
    "mixed": ["Azure App Service","AKS App Pool","Azure Functions","Batch VM"],
}


DATA_SERVICE_MAP = {
    "transactional": ["Azure SQL Database","Azure Storage Account","Azure Cache for Redis"],
    "enterprise_sql": ["Azure SQL Managed Instance","Azure Storage Account","Azure Key Vault"],
    "postgres": ["Azure Database for PostgreSQL","Azure Storage Account","Azure Cache for Redis"],
    "event_driven": ["Azure Cosmos DB","Azure Service Bus","Azure Event Hubs","Azure Storage Account"],
    "analytics": ["Azure Data Lake Storage","Azure Synapse / Fabric Endpoint","Azure SQL Database"],
    "ai": ["Azure AI Search","Azure Storage Account","Azure Cosmos DB","Azure Key Vault"],
}
REGIONS=["UAE North","UAE Central","West Europe","North Europe","UK South","France Central","Germany West Central","East US","East US 2","West US 2","Southeast Asia","Australia East"]

EDGE_SERVICE_MAP = {
    "frontdoor_waf_cdn": ["Azure Front Door Premium", "WAF + CDN"],
    "cdn_then_waf": ["Azure CDN", "Application Gateway WAF"],
    "waf_then_cdn": ["Application Gateway WAF", "Azure CDN"],
    "waf_only": ["Application Gateway WAF"],
    "frontdoor_only": ["Azure Front Door"],
    "private_only": ["Private Access Only"],
}

def _choice(rng, vals): return rng.choice(list(vals))
def _bool(rng,p=.5): return rng.random()<p

def _compute_details(profile, rng, difficulty):
    c=profile["compute_profile"]
    common={
        "health_probes":_bool(rng,.85),
        "autoscale":_bool(rng,.78),
        "min_instances":rng.choice([1,2,2,3]),
        "max_instances":rng.choice([4,6,10,20,50]),
        "managed_identity":profile.get("managed_identity",False),
        "private_ingress":not profile.get("internet_facing",True) or profile.get("private_endpoints",False),
    }
    if c in ("vm_single","vm_ha","vmss","dedicated_host"):
        d={
            **common,
            "os":_choice(rng,["Windows Server 2025","Ubuntu 24.04 LTS","RHEL 9"]),
            "vm_size":_choice(rng,["D4as_v5","D8as_v5","E8as_v5","F8s_v2"]),
            "disk_encryption":_bool(rng,.9),
            "edr":_bool(rng,.88),
            "update_manager":_bool(rng,.78),
            "jit_access":_bool(rng,.65),
            "golden_image":_bool(rng,.72),
            "availability_set": c=="vm_ha" and _bool(rng,.5),
            "vmss_zonal": c=="vmss" and profile.get("multi_az",False),
            "asr_enabled":profile.get("azure_site_recovery",False),
            "backup_vms":profile.get("backup_enabled",False),
            "admin_path":"Azure Bastion" if profile.get("bastion") else "Corporate jump host",
            "local_admin_disabled":_bool(rng,.72),
        }
        if c=="dedicated_host":
            d.update({"host_group":f"hostgrp-{rng.randint(1,9)}","maintenance_control":_bool(rng,.72),"host_fault_domain_count":rng.choice([1,2,3])})
        return d
    if c=="aks":
        return {
            **common,
            "kubernetes_version":_choice(rng,["1.31","1.32","1.33"]),
            "node_os":_choice(rng,["Azure Linux","Ubuntu"]),
            "system_nodes":rng.choice([2,3,3,4]),
            "app_nodes":rng.choice([3,4,6,9]),
            "node_pools":rng.choice([2,2,3,4]),
            "availability_zones":[1,2,3] if profile.get("multi_az") else [1],
            "network_plugin":_choice(rng,["Azure CNI Overlay","Azure CNI Powered by Cilium"]),
            "network_policy":_choice(rng,["Cilium","Azure NPM","None"]),
            "workload_identity":_bool(rng,.86),
            "keyvault_csi":_bool(rng,.76),
            "private_cluster":_bool(rng,.70),
            "acr_private_link":_bool(rng,.72),
            "image_scanning":_bool(rng,.84),
            "admission_policy":_bool(rng,.68),
            "pod_security":"restricted" if _bool(rng,.68) else _choice(rng,["baseline","unconfigured"]),
            "pdb":_bool(rng,.74),
            "replicas":rng.choice([1,2,3,4]),
            "cluster_autoscaler":_bool(rng,.82),
            "auto_upgrade":_bool(rng,.68),
            "backup_extension":_bool(rng,.58),
        }
    if c=="container_apps":
        return {
            **common,
            "environment_type":_choice(rng,["Workload Profiles","Consumption"]),
            "zone_redundant":profile.get("multi_az",False) and _bool(rng,.85),
            "internal_environment":_bool(rng,.64),
            "revisions_mode":_choice(rng,["single","multiple"]),
            "traffic_split":_bool(rng,.52),
            "keda_scaling":_bool(rng,.88),
            "min_replicas":rng.choice([0,1,2]),
            "max_replicas":rng.choice([5,10,20,50]),
            "health_probes":_bool(rng,.88),
            "managed_identity":profile.get("managed_identity",False),
            "keyvault_refs":_bool(rng,.78),
            "dapr":_bool(rng,.35),
            "vnet_integration":_bool(rng,.78),
            "image_registry":_choice(rng,["Azure Container Registry","External registry"]),
            "image_scanning":_bool(rng,.80),
        }
    if c=="app_service":
        return {
            **common,
            "plan":_choice(rng,["Premium v3 P1v3","Premium v3 P2v3","Isolated v2 I1v2"]),
            "zone_redundant":profile.get("multi_az",False) and _bool(rng,.86),
            "deployment_slots":_bool(rng,.82),
            "health_check":_bool(rng,.82),
            "vnet_integration":_bool(rng,.85),
            "private_endpoint":profile.get("private_endpoints",False),
            "managed_identity":profile.get("managed_identity",False),
            "autoscale":_bool(rng,.80),
            "always_on":_bool(rng,.92),
            "tls_min":_choice(rng,["1.2","1.3"]),
        }
    if c=="functions":
        return {
            **common,
            "hosting_plan":_choice(rng,["Flex Consumption","Elastic Premium","Dedicated"]),
            "zone_redundant":profile.get("multi_az",False) and _bool(rng,.72),
            "managed_identity":profile.get("managed_identity",False),
            "private_endpoint":profile.get("private_endpoints",False),
            "vnet_integration":_bool(rng,.76),
            "retry_policy":_choice(rng,["fixed delay","exponential backoff","none"]),
            "dead_letter":_bool(rng,.62),
            "max_concurrency":rng.choice([10,50,100,500]),
            "storage_private":profile.get("private_endpoints",False) and _bool(rng,.82),
        }
    # mixed
    return {
        **common,
        "components":{
            "web":"Azure App Service",
            "api":"AKS",
            "async":"Azure Functions",
            "batch":"Azure VM"
        },
        "component_isolation":_bool(rng,.75),
        "shared_identity":_bool(rng,.18),
        "cross_component_private_network":_bool(rng,.80),
        "centralized_secrets":_bool(rng,.84),
        "centralized_logs":profile.get("logs_to_siem",False),
    }

def _edge_details(profile,rng,difficulty):
    e=profile["edge_pattern"]
    has_waf="waf" in e
    return {
        "pattern":e,
        "services":profile.get("edge_services",[]),
        "waf_present":has_waf,
        "waf_mode":_choice(rng,["Prevention","Detection"]) if has_waf else "None",
        "managed_rules":_bool(rng,.90) if has_waf else False,
        "bot_protection":_bool(rng,.62) if has_waf else False,
        "rate_limiting":_bool(rng,.76) if has_waf else False,
        "geo_filtering":_bool(rng,.32) if has_waf else False,
        "custom_rules":rng.randint(0,8) if has_waf else 0,
        "tls_min":_choice(rng,["1.2","1.3"]),
        "origin_restriction":_choice(rng,["Private Link","Service Tag","Origin secret header","Public unrestricted"]),
        "cdn_cache_ttl_seconds":rng.choice([0,30,60,300,900,3600,86400]) if "cdn" in e else None,
        "cdn_compression":_bool(rng,.75) if "cdn" in e else False,
        "frontdoor_health_probe":_bool(rng,.90) if "frontdoor" in e else False,
    }

def _data_details(profile,rng,difficulty):
    data=profile["data_profile"]
    common={"private_endpoints":profile.get("private_endpoints",False),"encryption_at_rest":True,"cmk":_bool(rng,.42),"auditing":_bool(rng,.82)}
    if data=="transactional":
        return {**common,"sql_zone_redundant":profile.get("multi_az",False) and _bool(rng,.84),"sql_geo_replica":profile.get("multi_region",False) and _bool(rng,.82),
                "sql_failover_group":profile.get("multi_region",False) and _bool(rng,.66),"storage_redundancy":_choice(rng,["ZRS","GRS","RA-GRS","LRS"]),
                "soft_delete":_bool(rng,.88),"immutability":_bool(rng,.44),"redis_zone_redundant":profile.get("multi_az",False) and _bool(rng,.62)}
    if data=="enterprise_sql":
        return {**common,"mi_zone_redundant":profile.get("multi_az",False) and _bool(rng,.80),"failover_group":profile.get("multi_region",False) and _bool(rng,.72),
                "backup_retention_days":rng.choice([7,14,35]),"storage_redundancy":_choice(rng,["ZRS","GRS","RA-GRS"])}
    if data=="postgres":
        return {**common,"postgres_ha":profile.get("multi_az",False) and _bool(rng,.82),"geo_replica":profile.get("multi_region",False) and _bool(rng,.62),
                "backup_retention_days":rng.choice([7,14,35]),"read_replicas":rng.choice([0,1,2,3])}
    if data=="event_driven":
        return {**common,"cosmos_regions":2 if profile.get("multi_region") else 1,"cosmos_auto_failover":profile.get("multi_region",False) and _bool(rng,.86),
                "cosmos_consistency":_choice(rng,["Session","Bounded staleness","Strong"]),
                "service_bus_tier":_choice(rng,["Premium","Standard"]),"service_bus_zone_redundant":profile.get("multi_az",False) and _bool(rng,.78),
                "event_hubs_capture":_bool(rng,.66),"storage_redundancy":_choice(rng,["ZRS","GRS","RA-GRS"])}
    if data=="analytics":
        return {**common,"storage_redundancy":_choice(rng,["ZRS","GRS","RA-GRS"]),"hierarchical_namespace":True,"private_link":profile.get("private_endpoints",False),
                "data_lifecycle_policy":_bool(rng,.72),"cross_region_copy":profile.get("multi_region",False) and _bool(rng,.66)}
    return {**common,"ai_search_replicas":rng.choice([1,2,3,4]),"ai_search_partitions":rng.choice([1,2,3,6]),
            "ai_search_private":profile.get("private_endpoints",False),"cosmos_regions":2 if profile.get("multi_region") else 1,
            "storage_redundancy":_choice(rng,["ZRS","GRS","RA-GRS","LRS"])}

def _network_details(profile,rng,difficulty):
    n=profile["network_profile"]
    # Give every synthetic DGF its own non-overlapping-looking private ranges.
    # For consecutive benchmark seeds the /24s below are unique, preventing
    # accidental cross-case reuse of the same network values.
    seed=int(profile.get("case_seed",profile.get("seed",0)))
    second=16 + ((seed // 16) % 200)
    third=(seed % 16) * 16
    return {
        "profile":n,
        "hub_cidr":f"10.{second}.{third}.0/24",
        "app_cidr":f"10.{second}.{third+1}.0/24",
        "data_cidr":f"10.{second}.{third+2}.0/24",
        "azure_firewall_tier":_choice(rng,["Premium","Standard"]) if profile.get("azure_firewall") else "None",
        "firewall_tls_inspection":profile.get("azure_firewall",False) and _bool(rng,.46),
        "dns_private_resolver":_bool(rng,.64),
        "ddos_plan":profile.get("ddos_protection",False),
        "bastion":profile.get("bastion",False),
        "expressroute":profile.get("expressroute",False),
        "vpn":profile.get("vpn",False),
        "nat_gateway":profile.get("nat_gateway",False),
        "nsg_per_subnet":_bool(rng,.84),
        "udr_forced_tunneling":profile.get("azure_firewall",False) and _bool(rng,.72),
        "private_dns_zones":rng.randint(2,12) if profile.get("private_endpoints") else 0,
    }

def enrich_profile(profile:Dict[str,Any], seed:int, difficulty:int=3) -> Dict[str,Any]:
    rng=random.Random(seed)
    profile=copy.deepcopy(profile)
    previous={d["id"] for d in profile.get("semantic_faults",[])}
    profile["defects"]=[d for d in profile.get("defects",[]) if d["id"] not in previous]
    profile["semantic_compute"]=_compute_details(profile,rng,difficulty)
    profile["semantic_edge"]=_edge_details(profile,rng,difficulty)
    profile["semantic_data"]=_data_details(profile,rng,difficulty)
    profile["semantic_network"]=_network_details(profile,rng,difficulty)
    # Compute-specific hidden faults
    faults=[]
    c=profile["compute_profile"]; cd=profile["semantic_compute"]
    def fault(i,desc,severity="high"):
        faults.append({"id":i,"description":desc,"severity":severity,"layer":"compute"})
    if c in ("vm_single","vm_ha","vmss","dedicated_host"):
        if not cd.get("edr"): fault("vm_no_edr","VM workload has no effective EDR onboarding.")
        if not cd.get("update_manager"): fault("vm_patch_gap","Azure Update Manager / equivalent patch orchestration is not configured.")
        if not cd.get("disk_encryption"): fault("vm_disk_encryption_gap","Managed disks are not using the expected encryption posture.")
        if not cd.get("golden_image"): fault("vm_image_drift","VMs are deployed without a controlled golden-image baseline.","medium")
        if c=="vmss" and profile.get("multi_az") and not cd.get("vmss_zonal"): fault("vmss_not_zonal","VM Scale Set does not actually span availability zones.")
    elif c=="aks":
        if not cd.get("private_cluster"): fault("aks_public_api","AKS API server is publicly reachable.")
        if cd.get("network_policy")=="None": fault("aks_no_network_policy","AKS has no effective network policy.")
        if not cd.get("workload_identity"): fault("aks_identity_gap","Pods use long-lived credentials instead of workload identity.")
        if not cd.get("image_scanning"): fault("aks_image_scan_gap","Container images are not scanned before deployment.")
        if not cd.get("pdb"): fault("aks_no_pdb","Critical workloads have no Pod Disruption Budget.","medium")
        if cd.get("replicas")==1: fault("aks_single_replica","Critical workload runs with a single pod replica.","high")
    elif c=="container_apps":
        if not cd.get("health_probes"): fault("aca_no_health_probe","Container Apps revision has incomplete health probes.")
        if not cd.get("keyvault_refs"): fault("aca_secret_gap","Secrets are stored directly in app configuration rather than referenced from Key Vault.")
        if not cd.get("image_scanning"): fault("aca_image_scan_gap","Container image scanning is not enforced.")
        if profile.get("multi_az") and not cd.get("zone_redundant"): fault("aca_not_zone_redundant","Container Apps environment is not zone redundant.")
    elif c=="app_service":
        if not cd.get("health_check"): fault("appservice_no_healthcheck","App Service health check is not configured.")
        if profile.get("multi_az") and not cd.get("zone_redundant"): fault("appservice_not_zone_redundant","App Service plan is not zone redundant.")
        if not cd.get("deployment_slots"): fault("appservice_no_slots","No deployment slot / safe release mechanism is configured.","medium")
    elif c=="functions":
        if cd.get("retry_policy")=="none": fault("functions_no_retry","Function trigger has no retry policy.")
        if not cd.get("dead_letter"): fault("functions_no_dlq","Failed asynchronous events have no dead-letter path.")
        if not cd.get("storage_private"): fault("functions_storage_public","Function storage path is not private.")
    else:
        if cd.get("shared_identity"): fault("mixed_shared_identity","Mixed platform shares one identity across components.")
        if not cd.get("centralized_logs"): fault("mixed_logging_gap","Mixed platform does not centralize all component logs.")
    ed=profile["semantic_edge"]
    if profile.get("internet_facing") and not ed.get("waf_present"): faults.append({"id":"edge_no_waf","description":"Internet-facing workload has no WAF.","severity":"critical","layer":"edge"})
    if ed.get("waf_present") and ed.get("waf_mode")=="Detection": faults.append({"id":"waf_detection_only","description":"WAF is configured in Detection mode, not Prevention.","severity":"medium","layer":"edge"})
    if ed.get("origin_restriction")=="Public unrestricted": faults.append({"id":"origin_unrestricted","description":"CDN/WAF origin is directly reachable from the Internet.","severity":"high","layer":"edge"})
    dd=profile["semantic_data"]
    if not dd.get("private_endpoints",True): faults.append({"id":"data_public_endpoint","description":"One or more data services use public network access.","severity":"high","layer":"data"})
    profile["semantic_faults"]=faults
    # merge into defect ledger, de-duplicate
    seen={d["id"] for d in profile.get("defects",[])}
    for f in faults:
        if f["id"] not in seen:
            profile.setdefault("defects",[]).append(f); seen.add(f["id"])
    return profile

def apply_overrides(profile:Dict[str,Any], overrides:Dict[str,Any]|None, seed:int, difficulty:int):
    if not overrides: return enrich_profile(profile,seed,difficulty)
    p=copy.deepcopy(profile)
    if overrides.get("compute_profile"):
        p["compute_profile"]=overrides["compute_profile"]; p["compute_services"]=COMPUTE_SERVICE_MAP[p["compute_profile"]]
    if overrides.get("edge_pattern"):
        p["edge_pattern"]=overrides["edge_pattern"]; p["edge_services"]=EDGE_SERVICE_MAP[p["edge_pattern"]]; p["internet_facing"]=p["edge_pattern"]!="private_only"
    if overrides.get("resilience_profile"):
        r=overrides["resilience_profile"]; p["resilience_profile"]=r
        p["multi_region"]=r.startswith("multi_region")
        p["multi_az"]=r in ("single_region_multi_az","multi_region_active_passive","multi_region_active_active")
        p["availability_zones"]=[1,2,3] if p["multi_az"] else [1]
        modes={"single_region_single_az":"No dedicated DR","single_region_multi_az":"Zone-resilient / same-region","multi_region_active_passive":"Warm standby","multi_region_active_active":"Active-active","backup_only":"Backup restore only"}
        p["dr_mode"]=modes[r]
        rng=random.Random(seed+77)
        if p["multi_region"]:
            if not p.get("secondary_region") or p.get("secondary_region")==p.get("primary_region"):
                p["secondary_region"]=rng.choice([x for x in REGIONS if x!=p.get("primary_region")])
            p["data_replication"]=rng.choice(["Geo-replication","GRS/RA-GRS","Cross-region replica","Active geo-replication"])
            p["dr_tested"]=rng.random()<0.65
            p["azure_site_recovery"]=p.get("compute_profile") in ("vm_single","vm_ha","vmss","dedicated_host","mixed") and rng.random()<0.78
        else:
            p["secondary_region"]=None
            p["data_replication"]=rng.choice(["Zone redundant","ZRS","Synchronous zone replica"]) if p["multi_az"] else rng.choice(["LRS only","Single-zone replica"])
            p["dr_tested"]=False
            p["azure_site_recovery"]=False
    if overrides.get("data_profile"):
        p["data_profile"]=overrides["data_profile"]
        p["data_services"]=DATA_SERVICE_MAP[p["data_profile"]]
    if overrides.get("network_profile"): p["network_profile"]=overrides["network_profile"]
    # Rebuild topology defects after changes instead of retaining obsolete facts.
    conditions=[('single_point_compute',p['compute_profile']=='vm_single','Single production compute instance'),
                ('no_waf',p['internet_facing'] and not any('WAF' in s for s in p['edge_services']),'Internet-facing workload has no WAF'),
                ('public_database',not p['private_endpoints'],'Data services expose public endpoints'),
                ('backup_not_restored',p['backup_enabled'] and not p['restore_tested'],'Backup restore not tested'),
                ('no_siem_export',not p['logs_to_siem'],'No SIEM export'),
                ('dr_not_tested',p['multi_region'] and not p['dr_tested'],'Regional failover not tested')]
    p['defects']=[{'id':key,'description':description,'severity':'high'} for key,active,description in conditions if active]
    p['semantic_faults']=[]
    p['inconsistencies']=[]
    from dgf_bench.azure_architecture import architecture_signature
    p["architecture_signature"]=architecture_signature(p)
    return enrich_profile(p,seed,difficulty)

def compute_security_rows(profile):
    c=profile["compute_profile"]; d=profile["semantic_compute"]
    rows=[]
    for k,v in d.items():
        if k=="components": continue
        rows.append((k.replace("_"," ").title(),str(v)))
    return c,rows

def platform_readiness_rows(profile):
    c=profile["compute_profile"]; d=profile["semantic_compute"]
    mapping={
        "vm_single":["os","vm_size","edr","update_manager","jit_access","golden_image","backup_vms","asr_enabled","admin_path","local_admin_disabled"],
        "vm_ha":["os","vm_size","edr","update_manager","availability_set","backup_vms","asr_enabled","admin_path"],
        "vmss":["os","vm_size","vmss_zonal","edr","update_manager","golden_image","backup_vms","asr_enabled"],
        "dedicated_host":["os","vm_size","host_group","maintenance_control","host_fault_domain_count","edr","update_manager","backup_vms"],
        "aks":["kubernetes_version","node_os","system_nodes","app_nodes","node_pools","availability_zones","network_plugin","network_policy","workload_identity","private_cluster","acr_private_link","image_scanning","admission_policy","pod_security","pdb","replicas","cluster_autoscaler","auto_upgrade","backup_extension"],
        "container_apps":["environment_type","zone_redundant","internal_environment","revisions_mode","traffic_split","keda_scaling","min_replicas","max_replicas","health_probes","managed_identity","keyvault_refs","vnet_integration","image_registry","image_scanning"],
        "app_service":["plan","zone_redundant","deployment_slots","health_check","vnet_integration","private_endpoint","managed_identity","autoscale","always_on","tls_min"],
        "functions":["hosting_plan","zone_redundant","managed_identity","private_endpoint","vnet_integration","retry_policy","dead_letter","max_concurrency","storage_private"],
        "mixed":["component_isolation","shared_identity","cross_component_private_network","centralized_secrets","centralized_logs"],
    }
    keys=mapping.get(c,list(d.keys()))
    return [(k.replace("_"," ").title(),str(d.get(k))) for k in keys if k in d]
