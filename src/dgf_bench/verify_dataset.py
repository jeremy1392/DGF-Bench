"""Offline validation of materialized cases and a public-observation baseline."""
import argparse
import hashlib
import json
from pathlib import Path
from dgf_bench.benchmark_protocol import PROTOCOL_VERSION, SCORING_VERSION, facts_id, select_upstream, dataset_fingerprint, source_fingerprint
from dgf_bench.evaluator import evaluate_gate
from dgf_bench.openrouter_eval.benchmark_runner import iter_cases
from dgf_bench.openrouter_eval.public_evidence import PublicEvidenceReader
from dgf_bench.score_submission import score
from dgf_bench.validate_case import validate_case


def public_baseline(case):
    """Uses published facts/policy only. Shared evaluator is not external ground truth."""
    occurrences=json.loads((case/'01_route_manifest.json').read_text(encoding='utf-8'))['occurrences']
    predictions=[]; records={}; history=[]
    for occ in occurrences:
        oid=occ['occurrence_id']; eid=facts_id(occ['gate'])
        observed=PublicEvidenceReader(case,occ['gate'],occ['phase']).read_evidence(eid)
        if observed.get('status')!='OK': raise ValueError('Public snapshot unavailable: '+oid)
        upstream=select_upstream(history,occ['phase'])
        ref=evaluate_gate(observed['content'],occ['gate'],occ['phase'],upstream)
        pred={'occurrence_id':oid,'disposition':ref['disposition'],
              'finding_ids':[f['id'] for f in ref['findings']],
              'actions':list(dict.fromkeys(a['action'] for a in ref['required_actions'])),
              'evidence_refs':[eid],'evidence_support':[],
              'authorization_required':ref['authorization_required'],
              'rationale':'Deterministic public-policy baseline, not a language model.','confidence':1.0}
        events=[{'tool':'read_evidence','args':{'evidence_id':eid},'result':observed}]
        for finding in ref['findings']:
            source='UPSTREAM_DECISIONS' if finding['id'].startswith('GEN-UPSTREAM-') else eid
            content=upstream if source=='UPSTREAM_DECISIONS' else observed['content']
            if source not in pred['evidence_refs']:
                pred['evidence_refs'].append(source)
                events.append({'tool':'read_evidence','args':{'evidence_id':source},
                               'result':{'status':'OK','evidence_id':source,'content':content}})
            pred['evidence_support'].append({'finding_id':finding['id'],'evidence_id':source,
                                            'quote':json.dumps(content,ensure_ascii=False)})
        predictions.append(pred); records[oid]={'tool_trace':events}; history.append(ref)
    return score(case,{'gate_results':predictions},records)


def verify_dataset(dataset):
    dataset=Path(dataset); cases=iter_cases(dataset); errors=[]; gates=0; successful=0
    for case in cases:
        context=json.loads((case/'00_project_context.json').read_text(encoding='utf-8'))
        if context.get('schema_version')!=PROTOCOL_VERSION:
            errors.append({'case':case.name,'errors':['Unsupported protocol']}); continue
        problems=validate_case(case)
        if problems:
            errors.append({'case':case.name,'errors':problems}); continue
        baseline=public_baseline(case); gates+=baseline['expected_gate_count']
        if baseline['route_complete_decision']: successful+=1
        else: errors.append({'case':case.name,'errors':['Public-only baseline failed strict scoring']})
    manifest=dataset/'dataset_manifest.json'
    return {'status':'PASS' if cases and not errors else 'FAIL','case_count':len(cases),
            'public_baseline_complete_cases':successful,'gate_occurrences':gates,
            'errors':errors,'scoring_version':SCORING_VERSION,
            'dataset_fingerprint':dataset_fingerprint(cases),'source_fingerprint':source_fingerprint(),
            'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None,
            'model_api_calls':0,
            'note':'Checks observability, reference consistency and scoring. Uses the same public policy evaluator as the oracle; not independent business-rule validation.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); report=verify_dataset(args.dataset)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report['status']=='PASS' else 1)


if __name__=='__main__': main()
