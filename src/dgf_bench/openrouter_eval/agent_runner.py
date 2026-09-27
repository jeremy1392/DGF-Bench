from __future__ import annotations
import base64, copy, json, uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .agent_tools import ALL_TOOLS, ToolExecutor
from .finding_catalog import build_catalog
from .json_utils import parse_json_object, normalize_submission
from .openrouter_client import OpenRouterClient, Usage, OpenRouterError, BudgetStopped
from .prompts import (
    occurrence_prompt,
    output_schema,
    structured_response_format,
    submit_decision_tool,
    system_prompt,
)
from dgf_bench.benchmark_protocol import PROJECT_IDENTITY_FIELDS, hides_snapshots, public_policy, select_upstream
from .agent_tools import is_snapshot


@dataclass
class AgentConfig:
    model: str
    temperature: float = 0.0
    max_tokens: int = 8192
    max_turns: int = 20
    max_tool_calls: int = 40
    reasoning_effort: str | None = None
    use_vision: bool = False
    require_parameters: bool = True
    state_dir: str | None = None
    information_condition: str = "facts"
    provider_order: list[str] | None = None
    policy_form: str = "code"
    reading_conventions: bool = True


class AgentRunError(RuntimeError):
    """Agent failure that preserves usage and trace data for paper-grade accounting."""

    def __init__(self, message: str, *, kind: str, record: dict[str, Any]):
        super().__init__(message)
        self.kind = kind
        self.record = record


def _clean_message(m:dict[str,Any])->dict[str,Any]:
    keep={"role","content","tool_calls","name"}
    result = {k:v for k,v in m.items() if k in keep and v is not None}
    result['role'] = 'assistant'
    return result


def _tool_call_error(calls):
    """Validate transport shape before replaying a model message to a provider."""
    if not isinstance(calls, list):
        return 'tool_calls must be an array'
    ids = set()
    for call in calls:
        if not isinstance(call, dict) or call.get('type') != 'function':
            return 'tool call must have type function'
        identifier = call.get('id')
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            return 'tool call IDs must be nonempty and unique'
        ids.add(identifier)
        fn = call.get('function')
        if not isinstance(fn, dict) or not isinstance(fn.get('name'), str) or not fn['name']:
            return 'tool function must have a name'
        try:
            arguments = json.loads(fn['arguments'])
        except (KeyError, TypeError, ValueError):
            return 'tool function arguments must be valid JSON text'
        if not isinstance(arguments, dict):
            return 'tool function arguments must encode an object'
    return None


def _image_part(case_dir:Path, reader):
    if reader.read_evidence('HLD').get('status') != 'OK': return None
    p=reader._safe_path(reader.nodes['HLD']).with_suffix('.png')
    if not p.is_file(): return None
    b64=base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type":"image_url","image_url":{"url":"data:image/png;base64,"+b64}}


def _decision_tool(strict: bool, condition: str = "facts") -> dict[str, Any]:
    tool = submit_decision_tool(condition)
    if strict:
        tool["function"]["strict"] = True
    return tool


def _record(
    *,
    result: dict[str, Any] | None,
    usage: Usage,
    tools: ToolExecutor,
    raw_turns: list[dict[str, Any]],
    resolved_models: list[str],
    provider_values: list[Any],
    turns: int,
    tool_calls: int,
    finish_reasons: list[Any],
    truncated_response_count: int,
    finalization_mode: str | None = None,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    out = {
        "usage": usage.as_dict(),
        "tool_trace": tools.trace,
        "raw_turns": raw_turns,
        "resolved_models": resolved_models,
        "providers": provider_values,
        "turns": turns,
        "tool_call_count": tool_calls,
        "finish_reasons": finish_reasons,
        "last_finish_reason": finish_reasons[-1] if finish_reasons else None,
        "truncated_response_count": truncated_response_count,
        "finalization_mode": finalization_mode,
        "validation_errors": validation_errors or [],
        "image_attached": bool(getattr(tools, "image_attached", False)),
    }
    if result is not None:
        out["result"] = result
    return out


def _normalize_payload(payload: dict[str, Any], oid: str, condition: str = "facts") -> dict[str, Any]:
    # occurrence_id is owned by the harness, not trusted from the model.
    obj = dict(payload)
    obj["occurrence_id"] = oid
    return normalize_submission(obj, oid, condition)


def _finalization_request(client: OpenRouterClient, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Send the structured finalization; returns (response, structured).

    A pinned provider may not support structured outputs although the model does elsewhere: with
    fallbacks disabled, OpenRouter then finds no endpoint. The call is repeated once without
    response_format, relying on the JSON instruction already in the conversation.
    """
    try:
        return client.chat(body), "response_format" in body
    except OpenRouterError as e:
        if "response_format" not in body or "No endpoints found" not in str(e) or isinstance(e, BudgetStopped):
            raise
    plain = {k: v for k, v in body.items() if k != "response_format"}
    return client.chat(plain), False


def _apply_common_params(body: dict[str, Any], config: AgentConfig, params: set[str]) -> None:
    if "temperature" in params:
        body["temperature"] = config.temperature
    if "max_tokens" in params:
        body["max_tokens"] = config.max_tokens
    elif "max_completion_tokens" in params:
        body["max_completion_tokens"] = config.max_tokens
    if config.reasoning_effort:
        if "reasoning_effort" in params:
            body["reasoning_effort"] = config.reasoning_effort
        elif "reasoning" in params:
            body["reasoning"] = {"effort": config.reasoning_effort}
    if config.provider_order:
        # Pinned providers keep a model's serving stack fixed for the whole comparison.
        body["provider"] = {"order": list(config.provider_order), "allow_fallbacks": False,
                            "require_parameters": bool(config.require_parameters)}
    elif config.require_parameters:
        # Provider fallback means another endpoint serving the SAME requested model,
        # not a silent switch to a different model ID. The resolved model is recorded.
        body["provider"] = {"require_parameters": True, "allow_fallbacks": True}


def run_occurrence(client:OpenRouterClient, case_dir:Path, occurrence:dict[str,Any], model_caps:dict[str,Any], config:AgentConfig, upstream:list[dict[str,Any]]):
    case_dir=Path(case_dir)
    project=json.loads((case_dir/"00_project_context.json").read_text(encoding="utf-8"))
    route_manifest=json.loads((case_dir/"01_route_manifest.json").read_text(encoding="utf-8"))
    contracts=json.loads((case_dir/"04_gate_contracts.json").read_text(encoding="utf-8"))
    # Always the packaged rules: a dataset directory must not be able to supply its own policy.
    catalog=build_catalog(Path(__file__).resolve().parents[1]/"evaluator.py")
    gate=occurrence["gate"]; phase=occurrence["phase"]; oid=occurrence["occurrence_id"]
    candidates=catalog.get(gate,[])
    condition=config.information_condition
    # The packaged policy replaces the copy frozen in the dataset at generation time.
    contract=dict(contracts[gate]); contract['decision_policy']=public_policy(gate,condition,config.policy_form,config.reading_conventions)
    project_context=project["project"]
    if hides_snapshots(condition):
        contract['admissible_inputs']=[e for e in contract.get('admissible_inputs',[]) if not is_snapshot(e)]
        project_context={k:project_context[k] for k in PROJECT_IDENTITY_FIELDS if k in project_context}
    upstream=select_upstream(upstream,phase)
    tools=ToolExecutor(case_dir,gate,phase,oid,upstream,config.state_dir,condition)
    prompt=occurrence_prompt(project_context,route_manifest["route"],occurrence,contract,candidates,upstream,condition)
    content:Any=prompt
    if config.use_vision and gate in {"architecture","security","it","tech_readiness"} and "image" in set(model_caps.get("input_modalities") or []):
        img=_image_part(case_dir,tools.public)
        if img:
            content=[{"type":"text","text":prompt},img]
            tools.image_attached=True
    messages=[{"role":"system","content":system_prompt(condition)},{"role":"user","content":content}]
    usage=Usage(); resolved_models=[]; provider_values=[]; tool_calls=0; raw_turns=[]; finish_reasons=[]; truncated_response_count=0
    validation_errors: list[str] = []
    session_id=f"dgfbench:{project['case_id']}:{config.model}:{oid}:{uuid.uuid4().hex[:8]}"
    params=set(model_caps.get("supported_parameters") or [])
    strict_structured = "structured_outputs" in params or "response_format" in params
    submit_tool = _decision_tool(strict_structured, condition)

    def fail(message: str, kind: str, turns: int) -> None:
        raise AgentRunError(
            message,
            kind=kind,
            record=_record(
                result=None,
                usage=usage,
                tools=tools,
                raw_turns=raw_turns,
                resolved_models=resolved_models,
                provider_values=provider_values,
                turns=turns,
                tool_calls=tool_calls,
                finish_reasons=finish_reasons,
                truncated_response_count=truncated_response_count,
                validation_errors=validation_errors,
            ),
        )

    # Main investigation loop. The last turn is reserved for a forced typed submission.
    for turn in range(config.max_turns):
        force_finalize = turn == config.max_turns - 1 or tool_calls >= config.max_tool_calls
        offered_tools = [submit_tool] if force_finalize else (ALL_TOOLS + [submit_tool])
        body={
            "model":config.model,
            "messages":messages,
            "tools":offered_tools,
            "session_id":session_id,
            "usage":{"include":True},
        }
        if "parallel_tool_calls" in params:
            body["parallel_tool_calls"] = False
        if "tool_choice" in params:
            body["tool_choice"] = (
                {"type":"function","function":{"name":"submit_gate_decision"}}
                if force_finalize else "auto"
            )
        _apply_common_params(body, config, params)

        try:
            resp=client.chat(body)
        except OpenRouterError as e:
            fail(f"OpenRouter request failed: {e}", "budget" if isinstance(e,BudgetStopped) else "infrastructure", turn)

        turn_usage=Usage.from_response(resp)
        usage.add(turn_usage)
        if resp.get("model"): resolved_models.append(str(resp["model"]))
        if resp.get("provider"): provider_values.append(resp["provider"])
        choices=resp.get("choices") or []
        if not choices:
            fail("No choices returned by OpenRouter", "infrastructure", turn+1)
        choice=choices[0]
        finish_reason=choice.get("finish_reason")
        finish_reasons.append(finish_reason)
        if finish_reason in {"length", "max_tokens"}:
            truncated_response_count += 1
        msg=_clean_message(choice.get("message") or {})
        raw_turns.append({
            "turn":turn,
            "assistant":msg,
            "finish_reason":finish_reason,
            "usage":turn_usage.as_dict(),
            "resolved_model":resp.get("model"),
            "provider":resp.get("provider"),
            "forced_finalization":force_finalize,
        })
        tc=msg.get("tool_calls") or []
        if finish_reason == 'error':
            fail('Provider returned finish_reason=error', 'infrastructure', turn+1)
        malformed = _tool_call_error(tc)
        if malformed:
            validation_errors.append('Invalid tool message: ' + malformed)
            # Preserve the original in raw_turns, but never replay invalid API
            # structures. No call in a malformed batch is executed or invented.
            messages.append({'role':'assistant','content':
                             'Invalid tool message (not executed): ' + json.dumps(msg,ensure_ascii=False)})
            messages.append({'role':'user','content':
                             malformed + '. Retry using valid tool calls with complete JSON object arguments.'})
            continue
        if tc or msg.get('content'):
            messages.append(msg)
        else:
            # Empty/truncated assistant messages can make strict providers reject
            # the entire subsequent history with HTTP 422.
            validation_errors.append('Empty assistant message omitted from transport history')

        if tc:
            # A typed decision tool call is a final answer; do not send it to the environment.
            for call in tc:
                fn=(call.get("function") or {})
                if fn.get("name") != "submit_gate_decision":
                    continue
                argtext=fn.get("arguments") or "{}"
                try:
                    args=json.loads(argtext) if isinstance(argtext,str) else dict(argtext)
                    result=_normalize_payload(args, oid, condition)
                    return _record(
                        result=result,
                        usage=usage,
                        tools=tools,
                        raw_turns=raw_turns,
                        resolved_models=resolved_models,
                        provider_values=provider_values,
                        turns=turn+1,
                        tool_calls=tool_calls,
                        finish_reasons=finish_reasons,
                        truncated_response_count=truncated_response_count,
                        finalization_mode="submit_gate_decision",
                        validation_errors=validation_errors,
                    )
                except Exception as e:
                    validation_errors.append(f"submit_gate_decision validation failed: {type(e).__name__}: {e}")
                    # Arguments are syntactically valid here. A schema error is
                    # safe to return as an ordinary tool result for correction.
                    messages.append({
                        "role":"tool",
                        "tool_call_id":call.get("id") or "submit-invalid",
                        "name":"submit_gate_decision",
                        "content":json.dumps({"status":"INVALID_SUBMISSION","error":str(e)}, ensure_ascii=False),
                    })

            # Execute evidence/action tools. Calls beyond the budget receive an explicit result
            # rather than crashing the conversation; the next turn is forced to finalize.
            for call in tc:
                fn=(call.get("function") or {})
                name=fn.get("name")
                if name == "submit_gate_decision":
                    continue
                argtext=fn.get("arguments") or "{}"
                if force_finalize or tool_calls >= config.max_tool_calls:
                    messages.append({
                        "role":"tool",
                        "tool_call_id":call.get("id") or f"budget-{tool_calls}",
                        "name":name,
                        "content":json.dumps({
                            "status":"TOOL_BUDGET_EXHAUSTED",
                            "message":"No more investigation tools are available. Submit the gate decision now."
                        }),
                    })
                    continue
                tool_calls += 1
                try: args=json.loads(argtext) if isinstance(argtext,str) else dict(argtext)
                except Exception: args={"_raw_arguments":str(argtext)}
                try: result=tools.call(name,args)
                except Exception as e: result={"status":"TOOL_ERROR","error":str(e),"tool":name}
                messages.append({"role":"tool","tool_call_id":call.get("id") or f"call-{tool_calls}","name":name,"content":json.dumps(result,ensure_ascii=False)})
            continue

        # Plain JSON remains supported for models/providers that choose not to use the submission tool.
        final=parse_json_object(str(msg.get("content") or ""))
        if final is not None:
            try:
                normalized=_normalize_payload(final, oid, condition)
                return _record(
                    result=normalized,
                    usage=usage,
                    tools=tools,
                    raw_turns=raw_turns,
                    resolved_models=resolved_models,
                    provider_values=provider_values,
                    turns=turn+1,
                    tool_calls=tool_calls,
                    finish_reasons=finish_reasons,
                    truncated_response_count=truncated_response_count,
                    finalization_mode="plain_json",
                    validation_errors=validation_errors,
                )
            except Exception as e:
                validation_errors.append(f"plain JSON validation failed: {type(e).__name__}: {e}")

        if not force_finalize:
            messages.append({
                "role":"user",
                "content":(
                    "Your last response was not a valid final gate decision. Continue only if material evidence is still missing; "
                    "otherwise call submit_gate_decision now. If you cannot call it, return ONLY valid JSON matching this schema: "
                    + json.dumps(output_schema(condition))
                ),
            })

    # Last-resort structured-output call. This removes incidental JSON-formatting failures from
    # the semantic benchmark while preserving the model's entire investigation history.
    final_body={
        "model":config.model,
        "messages":messages + [{
            "role":"user",
            "content":"Investigation is over. Return the final DGF gate decision now. Do not call any more tools."
        }],
        "session_id":session_id,
        "usage":{"include":True},
    }
    _apply_common_params(final_body, config, params)
    if "response_format" in params or "structured_outputs" in params:
        final_body["response_format"] = structured_response_format(condition)
    try:
        resp, structured = _finalization_request(client, final_body)
    except OpenRouterError as e:
        fail(f"OpenRouter finalization request failed: {e}", "budget" if isinstance(e,BudgetStopped) else "infrastructure", config.max_turns)
    if not structured:
        final_body.pop("response_format", None)

    turn_usage=Usage.from_response(resp)
    usage.add(turn_usage)
    if resp.get("model"): resolved_models.append(str(resp["model"]))
    if resp.get("provider"): provider_values.append(resp["provider"])
    choices=resp.get("choices") or []
    if not choices:
        fail("No choices returned during structured finalization", "infrastructure", config.max_turns+1)
    choice=choices[0]
    finish_reason=choice.get("finish_reason")
    finish_reasons.append(finish_reason)
    if finish_reason in {"length", "max_tokens"}:
        truncated_response_count += 1
    msg=_clean_message(choice.get("message") or {})
    raw_turns.append({
        "turn":config.max_turns,
        "assistant":msg,
        "finish_reason":finish_reason,
        "usage":turn_usage.as_dict(),
        "resolved_model":resp.get("model"),
        "provider":resp.get("provider"),
        "structured_finalization":structured,
    })
    final=parse_json_object(str(msg.get("content") or ""))
    if final is not None:
        try:
            normalized=_normalize_payload(final, oid, condition)
            return _record(
                result=normalized,
                usage=usage,
                tools=tools,
                raw_turns=raw_turns,
                resolved_models=resolved_models,
                provider_values=provider_values,
                turns=config.max_turns+1,
                tool_calls=tool_calls,
                finish_reasons=finish_reasons,
                truncated_response_count=truncated_response_count,
                finalization_mode="structured_output_fallback",
                validation_errors=validation_errors,
            )
        except Exception as e:
            validation_errors.append(f"structured finalization validation failed: {type(e).__name__}: {e}")

    fail(
        f"Agent did not produce a valid typed gate decision after {config.max_turns} investigation turns plus structured finalization",
        "agent_protocol",
        config.max_turns+1,
    )
