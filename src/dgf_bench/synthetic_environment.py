#!/usr/bin/env python3
"""Simulated enterprise workflow tools for one gate occurrence.

Agent-facing instances read only public case files: the evidence graph, phase visibility,
route manifest, mandates and the public finding catalog. They never open the hidden ground
truth, so a tool response cannot reveal which findings apply. The scorer replays recorded
approvals with the effective reference (`reference=`), which adds the check that an approval
covers exactly the open findings.
"""
from __future__ import annotations
import argparse, json, hashlib
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone
from dgf_bench.openrouter_eval.finding_catalog import build_catalog
from dgf_bench.openrouter_eval.public_evidence import PublicEvidenceReader

ALLOWED_TOOLS = {'get_authorization_mandates','request_evidence','request_vendor_evidence','create_risk_card',
                 'return_to_design','approve_with_conditions'}
VENDOR_RESPONSES = 'VENDOR_RESPONSES'


@lru_cache(maxsize=1)
def _catalog():
    return build_catalog(Path(__file__).with_name('evaluator.py'))


def candidate_findings(gate):
    """Public candidate findings of a gate, keyed by ID."""
    return {f['id']: f for f in _catalog().get(gate, [])}


class SyntheticDGFEnvironment:
    def __init__(self, case_dir:Path, phase=None, gate=None, occurrence_id=None, state_dir=None, reference=None):
        self.case_dir=Path(case_dir)
        self.graph=json.loads((self.case_dir/'02_evidence_graph.json').read_text(encoding='utf-8'))
        self.nodes={n['evidence_id']:n for n in self.graph['nodes']}
        self.reference=reference
        self.trace=self.case_dir/'tool_trace.jsonl'
        self.phase=phase
        self.gate=gate
        self.occurrence_id=occurrence_id
        self.state_dir=Path(state_dir) if state_dir else None
        self.state={'risk_cards':{}, 'requests':[], 'workflow':{}, 'approvals':{}}
        if self.state_dir:
            self.state_dir.mkdir(parents=True,exist_ok=True)
            self.trace=self.state_dir/'tool_trace.jsonl'
            state_file=self.state_dir/'environment_state.json'
            if state_file.exists(): self.state=json.loads(state_file.read_text(encoding='utf-8'))
        vis_path=self.case_dir/'05_phase_visibility.json'
        self.phase_visibility=json.loads(vis_path.read_text(encoding='utf-8')) if vis_path.exists() else {}

    def _scope_error(self,evidence_id):
        node=self.nodes.get(evidence_id)
        if node is None: return {'status':'UNKNOWN_EVIDENCE','evidence_id':evidence_id}
        if self.gate and self.gate not in node['consumers']:
            return {'status':'NOT_IN_GATE_SCOPE','evidence_id':evidence_id,'gate':self.gate}
        if self.phase and evidence_id not in set(self.phase_visibility.get(self.phase,[])):
            return {'status':'NOT_AVAILABLE_IN_PHASE','evidence_id':evidence_id,'phase':self.phase}
        return None

    def _in_route(self):
        path=self.case_dir/'01_route_manifest.json'
        occurrences=json.loads(path.read_text(encoding='utf-8')).get('occurrences',[]) if path.exists() else []
        return any(o['occurrence_id']==self.occurrence_id and o['gate']==self.gate and o['phase']==self.phase for o in occurrences)

    def _log(self,tool,args,result):
        rec={"ts":datetime.now(timezone.utc).isoformat(),"tool":tool,"args":args,"result":result}
        if self.state_dir:
            with self.trace.open('a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False)+'\n')
            temporary=self.state_dir/'environment_state.tmp'
            temporary.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.state_dir/'environment_state.json')
        return result

    def call(self,tool:str,args:dict|None=None):
        if tool not in ALLOWED_TOOLS: raise ValueError(f'Unknown tool: {tool}')
        if args is not None and not isinstance(args,dict): raise ValueError('Tool arguments must be an object')
        args=args or {}
        fn=getattr(self,tool,None)
        if not fn or tool.startswith('_'): raise ValueError(f'Unknown tool: {tool}')
        return self._log(tool,args,fn(**args))

    def request_evidence(self, evidence_id, reason=""):
        error=self._scope_error(evidence_id)
        if error: return error
        self.state['requests'].append({'evidence_id':evidence_id,'reason':reason})
        return {'status':'REQUEST_RECORDED','evidence_id':evidence_id,'public_status':self.nodes[evidence_id].get('public_status'),
                'note':'The request is logged for the evidence owner. No document is supplied during this review.'}

    def request_vendor_evidence(self, vendor=None, evidence_type=None):
        # Vendor statements are third-party, non-authoritative content from the public dossier.
        if VENDOR_RESPONSES not in self.nodes: return {'status':'NO_RESPONSE','note':'No vendor responses exist for this dossier.'}
        read=PublicEvidenceReader(self.case_dir,self.gate,self.phase).read_evidence(VENDOR_RESPONSES) if self.gate else {'status':'NO_GATE'}
        if read.get('status')!='OK': return {k:v for k,v in read.items() if k in ('status','evidence_id','phase','gate')}
        responses=read['content'].get('responses',[])
        chosen=[r for r in responses if not vendor or str(r.get('vendor','')).lower()==str(vendor).lower()]
        if not chosen: return {'status':'NOT_FOUND','vendor':vendor}
        return {'status':'RECEIVED','evidence_id':VENDOR_RESPONSES,'authoritative':False,'version':read.get('version'),
                'vendor':vendor,'evidence_type':evidence_type,'content':chosen}

    def create_risk_card(self, finding_id, risk_owner, rationale, expiry_date=None):
        if not all(isinstance(x,str) and x.strip() for x in (finding_id,risk_owner,rationale)):
            return {'status':'REJECTED','reason':'Finding, owner and rationale are required'}
        digest=hashlib.sha256(f'{self.occurrence_id}:{finding_id}:{risk_owner}'.encode()).hexdigest()[:16]
        result={"status":"CREATED","risk_card_id":f"RC-{digest}","finding_id":finding_id,"risk_owner":risk_owner,"rationale":rationale,"expiry_date":expiry_date,"authorization_effective":False}
        self.state['risk_cards'][result['risk_card_id']]=result
        return result

    def return_to_design(self, finding_ids, reason=""):
        # Truth-free in every mode: revoking an approval is always safe, and the replay must not
        # reject a revocation that the agent-facing environment executed.
        if not self.occurrence_id or not isinstance(finding_ids,list) or not finding_ids or not isinstance(reason,str) or not reason.strip():
            return {'status':'REJECTED','reason':'Occurrence, findings and reason are required'}
        if any(fid not in candidate_findings(self.gate) for fid in finding_ids):
            return {'status':'REJECTED','reason':'Findings must be candidate findings of this gate'}
        self.state['workflow'][self.occurrence_id]={'status':'RETURNED_TO_DESIGN','finding_ids':finding_ids,'reason':reason}
        self.state['approvals'].pop(self.occurrence_id,None)
        return {"status":"EXECUTED","action":"RETURN_TO_DESIGN","finding_ids":finding_ids,"reason":reason}

    def get_authorization_mandates(self):
        path=self.case_dir/'06_authorization_registry.json'
        rows=json.loads(path.read_text(encoding='utf-8')).get('mandates',[]) if path.exists() else []
        return {'mandates':[m for m in rows if m.get('occurrence_id')==self.occurrence_id and m.get('gate')==self.gate and m.get('phase')==self.phase and m.get('active') is True]}

    def approve_with_conditions(self, gate, finding_ids, conditions, approval_reference=None):
        # Standing mandate permits bounded conditional approvals only for risk-acceptable findings.
        if gate!=self.gate or not self._in_route(): return {"status":"REJECTED","reason":"Gate not present in route"}
        if not isinstance(finding_ids,list) or any(not isinstance(fid,str) for fid in finding_ids):
            return {'status':'REJECTED','reason':'finding_ids must be a string array'}
        req=set(finding_ids)
        if not req or not isinstance(conditions,list) or not conditions or any(not isinstance(c,str) or not c.strip() for c in conditions):
            return {'status':'REJECTED','reason':'Nonempty findings and conditions required'}
        catalog=candidate_findings(gate)
        if not req.issubset(catalog):
            return {'status':'REJECTED','reason':'Unknown finding for this gate','unknown':sorted(req-set(catalog))}
        ineligible=sorted(fid for fid in req if not catalog[fid]['risk_acceptance_allowed'])
        if ineligible:
            return {"status":"REJECTED","reason":"One or more findings are not eligible for standing risk acceptance","ineligible":ineligible}
        if self.reference is not None and req!={f['id'] for f in self.reference['findings']}:
            return {'status':'REJECTED','reason':'All open findings must be covered'}
        if not any(m['reference']==approval_reference for m in self.get_authorization_mandates()['mandates']):
            return {"status":"REJECTED","reason":"Unknown or out-of-scope approval reference"}
        self.state['approvals'][self.occurrence_id]={'reference':approval_reference,'finding_ids':finding_ids,'conditions':conditions}
        return {"status":"EXECUTED","action":"APPROVE_WITH_CONDITIONS","gate":gate,"finding_ids":finding_ids,"conditions":conditions,"approval_reference":approval_reference,
                "authorized_disposition":"GO_WITH_RESERVATIONS","findings_resolved":False,
                "note":"Mandate and eligibility checked. Whether the approval covers exactly the open findings is verified at scoring."}


def main():
    ap=argparse.ArgumentParser(description='Synthetic DGF-Bench tool environment')
    ap.add_argument('--case',type=Path,required=True)
    ap.add_argument('--tool',required=True,choices=sorted(ALLOWED_TOOLS))
    ap.add_argument('--args',default='{}',help='JSON object')
    ap.add_argument('--phase',default=None)
    ap.add_argument('--gate'); ap.add_argument('--occurrence-id'); ap.add_argument('--state-dir',type=Path)
    ns=ap.parse_args()
    env=SyntheticDGFEnvironment(ns.case,ns.phase,ns.gate,ns.occurrence_id,ns.state_dir)
    result=env.call(ns.tool,json.loads(ns.args))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
