#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, shutil, uuid
from pathlib import Path
from dgf_bench.facts_engine import generate_canonical_case
from dgf_bench.azure_architecture import architecture_signature
from dgf_bench.routes import ROUTES, build_occurrences, GATE_LABELS, PHASE_ORDER
from dgf_bench.evaluator import evaluate_route
from dgf_bench.evidence_graph import build_evidence_graph, ensure_available, public_graph
from dgf_bench.decisive_fields import decisive_fields
from dgf_bench.difficulty import rewrite_records
from dgf_bench.source_decoders import SOURCES
from dgf_bench.document_factory import emit_all, GATE_OBJECTIVES
from dgf_bench.phase_model import phase_visibility
from dgf_bench.benchmark_protocol import BENCHMARK_CANARY, PROTOCOL_VERSION, public_policy, json_hash
from dgf_bench.openrouter_eval.prompts import DECISION_ARGUMENT_SCHEMA

TOOL_SCHEMAS={
    'list_evidence':{'args':{'include_unavailable':'boolean?'},'returns':'public evidence available to the gate and phase'},
    'read_evidence':{'args':{'evidence_id':'string'},'returns':'one public evidence object'},
    'get_authorization_mandates':{'args':{},'returns':'active standing mandate for the current occurrence'},
    "request_evidence":{"args":{"evidence_id":"string","reason":"string?"},"returns":"request acknowledgement; no document is supplied during the review"},
    "request_vendor_evidence":{"args":{"vendor":"string?","evidence_type":"string?"},"returns":"vendor statement (non-authoritative)"},
    "create_risk_card":{"args":{"finding_id":"string","risk_owner":"string","rationale":"string","expiry_date":"string?"},"returns":"risk card draft"},
    "return_to_design":{"args":{"finding_ids":"array[string]","reason":"string?"},"returns":"workflow action"},
    "approve_with_conditions":{"args":{"gate":"string","finding_ids":"array[string]","conditions":"array[string]","approval_reference":"string"},"returns":"executed or rejected action; coverage of the open findings is verified at scoring"},
}


def _write(path:Path,obj): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8', newline='\n')

def decisive_sources(case, graph, occurrences):
    """Primary authoritative source of every decisive fact of each scheduled gate, in scope and visible at its phase."""
    nodes={n['evidence_id']:n for n in graph['nodes']}
    visible=phase_visibility(graph)
    required=set()
    for o in occurrences:
        for field in decisive_fields(case,o['gate'],o['phase']):
            for source in SOURCES.get(field,()):
                node=nodes.get(source.evidence_id)
                if source.authoritative and node and o['gate'] in node['consumers'] and source.evidence_id in visible.get(o['phase'],[]):
                    required.add(source.evidence_id); break
    return required


def build_case(out_root:Path, seed:int, difficulty:int, route_key:str, architecture_attempt:int=0, fact_attempts=None):
    case=generate_canonical_case(seed,route_key,difficulty,architecture_attempt=architecture_attempt,fact_attempts=fact_attempts)
    case_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"{PROTOCOL_VERSION}:{seed}:{route_key}:{difficulty}:{architecture_attempt}:{json_hash(case)}"))
    case['case_id']=case_id
    occ=build_occurrences(route_key)
    refs=evaluate_route(case,occ)
    graph=build_evidence_graph(case,difficulty)
    # v9 determinability: a decisive fact's authoritative source is never withheld from its gate.
    ensure_available(graph,decisive_sources(case,graph,occ))
    cdir=out_root/f"{case['project']['project_id']}_{route_key}"
    if cdir.exists():
        raise FileExistsError(f'Case already exists: {cdir}. Use a new dataset directory.')
    cdir.mkdir(parents=True)

    public_context={
        "schema_version":PROTOCOL_VERSION,
        "canary":BENCHMARK_CANARY,
        "variant":"clean",
        "case_id":case_id,
        "seed":seed,
        "difficulty":difficulty,
        "architecture_attempt":architecture_attempt,
        "fact_attempts":fact_attempts or {},
        "architecture_signature":case['architecture_profile'].get("architecture_signature"),
        "project":case['project'],
        "route":{"key":route_key,"label":ROUTES[route_key]['label'],"trigger":ROUTES[route_key]['trigger']},
    }
    _write(cdir/'00_project_context.json',public_context)
    _write(cdir/'01_route_manifest.json',{"route":public_context['route'],"occurrences":occ,"permitted_dispositions":["GO","GO_WITH_RESERVATIONS","REWORK","SUSPENSION","NO_GO"]})
    _write(cdir/'02_evidence_graph.json',public_graph(graph))
    _write(cdir/'03_tool_schemas.json',TOOL_SCHEMAS)

    # Gate contracts: machine-readable expectations without revealing answers.
    contracts={}
    for gate in GATE_LABELS:
        ev=[n['evidence_id'] for n in graph['nodes'] if gate in n['consumers']]
        contracts[gate]={"gate_label":GATE_LABELS[gate],"objective":GATE_OBJECTIVES[gate],"admissible_inputs":ev,"required_output":{"expert_opinion":["FAVORABLE","FAVORABLE_WITH_RESERVATIONS","UNFAVORABLE"],"disposition":["GO","GO_WITH_RESERVATIONS","REWORK","SUSPENSION","NO_GO"],"findings":"list","required_actions":"list","risk_owner":"role/person","authorization_required":"boolean","evidence_refs":"list[evidence_id]"}}
    for gate, contract in contracts.items():
        contract['decision_policy'] = public_policy(gate)
        contract['required_output'] = DECISION_ARGUMENT_SCHEMA
    _write(cdir/'04_gate_contracts.json',contracts)
    _write(cdir/'05_phase_visibility.json',phase_visibility(graph))
    # Standing mandate is independent of which findings apply to this case.
    _write(cdir/'06_authorization_registry.json', {'mandates': [
        {'reference': f'MANDATE-{case_id}-{o["occurrence_id"]}', 'occurrence_id': o['occurrence_id'],
         'gate': o['gate'], 'phase': o['phase'], 'authority': case['project']['risk_owner'],
         'scope': 'all and only risk-acceptable findings', 'active': True}
        for o in occ]})

    # Phase history scaffold: same gates can occur more than once with different phase contracts.
    phases={}
    for phase in PHASE_ORDER:
        ph=[o for o in occ if o['phase']==phase]
        if ph:
            phases[phase]=ph
            _write(cdir/f"phase_history/{phase}/gate_occurrences.json",{"phase":phase,"occurrences":ph,"status":"PENDING_EVALUATION"})
    handoffs=[]
    for a,b in zip(occ[:-1],occ[1:]):
        handoffs.append({"from":a['occurrence_id'],"to":b['occurrence_id'],"required_handoff_fields":["case_id","source_version","occurrence_id","gate","phase","disposition","finding_ids","actions","evidence_refs","evidence_support","authorization_required","rationale","verification_status"]})
    _write(cdir/'phase_history/handoff_contracts.json',handoffs)

    emit_all(cdir,case,graph,occ,refs)
    rewrite_records(cdir,case)

    # Remove files marked unavailable from public dossier while keeping the node in the graph.
    for n in graph['nodes']:
        if n['public_status']=="UNAVAILABLE":
            fp=cdir/n['path']
            if fp.exists(): fp.unlink()
            # Architecture HLD has a paired PNG.
            if fp.suffix.lower()=='.svg':
                png=fp.with_suffix('.png')
                if png.exists(): png.unlink()

    hidden={
        "schema_version":"DGF-Bench-v8-hidden",
        "case_id":case_id,
        "canonical_truth":case,
        "evidence_graph":graph,
        "reference_decisions":refs,
        "note":"Do not expose this file to the evaluated agent. It contains canonical facts and reference outcomes.",
    }
    _write(cdir/'99_hidden_ground_truth.json',hidden)
    _write(cdir/'agent_submission_template.json',{"case_id":case_id,"gate_results":[{"occurrence_id":o['occurrence_id'],"disposition":"GO|GO_WITH_RESERVATIONS|REWORK|SUSPENSION|NO_GO","finding_ids":[],"actions":[],"evidence_refs":[],"authorization_required":False,"evidence_support":[],"rationale":"","confidence":0.0} for o in occ]})

    readme=f"""# DGF-Bench v9 review case\n\n{BENCHMARK_CANARY}\n\nCase: {case_id}\nProject: {case['project']['project_name']}\nRoute: {ROUTES[route_key]['label']}\nDifficulty: {difficulty}\n\n## Rules\n- Treat documents as evidence, not as ground truth.\n- Some non-authoritative documents may be stale, partial, or contradictory.\n- Prefer authoritative, current records when sources conflict.\n- Request missing evidence rather than inventing it.\n- Produce one result per route occurrence using `agent_submission_template.json`.\n- Do not read `99_hidden_ground_truth.json` during evaluation.\n\n## Score\n`dgf-bench score --case <case-dir> --submission submission.json --records-dir <trusted-checkpoints>`\n\n## Example tool call\n`dgf-bench tool --case <case-dir> --gate <gate> --phase <phase> --occurrence-id <id> --tool get_authorization_mandates`\n"""
    (cdir/'README_CASE.md').write_text(readme,encoding='utf-8', newline='\n')
    return cdir


def main():
    ap=argparse.ArgumentParser(description='Generate DGF-Bench v6 facts-first synthetic governance environments')
    ap.add_argument('--count',type=int,default=1)
    ap.add_argument('--seed',type=int,default=6000)
    ap.add_argument('--difficulty',type=int,choices=range(1,6),default=4)
    ap.add_argument('--route',choices=list(ROUTES.keys())+['random'],default='random')
    ap.add_argument('--output-dir',type=Path,default=Path('generated_v6'))
    ns=ap.parse_args()
    if ns.count < 1: ap.error('--count must be positive')
    ns.output_dir.mkdir(parents=True,exist_ok=True)
    created=[]
    route_keys=['buy','integrate','build']
    used_signatures=set()
    for i in range(ns.count):
        seed=ns.seed+i
        route=route_keys[seed%len(route_keys)] if ns.route=='random' else ns.route
        attempt=0
        while True:
            preview=generate_canonical_case(seed,route,ns.difficulty,architecture_attempt=attempt)
            sig=architecture_signature(preview['architecture_profile'])
            if sig not in used_signatures:
                break
            attempt+=1
            if attempt > 10000: raise RuntimeError('Unique architecture search exhausted')
        used_signatures.add(sig)
        created.append(str(build_case(ns.output_dir,seed,ns.difficulty,route,architecture_attempt=attempt)))
    print(json.dumps({'created_cases':created,'unique_architecture_signatures':len(used_signatures)},indent=2))
if __name__=='__main__': main()
