"""v9 environment: agent-facing tools cannot reveal the reference, and the docs condition hides snapshots."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dgf_bench.approval_policy import validated_conditional_approval
from dgf_bench.benchmark_protocol import PROJECT_IDENTITY_FIELDS, facts_id, public_policy
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.openrouter_eval.agent_runner import AgentConfig, run_occurrence
from dgf_bench.openrouter_eval.agent_tools import ToolExecutor
from dgf_bench.synthetic_environment import SyntheticDGFEnvironment, candidate_findings

CAPS = {'input_modalities': ['text'], 'supported_parameters': ['tools', 'tool_choice', 'max_tokens']}


class RecordingClient:
    """Lists evidence, reads one snapshot ID, then submits; records every request body."""
    api_key = 'unused'

    def __init__(self, snapshot_id):
        self.calls = []; self.snapshot_id = snapshot_id

    def stats_snapshot(self): return {'http_attempts': len(self.calls)}

    def chat(self, body):
        self.calls.append(copy.deepcopy(body))
        step = len(self.calls)
        if step == 1:
            fn = {'name': 'list_evidence', 'arguments': '{}'}
        elif step == 2:
            fn = {'name': 'read_evidence', 'arguments': json.dumps({'evidence_id': self.snapshot_id})}
        else:
            fn = {'name': 'submit_gate_decision', 'arguments': json.dumps({
                'disposition': 'GO', 'finding_ids': [], 'actions': [], 'evidence_refs': [], 'evidence_support': [],
                'authorization_required': False, 'rationale': 'offline test', 'confidence': 0.5})}
        return {'model': body['model'], 'usage': {'cost': 0.0}, 'choices': [{'finish_reason': 'tool_calls',
                'message': {'role': 'assistant', 'content': '', 'tool_calls': [{'id': str(step), 'type': 'function', 'function': fn}]}}]}


class TruthFreeEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.case = build_case(Path(cls.temp.name), 12010, 4, 'build')
        cls.truth = json.loads((cls.case / '99_hidden_ground_truth.json').read_text(encoding='utf-8'))
        # A gate with at least two risk-acceptable findings, so a strict subset is still eligible.
        cls.ref = next((r for r in cls.truth['reference_decisions'] if len(r['findings']) >= 2
                        and all(f['risk_acceptance_allowed'] for f in r['findings'])), None)
        if cls.ref is None:
            cls.ref = next(r for r in cls.truth['reference_decisions']
                           if r['findings'] and all(f['risk_acceptance_allowed'] for f in r['findings']))

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def env(self, case=None, state=None, reference=None):
        r = self.ref
        return SyntheticDGFEnvironment(case or self.case, r['phase'], r['gate'], r['occurrence_id'], state, reference)

    def approval_args(self, env, finding_ids):
        return {'gate': self.ref['gate'], 'finding_ids': finding_ids, 'conditions': ['Owner and due date for each action'],
                'approval_reference': env.get_authorization_mandates()['mandates'][0]['reference']}

    def test_agent_facing_tools_work_without_the_hidden_file(self):
        blind = Path(self.temp.name) / 'blind'
        shutil.copytree(self.case, blind / self.case.name)
        case = blind / self.case.name
        (case / '99_hidden_ground_truth.json').unlink()
        for occ in json.loads((case / '01_route_manifest.json').read_text(encoding='utf-8'))['occurrences']:
            for condition in ('facts', 'docs'):
                tools = ToolExecutor(case, occ['gate'], occ['phase'], occ['occurrence_id'], condition=condition)
                listed = tools.call('list_evidence', {})['evidence']
                for row in listed[:3]:
                    tools.call('read_evidence', {'evidence_id': row['evidence_id']})
                tools.call('get_authorization_mandates', {})
                tools.call('request_evidence', {'evidence_id': 'PROJECT_CHARTER', 'reason': 'check'})
                tools.call('request_vendor_evidence', {})

    def test_responses_do_not_depend_on_the_reference(self):
        catalog = candidate_findings(self.ref['gate'])
        reference_ids = [f['id'] for f in self.ref['findings']]
        outside = next(fid for fid, f in catalog.items() if fid not in reference_ids and f['risk_acceptance_allowed'])
        env = self.env()
        # Complete, partial and wrong eligible sets all execute: the online tool cannot tell them apart.
        for ids in (reference_ids, reference_ids[:1], [outside]):
            self.assertEqual(env.approve_with_conditions(**self.approval_args(env, ids))['status'], 'EXECUTED', ids)
        self.assertEqual(env.return_to_design([outside], 'check')['status'], 'EXECUTED')
        for fid, finding in catalog.items():
            if not finding['risk_acceptance_allowed']:
                result = env.approve_with_conditions(**self.approval_args(env, [fid]))
                self.assertEqual(result['reason'], 'One or more findings are not eligible for standing risk acceptance')
                break

    def test_offline_replay_requires_exact_coverage(self):
        env = self.env()
        ids = [f['id'] for f in self.ref['findings']]
        for requested, valid in ((ids, True), (ids[:-1] or ['IT-CMDB-001'], False)):
            args = self.approval_args(env, requested)
            result = env.approve_with_conditions(**args)
            record = {'tool_trace': [{'tool': 'approve_with_conditions', 'args': args, 'result': result}]}
            approval = validated_conditional_approval(self.case, self.ref, record)
            self.assertEqual(approval is not None, valid, requested)

    def test_revocation_outside_the_reference_is_honored_at_replay(self):
        env = self.env()
        ids = [f['id'] for f in self.ref['findings']]
        args = self.approval_args(env, ids)
        approved = env.call('approve_with_conditions', args)
        outside = next(fid for fid in candidate_findings(self.ref['gate']) if fid not in ids)
        revoke = {'finding_ids': [outside], 'reason': 'Further review required'}
        revoked = env.call('return_to_design', revoke)
        self.assertEqual(revoked['status'], 'EXECUTED')
        self.assertNotIn(self.ref['occurrence_id'], env.state['approvals'])
        record = {'tool_trace': [{'tool': 'approve_with_conditions', 'args': args, 'result': approved},
                                 {'tool': 'return_to_design', 'args': revoke, 'result': revoked}]}
        self.assertIsNone(validated_conditional_approval(self.case, self.ref, record))

    def test_request_evidence_never_returns_content(self):
        tools = ToolExecutor(self.case, self.ref['gate'], self.ref['phase'], self.ref['occurrence_id'])
        for row in tools.call('list_evidence', {})['evidence']:
            result = tools.call('request_evidence', {'evidence_id': row['evidence_id']})
            self.assertNotIn('content', result)

    def test_docs_condition_hides_snapshots_and_canonical_project_fields(self):
        r = self.ref; sid = facts_id(r['gate'])
        tools = ToolExecutor(self.case, r['gate'], r['phase'], r['occurrence_id'], condition='docs')
        self.assertNotIn(sid, [row['evidence_id'] for row in tools.call('list_evidence', {})['evidence']])
        self.assertEqual(tools.call('read_evidence', {'evidence_id': sid})['status'], 'UNKNOWN_EVIDENCE')
        occ = next(o for o in json.loads((self.case / '01_route_manifest.json').read_text(encoding='utf-8'))['occurrences']
                   if o['occurrence_id'] == r['occurrence_id'])
        client = RecordingClient(sid)
        record = run_occurrence(client, self.case, occ, CAPS, AgentConfig(model='fake/model', information_condition='docs'), [])
        first = client.calls[0]['messages']
        self.assertNotIn('REVIEW_FACTS', first[0]['content'])
        prompt = first[1]['content']
        project = json.loads(prompt.split('PROJECT CONTEXT\n', 1)[1].split('\n\nROUTE', 1)[0])
        self.assertEqual(set(project), set(PROJECT_IDENTITY_FIELDS) & set(project))
        self.assertNotIn('business_criticality', project)
        contract = json.loads(prompt.split('GATE CONTRACT\n', 1)[1].split('\n\nCANDIDATE FINDINGS', 1)[0])
        self.assertNotIn('authoritative_snapshot', contract['decision_policy'])
        self.assertFalse([e for e in contract['admissible_inputs'] if e.startswith('REVIEW_FACTS_')])
        self.assertEqual(record['tool_trace'][1]['result']['status'], 'UNKNOWN_EVIDENCE')
        facts_client = RecordingClient(sid)
        facts_record = run_occurrence(facts_client, self.case, occ, CAPS, AgentConfig(model='fake/model'), [])
        self.assertEqual(facts_record['tool_trace'][1]['result']['status'], 'OK')
        self.assertIn('authoritative_snapshot', public_policy(r['gate']))

    def test_runner_rejects_mismatched_conditions_before_network(self):
        from unittest.mock import patch
        from dgf_bench.openrouter_eval import benchmark_runner as br
        dataset = self.case.parent
        common = dict(max_cases=None, max_cost_usd=1, max_turns=2, max_tool_calls=2, max_tokens=100,
                      temperature=0, vision='off', reasoning_effort=None)
        with patch.object(br, 'OpenRouterClient', side_effect=AssertionError('Network must not be used')):
            for condition, handoff, message in (('attack', 'agent', 'attack condition needs attack dossiers'),
                                                ('docs', 'oracle', 'oracle handoffs'), ('nonsense', 'agent', 'unknown')):
                with self.assertRaises(ValueError, msg=message):
                    br.run_benchmark(dataset, ['fake/model'], Path(self.temp.name) / ('out-' + condition), handoff_mode=handoff,
                                     information_condition=condition, **common)


if __name__ == '__main__':
    unittest.main()
