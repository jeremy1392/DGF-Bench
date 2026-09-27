from __future__ import annotations
import argparse, json, statistics
from collections import defaultdict
from pathlib import Path

SCOREABLE_STATUSES={"OK","AGENT_FAILURE"}
ATTACK_COUNTS=('gates','injection_observed','attack_success','attack_success_given_observed','canary_in_rationale',
               'placebo_gates','placebo_observed','placebo_canary_in_rationale','forged_authority_executed')


def _sum_attack(summaries):
    total={k:sum(a.get(k,0) for a in summaries) for k in ATTACK_COUNTS}
    for group in ('by_goal','by_family'):
        keys=sorted({g for a in summaries for g in a.get(group,{})})
        total[group]={g:{k:sum(a.get(group,{}).get(g,{}).get(k,0) for a in summaries)
                         for k in ('gates','injection_observed','attack_success','attack_success_given_observed','canary_in_rationale')}
                      for g in keys}
    return total


def _planned_gates(manifest):
    """Scheduled gates per case of the benchmarked dataset, so failures can count against the rates."""
    dataset=manifest.get('dataset')
    if not dataset or not Path(dataset).exists(): return {}
    from .benchmark_runner import iter_cases
    cases=iter_cases(Path(dataset))[:manifest.get('case_count') or None]
    return {c.name:len(json.loads((c/'01_route_manifest.json').read_text(encoding='utf-8'))['occurrences']) for c in cases}


def aggregate(root:Path):
    root=Path(root); rows=[]; gate_rows=[]; excluded=[]
    accounting=defaultdict(lambda: {'planned_cases':0,'excluded_cases':0,'total_cost_usd':0.0,'unknown_cost_calls':0})
    versions=set(); conditions=set()
    for score_path in root.glob("*/*/score.json"):
        sc=json.loads(score_path.read_text(encoding="utf-8"))
        status=sc.get("status")
        versions.add(sc.get('scoring_version','legacy') if status in SCOREABLE_STATUSES else None)
        model=sc.get("model") or sc.get("requested_model") or score_path.parents[1].name
        case=score_path.parent.name
        account=accounting[model]; account['planned_cases']+=1
        ledger=score_path.parent/'usage_ledger.jsonl'
        paid=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip()] if ledger.exists() else [sc.get('openrouter_usage') or {}]
        account['total_cost_usd']+=sum(float(u.get('cost',0) or 0) for u in paid)
        account['unknown_cost_calls']+=sum(int(u.get('unknown_cost_calls',0) or 0) for u in paid)
        if status not in SCOREABLE_STATUSES:
            account['excluded_cases']+=1
            excluded.append({"model":model,"case":case,"status":status,"error":sc.get("error")})
            continue
        u=sc.get("openrouter_usage") or {}
        conditions.add(sc.get('information_condition','facts'))
        rows.append({"model":model,"case":case,"status":status,"overall_score":sc.get("overall_score",0),
                     "strict_gate_success_rate":sc.get("strict_gate_success_rate",0),
                     "outcome_strict_success_rate":sc.get("outcome_strict_success_rate",0),
                     "outcome_successes":sc.get("outcome_strict_success_count",0),
                     "route_complete_outcome":bool(sc.get("route_complete_outcome",False)),
                     "attack":sc.get("attack"),
                     "route_complete_execution":bool(sc.get("route_complete_decision",sc.get("route_complete_execution",False))),
                     "critical_miss_count":sc.get("critical_miss_count",0),"false_approval_count":sc.get("false_approval_count",0),
                     "cost":u.get("cost",0),"tokens":u.get("total_tokens",0),
                     "truncated_responses":sc.get("truncated_response_count",0),
                     "gate_attempt_rate":sc.get("gate_attempt_rate",1.0),
                     "model_resolution_mismatch":bool(sc.get("model_resolution_mismatch",False))})
        for o in sc.get("occurrences",[]):
            gate_rows.append({"model":model,"case":case,"gate":o.get("gate"),"attempted":bool(o.get("attempted",True)),
                              "score":o.get("score",0),"strict_success":bool(o.get("strict_success",False)),
                              "outcome_strict":bool(o.get("outcome_strict",False)),
                              "decision":o.get("decision",0),"findings_f1":o.get("findings_f1",0),
                              "actions_f1":o.get("actions_f1",0),"evidence_fidelity":o.get("evidence_fidelity",0),
                              "authorization":o.get("authorization",0),"attack":o.get("attack")})
    versions.discard(None)
    if len(versions)>1: raise ValueError('Cannot aggregate incompatible scoring versions')
    if len(conditions)>1: raise ValueError('One results directory holds one information condition')
    manifest_path=root/'benchmark_manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    planned_gates=_planned_gates(manifest)
    from .benchmark_runner import slug
    known_models={slug(model):model for model in manifest.get('models',[])}
    for ledger in root.glob('*/*/usage_ledger.jsonl'):
        if (ledger.parent/'score.json').exists(): continue
        model=known_models.get(ledger.parent.parent.name,ledger.parent.parent.name)
        account=accounting[model]; account['planned_cases']+=1; account['excluded_cases']+=1
        paid=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip()]
        account['total_cost_usd']+=sum(float(u.get('cost',0) or 0) for u in paid)
        account['unknown_cost_calls']+=sum(int(u.get('unknown_cost_calls',0) or 0) for u in paid)
        excluded.append({'model':model,'case':ledger.parent.name,'status':'INCOMPLETE_CHECKPOINT','error':'Paid responses recorded before a score was written'})
    for model in manifest.get('models',[]):
        account=accounting[model]
        account['recorded_cases']=account['planned_cases']
        account['planned_cases']=max(account['planned_cases'],manifest.get('case_count',0))
        account['unscheduled_cases']=account['planned_cases']-account['recorded_cases']
    by_model=defaultdict(list)
    for r in rows: by_model[r["model"]].append(r)
    models=[]
    for m,rs in by_model.items():
        models.append({"model":m,"cases":len(rs),"completed_cases":sum(x["status"]=="OK" for x in rs),
                       "agent_failure_cases":sum(x["status"]=="AGENT_FAILURE" for x in rs),
                       "mean_score":round(statistics.mean(x["overall_score"] for x in rs),4),
                       "mean_gate_csr":round(statistics.mean(x["strict_gate_success_rate"] for x in rs),4),
                       "mean_outcome_csr":round(statistics.mean(x["outcome_strict_success_rate"] for x in rs),4),
                       "route_outcome_rate":round(statistics.mean(1.0 if x["route_complete_outcome"] else 0.0 for x in rs),4),
                       "mean_gate_attempt_rate":round(statistics.mean(x["gate_attempt_rate"] for x in rs),4),
                       "route_complete_rate":round(statistics.mean(1.0 if x["route_complete_execution"] else 0.0 for x in rs),4),
                       "critical_misses":sum(x["critical_miss_count"] for x in rs),
                       "false_approvals":sum(x["false_approval_count"] for x in rs),
                       "truncated_responses":sum(x["truncated_responses"] for x in rs),
                       "model_resolution_mismatches":sum(x["model_resolution_mismatch"] for x in rs),
                       "total_cost_usd":round(sum(x["cost"] for x in rs),6),"total_tokens":sum(x["tokens"] for x in rs),
                       **({"attack":_sum_attack([x["attack"] for x in rs if x["attack"]])} if any(x["attack"] for x in rs) else {})})
    present={m['model']:m for m in models}
    for model, account in accounting.items():
        if model not in present:
            models.append({'model':model,'cases':0,'completed_cases':0,'agent_failure_cases':0,
                           'mean_score':None,'mean_gate_csr':None,'route_complete_rate':None,'total_cost_usd':0})
            present[model]=models[-1]
        row=present[model]
        row['scoreable_cost_usd']=row['total_cost_usd']
        row.update(account)
        row['availability_rate']=row['cases']/account['planned_cases'] if account['planned_cases'] else None
        relevant=[g for g in gate_rows if g['model']==model]
        row['gate_csr_micro']=sum(g['strict_success'] for g in relevant)/len(relevant) if relevant else None
        row['mean_gate_csr_macro']=row['mean_gate_csr']
        row['outcome_csr_micro']=sum(g['outcome_strict'] for g in relevant)/len(relevant) if relevant else None
        # Primary v9 rates count every planned gate and route; excluded runs (infrastructure errors,
        # context overflow, budget stops) are failures, not missing data.
        planned=planned_gates
        if planned:
            scored={r['case']:r for r in rows if r['model']==model}
            row['planned_gates']=sum(planned.values())
            row['outcome_success_all_planned']=sum(r['outcome_successes'] for r in scored.values())
            row['outcome_rate_all_planned']=row['outcome_success_all_planned']/row['planned_gates']
            row['route_outcome_all_planned_rate']=sum(r['route_complete_outcome'] for r in scored.values())/len(planned)
    by_gate=defaultdict(list)
    for r in gate_rows: by_gate[(r["model"],r["gate"])].append(r)
    gates=[]
    for (m,g),rs in by_gate.items():
        attempted=[x for x in rs if x["attempted"]]
        gates.append({"model":m,"gate":g,"n_expected":len(rs),"n_attempted":len(attempted),
                      "attempt_rate":round(len(attempted)/len(rs),4) if rs else 0.0,
                      "csr":round(statistics.mean(1.0 if x["strict_success"] else 0.0 for x in rs),4),
                      "conditional_csr_attempted":round(statistics.mean(1.0 if x["strict_success"] else 0.0 for x in attempted),4) if attempted else 0.0,
                      "mean_score":round(statistics.mean(x["score"] for x in rs),4),
                      "decision_accuracy":round(statistics.mean(x["decision"] for x in attempted),4) if attempted else 0.0,
                      "findings_f1":round(statistics.mean(x["findings_f1"] for x in attempted),4) if attempted else 0.0,
                      "actions_f1":round(statistics.mean(x["actions_f1"] for x in attempted),4) if attempted else 0.0,
                      "evidence_fidelity":round(statistics.mean(x["evidence_fidelity"] for x in attempted),4) if attempted else 0.0,
                      "authorization_accuracy":round(statistics.mean(x["authorization"] for x in attempted),4) if attempted else 0.0})
    cohorts=[{r['case'] for r in rows if r['model']==model} for model in accounting]
    common=set.intersection(*cohorts) if cohorts else set()
    paired=[]
    for model in accounting:
        gs=[r for r in gate_rows if r['model']==model and r['case'] in common]
        rs=[r for r in rows if r['model']==model and r['case'] in common]
        paired.append({'model':model,'common_cases':len(common),'common_gate_count':len(gs),
                       'common_gate_csr':sum(g['strict_success'] for g in gs)/len(gs) if gs else None,
                       'common_false_approvals':sum(r['false_approval_count'] for r in rs)})
    return {'scoring_version':next(iter(versions),None),'information_condition':next(iter(conditions),None),"models":sorted(models,key=lambda x:x["model"]),"gates":sorted(gates,key=lambda x:(x["model"],x["gate"])),
            "excluded_runs":excluded,'common_case_ids':sorted(common),'paired_models':paired}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--results",type=Path,required=True); ap.add_argument("--output",type=Path,default=None)
    ns=ap.parse_args(); out=aggregate(ns.results)
    target=ns.output or ns.results/"aggregate.json"; target.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps(out,indent=2))
if __name__=="__main__": main()
