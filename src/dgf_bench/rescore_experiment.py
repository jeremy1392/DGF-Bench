"""Offline, non-destructive scoring reanalysis of a recorded v8 experiment."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from dgf_bench.benchmark_protocol import PROTOCOL_VERSION, SCORING_VERSIONS, source_fingerprint
from dgf_bench.openrouter_eval.aggregate import aggregate
from dgf_bench.openrouter_eval.benchmark_runner import _checkpoint_records, iter_cases
from dgf_bench.run_full_experiment import collect_paper_metrics, write_paper_outputs
from dgf_bench.score_submission import score


def rescore_experiment(experiment, output):
    experiment, output=Path(experiment).resolve(),Path(output).resolve()
    if experiment==output or experiment in output.parents or output in experiment.parents:
        raise ValueError('Source experiment and reanalysis output must be disjoint')
    if output.exists(): raise FileExistsError('Reanalysis output must be new')
    config=json.loads((experiment/'experiment_config.json').read_text(encoding='utf-8'))
    if config.get('schema')!=PROTOCOL_VERSION: raise ValueError(f'Only {PROTOCOL_VERSION} observations may be rescored')
    condition=config.get('information_condition','facts')
    scoring_version=SCORING_VERSIONS[condition]
    dataset=Path(config.get('dataset_path',experiment/'dataset'))
    cases={case.name:case for case in iter_cases(dataset)}
    output.mkdir(parents=True); results=output/'results'; results.mkdir()
    hashes={}
    def remember(path):
        hashes[str(path.relative_to(experiment))]=hashlib.sha256(path.read_bytes()).hexdigest()
    remember(experiment/'experiment_config.json')
    original_results=experiment/'results'
    manifest=original_results/'benchmark_manifest.json'
    if manifest.exists():
        remember(manifest); shutil.copyfile(manifest,results/manifest.name)
    for path in sorted(original_results.glob('*/*/score.json')):
        remember(path); old=json.loads(path.read_text(encoding='utf-8'))
        updated=dict(old)
        if old.get('status') in ('OK','AGENT_FAILURE'):
            records,_=_checkpoint_records(path.parent)
            for trace in path.parent.glob('[0-9][0-9]_*.json'): remember(trace)
            predictions=[r['result'] for r in records.values()]
            updated.update(score(cases[path.parent.name],{'gate_results':predictions},records,condition=condition))
        updated['original_scoring_version']=old.get('scoring_version')
        updated['reanalysis_source']=str(path)
        target=results/path.relative_to(original_results); target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding='utf-8')
    # Preserve recorded billing, including jobs interrupted before score.json.
    for ledger in original_results.glob('*/*/usage_ledger.jsonl'):
        remember(ledger); target=results/ledger.relative_to(original_results)
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ledger,target)
    report=output/'paper_outputs'; report.mkdir()
    aggregated=aggregate(results)
    (report/'aggregate.json').write_text(json.dumps(aggregated,ensure_ascii=False,indent=2),encoding='utf-8')
    config.update(scoring_version=scoring_version,reanalysis_source=str(experiment),analysis_only=True,
                  note='Scoring-only reanalysis. Original provider failures remain excluded; no answers were regenerated.')
    (output/'experiment_config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    overall,gates=collect_paper_metrics(results,aggregated); write_paper_outputs(report,overall,gates,config)
    (output/'reanalysis_provenance.json').write_text(json.dumps({
        'source':str(experiment),'scoring_version':scoring_version,'source_code_fingerprint':source_fingerprint(),
        'source_file_sha256':hashes,'api_calls':0,'original_failure_classifications_preserved':True},indent=2),encoding='utf-8')
    # Detect accidental modification during processing rather than silently
    # publishing a reanalysis of an experiment that was still running.
    for relative,digest in hashes.items():
        if hashlib.sha256((experiment/relative).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Source changed during reanalysis: '+relative)
    return overall


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    rows=rescore_experiment(args.experiment,args.output)
    print(json.dumps([{k:r[k] for k in ('model','cases','gate_csr','route_complete_rate','false_approvals','total_cost_usd')} for r in rows],indent=2))


if __name__=='__main__': main()
