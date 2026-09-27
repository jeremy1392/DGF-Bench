#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from dgf_bench.azure_architecture import architecture_signature
from dgf_bench.facts_engine import generate_canonical_case
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.dataset_diversity import audit_canonical, write_report


def sha256(path: Path):
    h = hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()


def canonical_sha(case: dict[str, Any]) -> str:
    raw = json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _assert_unique(label: str, values: list[Any]) -> dict[str, Any]:
    normalized = [json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (dict, list, tuple)) else str(v) for v in values]
    seen = {}
    duplicates = []
    for i, v in enumerate(normalized):
        if v in seen:
            duplicates.append({"value": v, "first_index": seen[v], "duplicate_index": i})
        else:
            seen[v] = i
    return {
        "field": label,
        "count": len(values),
        "unique_count": len(seen),
        "ok": len(duplicates) == 0,
        "duplicates": duplicates,
    }


def build_uniqueness_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    checks = []
    checks.append(_assert_unique("case_id", [x["case_id"] for x in cases]))
    checks.append(_assert_unique("project_id", [x["project"]["project_id"] for x in cases]))
    checks.append(_assert_unique("project_name", [x["project"]["project_name"] for x in cases]))
    checks.append(_assert_unique("project_code", [x["project"]["project_code"] for x in cases]))
    checks.append(_assert_unique("architecture_id", [x["architecture"]["architecture_id"] for x in cases]))
    checks.append(_assert_unique("architecture_signature", [x["architecture_signature"] for x in cases]))
    checks.append(_assert_unique("resource_prefix", [x["architecture"]["resource_prefix"] for x in cases]))
    checks.append(_assert_unique("canonical_truth_sha256", [x["canonical_sha256"] for x in cases]))
    checks.append(_assert_unique("contract_version", [x["legal_contract_version"] for x in cases]))
    checks.append(_assert_unique("hld_version", [x["hld_version"] for x in cases]))
    checks.append(_assert_unique("lld_version", [x["lld_version"] for x in cases]))

    all_people = []
    all_vendors = []
    all_cidrs = []
    for x in cases:
        all_people += list(x["people"])
        all_vendors += list(x["vendors"])
        all_cidrs += list(x["cidrs"])
    checks.append(_assert_unique("all_named_people_across_dataset", all_people))
    checks.append(_assert_unique("all_vendor_names_across_dataset", all_vendors))
    checks.append(_assert_unique("all_private_cidrs_across_dataset", all_cidrs))

    return {
        "schema": "DGF-Bench-Uniqueness-v1",
        "case_count": len(cases),
        "all_checks_passed": all(c["ok"] for c in checks),
        "checks": checks,
        "note": (
            "Case-specific identities/configurations are unique. Controlled benchmark categories such as route, gate type, "
            "Azure product names, dispositions, booleans and policy states intentionally remain reusable; prohibiting those "
            "repetitions would make cross-case comparison impossible."
        ),
    }



def _materialize(job):
    output,row=job
    cdir=build_case(Path(output),row['seed'],row['difficulty'],row['route'],
                    architecture_attempt=row['architecture_attempt'],fact_attempts=row['fact_attempts'])
    hidden=json.loads((cdir/'99_hidden_ground_truth.json').read_text(encoding='utf-8'))['canonical_truth']
    hidden.pop('case_id',None)
    from dgf_bench.benchmark_protocol import json_hash
    if json_hash(hidden)!=row['canonical_sha256']:
        raise RuntimeError('Materialized facts differ from sampling plan: '+cdir.name)
    return str(cdir)


def prepare_dataset(output,plan,workers=1):
    from concurrent.futures import ProcessPoolExecutor
    output=Path(output)
    if workers<1: raise ValueError('generation workers must be positive')
    if output.exists() and any(output.iterdir()): raise FileExistsError('Dataset directory must be new or empty')
    output.mkdir(parents=True,exist_ok=True)
    (output/'sampling_plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8', newline='\n')
    jobs=[(str(output),row) for row in plan['cases']]
    rows=[]; audit_cases=[]; canonical_cases=[]
    pool=ProcessPoolExecutor(max_workers=workers) if workers>1 else None
    try:
        results=pool.map(_materialize,jobs) if pool else map(_materialize,jobs)
        for index,(row,name) in enumerate(zip(plan['cases'],results),start=1):
            cdir=Path(name)
            print(f"[dataset] [{index}/{len(jobs)}] {cdir.name} done",flush=True)
            ctx=json.loads((cdir/'00_project_context.json').read_text(encoding='utf-8'))
            hidden=json.loads((cdir/'99_hidden_ground_truth.json').read_text(encoding='utf-8'))['canonical_truth']
            canonical_cases.append(hidden)
            arch=hidden['architecture_profile']; project=hidden['project']; case_id=ctx['case_id']
            rows.append({**row,'case_dir':cdir.name,'case_id':case_id,
                         'project_id':project['project_id'],'project_name':project['project_name'],
                         'public_context_sha256':sha256(cdir/'00_project_context.json')})
            audit_cases.append({
                'case_id':case_id,'project':project,'architecture':arch,
                'architecture_signature':row['architecture_signature'],'canonical_sha256':canonical_sha(hidden),
                'people':[project['sponsor'],project['project_manager'],project['business_owner'],project['service_owner'],project['risk_owner'],hidden['general']['benefits_owner']],
                'vendors':[o['vendor'] for o in hidden['procurement']['offers']],
                'cidrs':list(hidden['architecture']['private_cidrs']),
                'legal_contract_version':hidden['legal']['contract_version'],
                'hld_version':hidden['architecture']['hld_version'],'lld_version':hidden['architecture']['lld_version']})
    finally:
        if pool: pool.shutdown(wait=True,cancel_futures=True)
    report=build_uniqueness_report(audit_cases)
    (output/'dataset_uniqueness_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8', newline='\n')
    if not report['all_checks_passed']:
        raise RuntimeError('Dataset uniqueness validation failed: '+', '.join(c['field'] for c in report['checks'] if not c['ok']))
    diversity=audit_canonical(canonical_cases)
    diversity['sampling_policy']=plan['policy']; diversity['sampling_version']=plan['sampling_version']
    write_report(diversity,output/'dataset_diversity_report.json')
    if diversity['concentrated_gates']:
        print('[dataset] decision concentration above 80%: '+', '.join(diversity['concentrated_gates']),flush=True)
    manifest={'schema':'DGF-Bench-OpenRouter-Dataset-v8','uniqueness_enforced':True,
              'architecture_signature_uniqueness':True,'uniqueness_report':'dataset_uniqueness_report.json',
              'diversity_report':'dataset_diversity_report.json','sampling_policy':plan['policy'],
              'sampling_version':plan['sampling_version'],'sampling_plan':'sampling_plan.json','cases':rows}
    (output/'dataset_manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8', newline='\n')
    print(json.dumps({'output_dir':str(output),'case_count':len(rows),'sampling_policy':plan['policy'],
                      'unique_architecture_signatures':len({r['architecture_signature'] for r in rows}),
                      'uniqueness_checks_passed':True,'concentrated_gates':diversity['concentrated_gates']},indent=2))
    return manifest


def main():
    from dgf_bench.dataset_sampling import build_plan
    ap=argparse.ArgumentParser(description='Create a balanced-by-route DGF-Bench dataset with an explicit sampling policy')
    ap.add_argument('--cases-per-route',type=int,default=20)
    ap.add_argument('--seed',type=int,default=9000)
    ap.add_argument('--difficulty',type=int,choices=range(1,6),default=4)
    ap.add_argument('--include-full-lifecycle',action='store_true')
    ap.add_argument('--output-dir',type=Path,default=Path('openrouter_dataset'))
    ap.add_argument('--sampling-policy',choices=['natural','balanced'],default='natural')
    ap.add_argument('--generation-workers',type=int,default=1,help='Local document generation processes; does not call models')
    ns=ap.parse_args()
    if ns.cases_per_route<1 or ns.generation_workers<1: ap.error('Counts/workers must be positive')
    if ns.output_dir.exists() and any(ns.output_dir.iterdir()): ap.error('Dataset directory must be new or empty')
    routes=['buy','integrate','build']+(['full_lifecycle'] if ns.include_full_lifecycle else [])
    if ns.sampling_policy=='balanced' and ns.include_full_lifecycle: ap.error('Balanced coverage currently supports the three main routes only')
    print(f'[dataset] planning {len(routes)*ns.cases_per_route} cases, policy={ns.sampling_policy}',flush=True)
    plan=build_plan(ns.cases_per_route,ns.seed,ns.difficulty,routes,ns.sampling_policy)
    prepare_dataset(ns.output_dir,plan,ns.generation_workers)


if __name__=='__main__': main()
