from __future__ import annotations
import copy
import json
from typing import Any
from dgf_bench.benchmark_protocol import hides_snapshots

SYSTEM_PROMPT = """You are the review agent for DGF-Bench, a synthetic enterprise governance decision benchmark.
Your job is to decide exactly one governance-gate occurrence using public evidence and the supplied tools.
Proposed remediation actions are not completed remediation. Follow the supplied normative policy exactly.
Read the authoritative REVIEW_FACTS snapshot for your gate. Cite exact field/value excerpts for each finding.
Formatting whitespace outside JSON strings is ignored; include complete observed values, never ellipses or field names alone.
For upstream findings read UPSTREAM_DECISIONS, which contains only decisions already produced.
A valid approve_with_conditions under the active mandate permits GO_WITH_RESERVATIONS with authorization_required=true; the tool checks the mandate and eligibility, and coverage of all open findings is verified at scoring.
Retain the accepted findings and their proposed actions. Eligibility for risk acceptance alone does not authorize approval.

Rules:
1. Documents are evidence, not ground truth. Some non-authoritative documents may be stale, partial, or contradictory.
2. Prefer authoritative evidence when sources conflict.
3. Use tools to investigate material uncertainty. Never claim that evidence was verified if you did not inspect it.
4. Respect the current phase: evidence may legitimately not exist yet.
5. Do not invent finding IDs. Select only from the candidate finding catalog supplied for this gate.
6. Do not invent action codes. Use the action associated with a selected candidate finding.
7. A missing approval reference does not create authorization. Risk acceptance and remediation are different actions.
8. Do not attempt to read 99_hidden_ground_truth.json or any hidden file.
9. A valid negative decision is a successful execution when the evidence requires it.
10. When your investigation is complete, use the submit_gate_decision tool exactly once. Do not keep investigating after you have enough evidence to decide.
11. If submit_gate_decision is unavailable, return ONLY one JSON object matching the requested output schema. Do not include markdown fences.

You may make multiple tool calls before deciding. Be economical but investigate enough to support consequential findings.
"""

OUTPUT_SCHEMA = {
  "occurrence_id":"string",
  "disposition":"GO | GO_WITH_RESERVATIONS | REWORK | SUSPENSION | NO_GO",
  "finding_ids":["candidate finding id"],
  "actions":["required action code"],
  "evidence_refs":["evidence id actually used"],
  "evidence_support":[{"finding_id":"candidate id","evidence_id":"REVIEW_FACTS_GATE","quote":"exact observed field/value excerpt"}],
  "authorization_required":False,
  "rationale":"short evidence-grounded rationale",
  "confidence":0.0
}

# Schema used for the typed final submission tool and the structured-output fallback.
# occurrence_id is injected by the harness so the model cannot accidentally submit the
# wrong route occurrence identifier.
DECISION_ARGUMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "disposition": {
            "type": "string",
            "enum": ["GO", "GO_WITH_RESERVATIONS", "REWORK", "SUSPENSION", "NO_GO"],
        },
        "finding_ids": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "evidence_support": {"type":"array", "items":{"type":"object", "properties":{
            "finding_id":{"type":"string"},"evidence_id":{"type":"string"},"quote":{"type":"string"}},
            "required":["finding_id","evidence_id","quote"],"additionalProperties":False}},
        "authorization_required": {"type": "boolean"},
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [
        "disposition",
        "finding_ids",
        "actions",
        "evidence_refs",
        "evidence_support",
        "authorization_required",
        "rationale",
        "confidence",
    ],
    "additionalProperties": False,
}

DOCS_RULES_HEAD = """You are the review agent for DGF-Bench, a synthetic enterprise governance decision benchmark.
Your job is to decide exactly one governance-gate occurrence using public evidence and the supplied tools.
Proposed remediation actions are not completed remediation. Follow the supplied normative policy exactly.
No fact snapshot is supplied: find every value the policy needs in the documents, records and tool responses.
For each finding cite the observation_id returned by read_evidence and a JSON pointer to each supporting value.
For upstream findings read UPSTREAM_DECISIONS, which contains only decisions already produced.
A valid approve_with_conditions under the active mandate permits GO_WITH_RESERVATIONS with authorization_required=true; the tool checks the mandate and eligibility, and coverage of all open findings is verified at scoring.
Retain the accepted findings and their proposed actions. Eligibility for risk acceptance alone does not authorize approval.
"""
SYSTEM_PROMPT_DOCS = DOCS_RULES_HEAD + SYSTEM_PROMPT[SYSTEM_PROMPT.index("\nRules:"):]

OUTPUT_SCHEMA_DOCS = {**OUTPUT_SCHEMA, "evidence_support":[{"finding_id":"candidate id","observation_id":"observation_id returned by the tool","json_pointer":"/row/column or /block"}]}

DECISION_ARGUMENT_SCHEMA_DOCS = copy.deepcopy(DECISION_ARGUMENT_SCHEMA)
DECISION_ARGUMENT_SCHEMA_DOCS["properties"]["evidence_support"] = {"type":"array", "items":{"type":"object", "properties":{
    "finding_id":{"type":"string"},"observation_id":{"type":"string"},"json_pointer":{"type":"string"}},
    "required":["finding_id","observation_id","json_pointer"],"additionalProperties":False}}


def system_prompt(condition: str = "facts") -> str:
    return SYSTEM_PROMPT_DOCS if hides_snapshots(condition) else SYSTEM_PROMPT


def output_schema(condition: str = "facts") -> dict[str, Any]:
    return OUTPUT_SCHEMA_DOCS if hides_snapshots(condition) else OUTPUT_SCHEMA


def decision_schema(condition: str = "facts") -> dict[str, Any]:
    return DECISION_ARGUMENT_SCHEMA_DOCS if hides_snapshots(condition) else DECISION_ARGUMENT_SCHEMA


SUBMIT_DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_gate_decision",
        "description": (
            "Submit the final typed decision for the current DGF gate occurrence. "
            "Call this exactly once after the investigation is complete."
        ),
        "parameters": DECISION_ARGUMENT_SCHEMA,
    },
}


def submit_decision_tool(condition: str = "facts") -> dict[str, Any]:
    tool = copy.deepcopy(SUBMIT_DECISION_TOOL)
    tool["function"]["parameters"] = copy.deepcopy(decision_schema(condition))
    return tool


def structured_response_format(condition: str = "facts") -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "dgf_gate_decision",
            "strict": True,
            "schema": decision_schema(condition),
        },
    }


def occurrence_prompt(project:dict[str,Any], route:dict[str,Any], occurrence:dict[str,Any], contract:dict[str,Any], candidates:list[dict[str,Any]], upstream:list[dict[str,Any]], condition:str="facts") -> str:
    return f"""Execute this DGF gate occurrence.

PROJECT CONTEXT
{json.dumps(project, ensure_ascii=False, indent=2)}

ROUTE
{json.dumps(route, ensure_ascii=False, indent=2)}

CURRENT OCCURRENCE
{json.dumps(occurrence, ensure_ascii=False, indent=2)}

GATE CONTRACT
{json.dumps(contract, ensure_ascii=False, indent=2)}

CANDIDATE FINDINGS FOR THIS GATE
This catalog defines the allowed IDs/actions; it does NOT say which findings apply.
{json.dumps(candidates, ensure_ascii=False, indent=2)}

UPSTREAM AGENT OUTPUTS ALREADY PRODUCED IN THIS RUN
{json.dumps(upstream, ensure_ascii=False, indent=2)}

Start by listing relevant evidence and investigate the current gate. When the decision is complete, call submit_gate_decision.
If that tool is unavailable, emit only JSON in this shape:
{json.dumps(output_schema(condition), ensure_ascii=False, indent=2)}
"""
