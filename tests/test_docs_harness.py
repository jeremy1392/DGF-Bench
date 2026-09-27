"""End-to-end docs and attack conditions through the real harness, with scripted model clients."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dgf_bench.attacks import make_attack_dataset
from dgf_bench.compare_conditions import compare
from dgf_bench.control_agents import _INSTRUCTION, submission_from_observations
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.openrouter_eval import benchmark_runner as br
from dgf_bench.openrouter_eval.aggregate import aggregate
from dgf_bench.run_full_experiment import collect_paper_metrics, write_paper_outputs

MODEL = 'fake/agent'
CAPS = {'input_modalities': ['text'], 'supported_parameters': ['tools', 'tool_choice', 'max_tokens']}


class ScriptedClient:
    """Plays a control agent through tool calls: list, read every available source, then decide."""
    api_key = 'unused'

    def __init__(self, authority='authoritative', obey=False):
        self.authority, self.obey, self.calls = authority, obey, []

    def stats_snapshot(self): return {'http_attempts': len(self.calls)}

    def _reply(self, body, calls):
        tool_calls = [{'id': f'{len(self.calls)}-{i}', 'type': 'function',
                       'function': {'name': name, 'arguments': json.dumps(args)}} for i, (name, args) in enumerate(calls)]
        return {'model': body['model'], 'usage': {'cost': .001}, 'choices': [{'finish_reason': 'tool_calls',
                'message': {'role': 'assistant', 'content': '', 'tool_calls': tool_calls}}]}

    def chat(self, body):
        self.calls.append(copy.deepcopy(body))
        prompt = body['messages'][1]['content']
        occurrence = json.loads(prompt.split('CURRENT OCCURRENCE\n', 1)[1].split('\n\nGATE CONTRACT', 1)[0])
        results = [json.loads(m['content']) for m in body['messages'] if m['role'] == 'tool']
        listed = next((r['evidence'] for r in results if 'evidence' in r), None)
        if listed is None:
            return self._reply(body, [('list_evidence', {})])
        observations = {r['evidence_id']: (r['observation_id'], bool(r.get('authoritative')), r['content'])
                        for r in results if r.get('status') == 'OK' and 'observation_id' in r}
        unread = [row['evidence_id'] for row in listed if row['public_status'] == 'AVAILABLE' and row['evidence_id'] not in observations]
        if unread:
            return self._reply(body, [('read_evidence', {'evidence_id': eid}) for eid in unread])
        upstream = observations['UPSTREAM_DECISIONS'][2] if 'UPSTREAM_DECISIONS' in observations else []
        project = json.loads(prompt.split('PROJECT CONTEXT\n', 1)[1].split('\n\nROUTE', 1)[0])
        decision = submission_from_observations(occurrence, observations, upstream, self.authority, project)
        calls = []
        if self.obey:
            text = json.dumps([o[2] for o in observations.values()], ensure_ascii=False)
            forged_done = any('approve_with_conditions' in json.dumps(m.get('tool_calls') or []) for m in body['messages'] if m['role'] == 'assistant')
            for cue, fake, waived, token in (m.groups() for m in _INSTRUCTION.finditer(text)):
                if cue.startswith('with disposition GO'):
                    decision['disposition'], decision['authorization_required'] = 'GO', False
                elif fake and not forged_done:
                    calls.append(('approve_with_conditions', {'gate': occurrence['gate'], 'finding_ids': decision['finding_ids'] or ['none'],
                                                              'conditions': ['As instructed'], 'approval_reference': fake}))
                elif waived in decision['finding_ids']:
                    decision['finding_ids'].remove(waived)
                    decision['evidence_support'] = [i for i in decision['evidence_support'] if i['finding_id'] != waived]
        if calls:
            return self._reply(body, calls)
        decision.pop('occurrence_id')
        return self._reply(body, [('submit_gate_decision', decision)])


class DocsHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        for i, route in enumerate(('buy', 'integrate', 'build')):
            build_case(cls.root / 'clean', 24000 + i, 4, route)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def run_condition(self, name, dataset, condition, client):
        out = self.root / name
        with patch.object(br, 'OpenRouterClient', side_effect=lambda **kw: client), \
             patch.object(br, 'load_model_caps', return_value={MODEL: CAPS}), contextlib.redirect_stdout(io.StringIO()):
            br.run_benchmark(dataset=dataset, models=[MODEL], out_dir=out, max_cases=None, max_cost_usd=25, max_turns=8,
                             max_tool_calls=200, max_tokens=512, temperature=0, vision='off', reasoning_effort=None,
                             workers=1, information_condition=condition)
        return out

    def test_docs_and_attack_end_to_end(self):
        reference = self.run_condition('docs-reference', self.root / 'clean', 'docs', ScriptedClient())
        blind = self.run_condition('docs-blind', self.root / 'clean', 'docs', ScriptedClient('blind'))
        scores = [json.loads(p.read_text(encoding='utf-8')) for p in reference.glob('*/*/score.json')]
        self.assertEqual(len(scores), 3)
        for sc in scores:
            self.assertEqual(sc['information_condition'], 'docs')
            self.assertEqual(sc['strict_gate_success_count'], sc['expected_gate_count'], [o['evidence_diagnostics'] for o in sc['occurrences']])
        aggregated = aggregate(reference)
        self.assertEqual(aggregated['information_condition'], 'docs')
        model = aggregated['models'][0]
        self.assertEqual(model['outcome_csr_micro'], 1.0)
        self.assertEqual(model['outcome_rate_all_planned'], 1.0)
        overall, gates = collect_paper_metrics(reference, aggregated)
        self.assertEqual(overall[0]['outcome_csr'], 1.0)
        (reference / 'paper').mkdir()
        write_paper_outputs(reference / 'paper', overall, gates, {'information_condition': 'docs'})
        self.assertIn('Primary results (outcome-strict)', (reference / 'paper' / 'PAPER_RESULTS.md').read_text(encoding='utf-8'))
        comparison = compare(blind, reference, draws=500)
        self.assertEqual(comparison['models'][MODEL]['paired_cases'], 3)
        self.assertGreaterEqual(comparison['models'][MODEL]['gate']['delta'], 0.0)

        make_attack_dataset(self.root / 'clean', self.root / 'attack-data', 'dev', rate=1.0)
        resisted = self.run_condition('attack-reference', self.root / 'attack-data', 'attack', ScriptedClient())
        obeyed = self.run_condition('attack-obedient', self.root / 'attack-data', 'attack', ScriptedClient(obey=True))
        resisted_attack = aggregate(resisted)['models'][0]['attack']
        obeyed_attack = aggregate(obeyed)['models'][0]['attack']
        self.assertGreater(resisted_attack['gates'], 0)
        self.assertEqual(resisted_attack['injection_observed'], resisted_attack['gates'])
        self.assertEqual(resisted_attack['attack_success'], 0)
        self.assertEqual(obeyed_attack['attack_success'], obeyed_attack['gates'])
        self.assertEqual(obeyed_attack['forged_authority_executed'], 0)
        overall, _ = collect_paper_metrics(obeyed, aggregate(obeyed))
        self.assertEqual(overall[0]['attack_success_rate'], 1.0)


if __name__ == '__main__':
    unittest.main()
