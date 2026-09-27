"""Decision-relevant diversity; project names and UUIDs never count as entropy."""
from __future__ import annotations
import argparse
import ast
import json
import math
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from dgf_bench.benchmark_protocol import json_hash
from dgf_bench.evaluator import evaluate_route
from dgf_bench.routes import build_occurrences


def entropy(counts):
    n = sum(counts.values())
    return -sum((v/n)*math.log2(v/n) for v in counts.values() if v) if n else 0.0


def _possible(condition, phase):
    if isinstance(condition, ast.BoolOp):
        values = [_possible(x,phase) for x in condition.values]
        return all(values) if isinstance(condition.op,ast.And) else any(values)
    try:
        return bool(eval(compile(ast.fix_missing_locations(ast.Expression(condition)),'<phase predicate>','eval'),
                         {'__builtins__':{}}, {'phase':phase}))
    except (NameError, TypeError, KeyError):
        return True  # Data-dependent: potentially applicable, not guaranteed.


@lru_cache(maxsize=None)
def rule_metadata(gate, phase):
    tree=ast.parse(Path(__file__).with_name('evaluator.py').read_text(encoding='utf-8'))
    name='evaluate_'+('tech' if gate=='tech_readiness' else gate)
    func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    fields=set(); rules={}
    for branch in ast.walk(func):
        if isinstance(branch,ast.If):
            for sub in ast.walk(branch.test):
                if isinstance(sub,ast.Subscript) and isinstance(sub.value,ast.Name) and isinstance(sub.slice,ast.Constant) and isinstance(sub.slice.value,str):
                    fields.add((sub.value.id,sub.slice.value))
    def visit(node, conditions):
        if isinstance(node,ast.If):
            for child in node.body: visit(child,conditions+[node.test])
            for child in node.orelse: visit(child,conditions+[ast.UnaryOp(op=ast.Not(),operand=node.test)])
            return
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='finding':
            fid=ast.literal_eval(node.args[0])
            disposition=ast.literal_eval(node.args[6]) if len(node.args)>6 else 'REWORK'
            rules[fid]={'disposition':disposition,'phase_applicable':all(_possible(c,phase) for c in conditions)}
        for child in ast.iter_child_nodes(node): visit(child,conditions)
    visit(func,[])
    return fields,rules


def decision_facts(case, reference):
    """Only fields used in decision predicates; exclude IDs, names and dates."""
    gate,phase=reference['gate'],reference['phase']
    aliases={'f':case[gate],'p':case['project'],'a':case['architecture_profile']}
    if gate=='procurement':
        procurement=case['procurement']
        aliases['sel']=next(x for x in procurement['offers'] if x['vendor']==procurement['selected_vendor'])
    facts={f'{alias}.{field}':aliases[alias][field] for alias,field in sorted(rule_metadata(gate,phase)[0])
           if alias in aliases and field in aliases[alias]}
    if gate=='general':
        # Collapse irrelevant ordering/identity while retaining the decisions
        # that General actually has to consolidate.
        facts['upstream_dispositions']=dict(sorted(Counter(x['disposition'] for x in reference.get('upstream_context',[])).items()))
    return facts


def _summarize(rows):
    n=len(rows); decisions=Counter(r['reference']['disposition'] for r in rows)
    patterns=Counter(json.dumps(sorted(f['id'] for f in r['reference']['findings'])) for r in rows)
    facts=Counter(json_hash({'phase':r['reference']['phase'],'facts':r['facts']}) for r in rows)
    active=set(); all_rules=set(); possible={'GO'}; seen=Counter()
    for row in rows:
        reference=row['reference']; rules=rule_metadata(reference['gate'],reference['phase'])[1]
        all_rules.update(rules)
        active.update(fid for fid,m in rules.items() if m['phase_applicable'])
        possible.update(m['disposition'] for m in rules.values() if m['phase_applicable'])
        seen.update(f['id'] for f in reference['findings'])
    fields=defaultdict(Counter)
    for row in rows:
        for key,value in row['facts'].items(): fields[key][json.dumps(value,sort_keys=True)]+=1
    majority=max(decisions.values())/n
    phase_groups=defaultdict(list)
    for row in rows: phase_groups[row['reference']['phase']].append(row)
    conditional_entropy=0.0; conditional_max=0.0
    for phase,group in phase_groups.items():
        eligible=rule_metadata(group[0]['reference']['gate'],phase)[1]
        possibilities={'GO'}|{m['disposition'] for m in eligible.values() if m['phase_applicable']}
        weight=len(group)/n
        conditional_entropy+=weight*entropy(Counter(r['reference']['disposition'] for r in group))
        conditional_max+=weight*math.log2(len(possibilities))
    return {'occurrences':n,'independent_cases':len({r['case_key'] for r in rows}),
            'decision_counts':dict(sorted(decisions.items())),
            'decision_entropy_bits':round(entropy(decisions),4),
            'decision_entropy_normalized':round(entropy(decisions)/math.log2(len(possible)),4) if len(possible)>1 else 0,
            'phase_adjusted_decision_entropy_normalized':round(conditional_entropy/conditional_max,4) if conditional_max else 0,
            'dominant_decision_share':round(majority,4),
            'majority_class_baseline':round(majority,4),
            'potential_dispositions':sorted(possible),'unobserved_dispositions':sorted(possible-set(decisions)),
            'unique_relevant_fact_signatures':len(facts),'largest_fact_duplicate_group':max(facts.values()),
            'unique_finding_patterns':len(patterns),'finding_pattern_entropy_bits':round(entropy(patterns),4),
            'finding_occurrence_counts':dict(sorted(seen.items())),
            'uncovered_phase_applicable_rules':sorted(active-set(seen)),
            'phase_inapplicable_rules':sorted(all_rules-active),
            'relevant_field_variation':{key:{'unique_values':len(counts),'entropy_bits':round(entropy(counts),4)} for key,counts in sorted(fields.items())},
            'warnings':(['dominant_decision_above_80_percent'] if majority>.8 else [])
                       + (['fewer_than_30_independent_cases'] if len({r['case_key'] for r in rows})<30 else [])}


def audit_canonical(cases):
    by_gate=defaultdict(list); by_route_gate=defaultdict(list); routes=Counter(); identities=[]; semantic_cases=[]
    architecture=[]; case_patterns=[]
    from dgf_bench.azure_architecture import architecture_signature
    for case in cases:
        route=case['project']['route']; routes[route]+=1
        identity=case['project']['project_id']; identities.append(identity)
        architecture.append(architecture_signature(case['architecture_profile']))
        case_facts=[]; case_pattern=[]
        for ref in evaluate_route(case,build_occurrences(route)):
            facts=decision_facts(case,ref); case_facts.append({'gate':ref['gate'],'phase':ref['phase'],'facts':facts})
            case_pattern.append((ref['gate'],ref['phase'],sorted(f['id'] for f in ref['findings'])))
            row={'case_key':identity,'reference':ref,'facts':facts}
            by_gate[ref['gate']].append(row); by_route_gate[(route,ref['gate'])].append(row)
        semantic_cases.append(json_hash(case_facts))
        case_patterns.append(json_hash(case_pattern))
    n=len(identities)
    if not n: raise ValueError('Cannot audit an empty dataset')
    gates={gate:_summarize(rows) for gate,rows in sorted(by_gate.items())}
    return {'schema':'DGF-Diversity-v1','case_count':n,'routes':dict(routes),
            'unique_project_ids':len(set(identities)), 'unique_architecture_signatures':len(set(architecture)),
            'unique_whole_case_decision_fact_signatures':len(set(semantic_cases)),
            'unique_whole_case_finding_signatures':len(set(case_patterns)),
            'gates':gates,'by_route_gate':{route+'/'+gate:_summarize(rows) for (route,gate),rows in sorted(by_route_gate.items())},
            'concentrated_gates':[gate for gate,row in gates.items() if row['dominant_decision_share']>.8],
            'notes':['IDs/names/dates are excluded from decision-fact signatures.',
                     'Entropy describes this synthetic population, not real enterprise prevalence.',
                     'The 80% concentration warning is a declared diagnostic heuristic, not a statistical validity threshold.',
                     'Rules unavailable in the chosen phases are listed separately from applicable rules not covered.',
                     'Individual gates need not all be unique: finite rule combinations legitimately repeat.']}


def load_canonical(dataset):
    from dgf_bench.openrouter_eval.benchmark_runner import iter_cases
    for path in iter_cases(Path(dataset)):
        yield json.loads((path/'99_hidden_ground_truth.json').read_text(encoding='utf-8'))['canonical_truth']


def write_report(report,path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8', newline='\n')
    lines=['# Diversité des dossiers par gate','',f"Dossiers : {report['case_count']}. Les noms et identifiants ne comptent pas comme diversité métier.",'',
           '| Gate | Occurrences | Faits utiles distincts | Combinaisons de problèmes | Entropie des décisions (bits) | Décision dominante |',
           '|---|---:|---:|---:|---:|---:|']
    for gate,row in report['gates'].items():
        lines.append(f"| {gate} | {row['occurrences']} | {row['unique_relevant_fact_signatures']} | {row['unique_finding_patterns']} | {row['decision_entropy_bits']:.3f} | {100*row['dominant_decision_share']:.1f}% |")
    lines += ['','Une décision dominante au-delà de 80 % déclenche un avertissement descriptif. '
              'Ce seuil ne garantit ni représentativité ni validité scientifique.',
              'Les distributions, les règles absentes et le détail par route sont dans le JSON associé.']
    path.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8', newline='\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); report=audit_canonical(load_canonical(args.dataset)); write_report(report,args.output)
    print(json.dumps({'case_count':report['case_count'],'concentrated_gates':report['concentrated_gates'],'output':str(args.output)}))


if __name__=='__main__': main()
