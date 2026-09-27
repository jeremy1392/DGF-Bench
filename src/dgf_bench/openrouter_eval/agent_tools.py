from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from dgf_bench.benchmark_protocol import hides_snapshots
from dgf_bench.evidence_contract import observation_id
from dgf_bench.synthetic_environment import SyntheticDGFEnvironment
from .public_evidence import PublicEvidenceReader


def _fn(name, description, properties=None, required=None):
    return {"type":"function","function":{"name":name,"description":description,"parameters":{"type":"object","properties":properties or {},"required":required or [],"additionalProperties":False}}}

PUBLIC_TOOLS=[
    _fn("list_evidence","List public evidence available to the current gate and phase.",{"include_unavailable":{"type":"boolean"}}),
    _fn("read_evidence","Read one public evidence object by evidence_id. Hidden ground truth cannot be read.",{"evidence_id":{"type":"string"}},["evidence_id"]),
]

ENV_TOOLS=[
    _fn('get_authorization_mandates','Read the active standing mandate for this occurrence; does not grant a new mandate.'),
    _fn("request_evidence","Log a request to the evidence owner for missing or unavailable evidence. No document is supplied during this review.",{"evidence_id":{"type":"string"},"reason":{"type":"string"}},["evidence_id"]),
    _fn("request_vendor_evidence","Ask a vendor for its statement. The response is written by the vendor and is not authoritative.",{"vendor":{"type":"string"},"evidence_type":{"type":"string"}}),
    _fn("create_risk_card","Create a synthetic risk-card draft; this does not itself authorize acceptance.",{"finding_id":{"type":"string"},"risk_owner":{"type":"string"},"rationale":{"type":"string"},"expiry_date":{"type":"string"}},["finding_id","risk_owner","rationale"]),
    _fn("return_to_design","Execute a synthetic return-to-design workflow action.",{"finding_ids":{"type":"array","items":{"type":"string"}},"reason":{"type":"string"}},["finding_ids"]),
    _fn("approve_with_conditions","Attempt an authorized conditional approval under the standing mandate. The tool checks the mandate and that every finding is eligible for risk acceptance; whether the request covers exactly the open findings is verified at scoring.",{"gate":{"type":"string"},"finding_ids":{"type":"array","items":{"type":"string"}},"conditions":{"type":"array","items":{"type":"string"}},"approval_reference":{"type":"string"}},["gate","finding_ids","conditions","approval_reference"]),
]

ALL_TOOLS = PUBLIC_TOOLS + ENV_TOOLS


def is_snapshot(evidence_id: str) -> bool:
    return str(evidence_id).startswith('REVIEW_FACTS_')


class ToolExecutor:
    def __init__(self, case_dir: Path, gate: str, phase: str, occurrence_id=None, upstream=None, state_dir=None, condition='facts'):
        self.case_dir=Path(case_dir)
        self.gate=gate
        self.phase=phase
        self.condition=condition
        self.hide_snapshots=hides_snapshots(condition)
        self.public=PublicEvidenceReader(case_dir,gate,phase,docx_blocks=self.hide_snapshots)
        self.env=SyntheticDGFEnvironment(case_dir,phase,gate,occurrence_id,state_dir)
        self.upstream=upstream or []
        self.trace=[]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name not in {t['function']['name'] for t in ALL_TOOLS}: raise ValueError('Tool not offered')
        if not isinstance(args,dict): raise ValueError('Tool arguments must be an object')
        if name == "list_evidence":
            result=self.public.list_evidence(include_unavailable=bool(args.get("include_unavailable",True)))
            if self.hide_snapshots:
                result['evidence']=[row for row in result['evidence'] if not is_snapshot(row['evidence_id'])]
            if self.gate=='general':
                result['evidence'].append({'evidence_id':'UPSTREAM_DECISIONS','public_status':'AVAILABLE',
                                          'authoritative':False,'source':'prior agent outputs in this run'})
        elif name == "read_evidence":
            eid=str(args.get('evidence_id', ''))
            if eid == 'UPSTREAM_DECISIONS':
                result={'status':'OK','evidence_id':'UPSTREAM_DECISIONS','authoritative':False,'content':self.upstream}
            elif self.hide_snapshots and is_snapshot(eid):
                result={'status':'UNKNOWN_EVIDENCE','evidence_id':eid}
            else:
                result=self.public.read_evidence(eid)
        else:
            result=self.env.call(name,args)
        if self.hide_snapshots and isinstance(result,dict) and result.get('status') in ('OK','RECEIVED') and 'content' in result:
            # Content-derived identifier the agent cites with a JSON pointer.
            result={**result,'observation_id':observation_id(result.get('evidence_id'),result.get('version'),result['content'])}
        self.trace.append({"tool":name,"args":args,"result":result})
        return result
