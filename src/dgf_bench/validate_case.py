#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, csv
from pathlib import Path
from dgf_bench.benchmark_protocol import PROTOCOL_VERSION, facts_id, factual_snapshot, select_upstream
from dgf_bench.evaluator import evaluate_gate
from dgf_bench.openrouter_eval.public_evidence import PublicEvidenceReader

def validate_case(c, require_certified=False):
    """Structural and policy checks. require_certified adds the docs-condition requirement that every
    scheduled gate is decidable from authoritative public sources (see dgf_bench.certification)."""
    c=Path(c).resolve()
    errors=[]
    for req in ['00_project_context.json','01_route_manifest.json','02_evidence_graph.json','03_tool_schemas.json','04_gate_contracts.json','05_phase_visibility.json','99_hidden_ground_truth.json','agent_submission_template.json']:
        if not (c/req).exists(): errors.append(f'missing {req}')
    if not errors:
        gt=json.loads((c/'99_hidden_ground_truth.json').read_text(encoding='utf-8')); route=json.loads((c/'01_route_manifest.json').read_text(encoding='utf-8')); public=json.loads((c/'02_evidence_graph.json').read_text(encoding='utf-8'))
        if len(gt['reference_decisions'])!=len(route['occurrences']): errors.append('reference decision count mismatch')
        ids={n['evidence_id'] for n in gt['evidence_graph']['nodes']}; pids={n['evidence_id'] for n in public['nodes']}
        if ids!=pids: errors.append('public/hidden evidence IDs mismatch')
        for n in public['nodes']:
            if c not in (c/n['path']).resolve().parents:
                errors.append('Evidence path escapes the case'); continue
            if n['public_status']=='AVAILABLE' and not (c/n['path']).exists(): errors.append(f"available evidence missing: {n['evidence_id']} -> {n['path']}")
            if n['public_status']=='UNAVAILABLE' and (c/n['path']).exists(): errors.append(f"unavailable evidence unexpectedly present: {n['evidence_id']}")
        context=json.loads((c/'00_project_context.json').read_text(encoding='utf-8'))
        if context.get('schema_version')==PROTOCOL_VERSION:
            refs={r['occurrence_id']:r for r in gt['reference_decisions']}; history=[]
            occurrences=route['occurrences']; ids=[o['occurrence_id'] for o in occurrences]
            if len(ids)!=len(set(ids)) or set(ids)!=set(refs): errors.append('Occurrence identity mismatch')
            contracts=json.loads((c/'04_gate_contracts.json').read_text(encoding='utf-8'))
            for occurrence in occurrences:
                gate,phase,oid=occurrence['gate'],occurrence['phase'],occurrence['occurrence_id']
                observed=PublicEvidenceReader(c,gate,phase).read_evidence(facts_id(gate))
                if observed.get('status')!='OK':
                    errors.append(f'Missing observable facts for {oid}'); continue
                if observed['content']!=factual_snapshot(gt['canonical_truth'],gate): errors.append(f'Factual source mismatch for {oid}')
                if contracts.get(gate,{}).get('decision_policy',{}).get('version')!=PROTOCOL_VERSION:
                    errors.append(f'Missing decision policy for {gate}')
                result=evaluate_gate(observed['content'],gate,phase,select_upstream(history,phase))
                ref=refs.get(oid,{})
                for key in ('disposition','findings','required_actions','authorization_required'):
                    if result[key]!=ref.get(key): errors.append(f'Public policy/observations do not reproduce {oid}: {key}')
                history.append(result)
            with (c/'shared/action_register.csv').open(encoding='utf-8',newline='') as f:
                if any(row.get('occurrence_id') in refs for row in csv.DictReader(f)):
                    errors.append('Current/future reference decision leaked into initial action register')
        if require_certified and not errors:
            from dgf_bench.certification import certify_case
            for row in certify_case(c):
                if row['status']!='MATCH':
                    errors.append(f"Decisive fact not established from authoritative sources: {row['occurrence_id']} {row['field']} ({row['status']})")
    return errors

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--case',type=Path,required=True)
    ap.add_argument('--require-certified',action='store_true',help='Also require the docs/attack determinability certification')
    ns=ap.parse_args()
    errors=validate_case(ns.case,ns.require_certified)
    print(json.dumps({'status':'PASS' if not errors else 'FAIL','errors':errors},indent=2))
    raise SystemExit(1 if errors else 0)
if __name__=='__main__': main()
