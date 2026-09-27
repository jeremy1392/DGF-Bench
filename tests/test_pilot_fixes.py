"""Regressions reproduced from the September v8 pilot, without network calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from dgf_bench.evidence_provenance import supports_observed_fields
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.openrouter_eval.agent_runner import AgentConfig, AgentRunError, run_occurrence
from dgf_bench.statistical_intervals import cluster_interval


class EvidenceTests(unittest.TestCase):
    def test_layout_does_not_change_provenance(self):
        facts = {'x': 'draft', 'y': True, 'nested': {'n': 100}}
        for quote in ['"x": "draft", "y": true', '"x":"draft",\n\t"y":true',
                      json.dumps(facts, indent=4), '"n":100']:
            self.assertTrue(supports_observed_fields(facts, quote, {'x','n'}), quote)

    def test_missing_values_and_value_prefixes_are_rejected(self):
        facts = {'x': 100, 'name': 'draft document', 'enabled': False}
        for quote in ['"x":', '"x": 10', '"name": "draft"', '"name": "draft',
                      '"enabled": fal', '"x": 100 ...', '"name": "draftdocument"']:
            self.assertFalse(supports_observed_fields(facts, quote, set(facts)), quote)

    def test_unrelated_and_invented_fields_rejected(self):
        facts = {'x': 100, 'y': True}
        for quote in ['"y": true', '"x": 101', '"x": true']:
            self.assertFalse(supports_observed_fields(facts, quote, {'x'}))

    def test_complete_null_boolean_and_string_values(self):
        facts = {'x': None, 'y': False, 'z': 'a b'}
        for quote, field in [('"x": null','x'), ('"y": false','y'), ('"z": "a b"','z')]:
            self.assertTrue(supports_observed_fields(facts, quote, {field}))
        self.assertTrue(supports_observed_fields({'x':'é a'},'"x": "\\u00e9 a"',{'x'}))

    def test_degenerate_stratified_intervals_have_uncertainty(self):
        low, high = cluster_interval([('build',1,1)]*4 + [('integrate',0,1)]*3)
        self.assertLess(low,4/7)
        self.assertGreater(high,4/7)


class RepairClient:
    """Strict fake provider that rejects malformed/empty transport history."""
    def __init__(self, first, always_invalid=False):
        self.first=first; self.calls=[]; self.always_invalid=always_invalid

    def chat(self, body):
        self.calls.append(copy.deepcopy(body))
        for message in body['messages']:
            if message['role']=='assistant':
                assert message.get('content') or message.get('tool_calls'), 'Empty assistant history'
            for call in message.get('tool_calls',[]):
                assert isinstance(json.loads(call['function']['arguments']),dict), 'Invalid JSON history'
        if len(self.calls)==1 or self.always_invalid:
            message, finish = self.first
        else:
            args={'disposition':'GO','finding_ids':[],'actions':[],'evidence_refs':[],
                  'authorization_required':False,'rationale':'test','confidence':.5,'evidence_support':[]}
            message={'role':'assistant','tool_calls':[{'id':'final','type':'function',
                     'function':{'name':'submit_gate_decision','arguments':json.dumps(args)}}]}
            finish='tool_calls'
        return {'usage':{'cost':.01},'choices':[{'finish_reason':finish,'message':message}]}


class TransportRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.case=build_case(Path(cls.temp.name),43103,4,'build')
        cls.occ=json.loads((cls.case/'01_route_manifest.json').read_text(encoding='utf-8'))['occurrences'][0]

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def run_client(self, client):
        return run_occurrence(client,self.case,self.occ,{'supported_parameters':['tools']},
                              AgentConfig(model='fake/model',max_turns=2),[])

    def test_invalid_json_is_preserved_in_trace_but_not_replayed_as_tool(self):
        msg={'role':'assistant','tool_calls':[{'id':'bad','type':'function','function':
             {'name':'submit_gate_decision','arguments':'{"actions": '}}]}
        client=RepairClient((msg,'tool_calls')); result=self.run_client(client)
        self.assertEqual(result['result']['disposition'],'GO')
        self.assertEqual(result['raw_turns'][0]['assistant'],msg)
        self.assertTrue(result['validation_errors'])
        self.assertEqual(result['tool_trace'],[])
        self.assertAlmostEqual(result['usage']['cost'],.02)

    def test_empty_truncated_message_is_not_sent_back(self):
        client=RepairClient(({'role':'assistant'},'length'))
        result=self.run_client(client)
        self.assertEqual(result['result']['disposition'],'GO')
        self.assertEqual(result['truncated_response_count'],1)

    def test_explicit_provider_error_not_retried_as_empty_assistant(self):
        client=RepairClient(({'role':'assistant'},'error'))
        with self.assertRaises(AgentRunError) as caught: self.run_client(client)
        self.assertEqual(caught.exception.kind,'infrastructure')
        self.assertEqual(len(client.calls),1)

    def test_persistent_invalid_output_is_an_agent_failure_not_infra(self):
        msg={'role':'assistant','tool_calls':[{'id':'bad','type':'function','function':
             {'name':'submit_gate_decision','arguments':'{"actions": '}}]}
        client=RepairClient((msg,'tool_calls'),always_invalid=True)
        with self.assertRaises(AgentRunError) as caught: self.run_client(client)
        self.assertEqual(caught.exception.kind,'agent_protocol')
        self.assertEqual(len(client.calls),3)  # bounded loop plus finalization

    def test_malformed_batch_does_not_execute_other_tools(self):
        msg={'role':'assistant','tool_calls':[
            {'id':'good','type':'function','function':{'name':'list_evidence','arguments':'{}'}},
            {'id':'bad','type':'function','function':{'name':'read_evidence','arguments':'[]'}}]}
        result=self.run_client(RepairClient((msg,'tool_calls')))
        self.assertEqual(result['tool_trace'],[])


if __name__=='__main__': unittest.main()
