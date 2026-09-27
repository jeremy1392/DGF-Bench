"""Docs-condition evidence: cited observation locations, re-checked against the public dossier."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from dgf_bench.control_agents import run_route
from dgf_bench.evidence_contract import observation_id, resolve_pointer
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.score_submission import score


class EvidenceLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.cases = {route: build_case(root, 22000 + i, 4, route) for i, route in enumerate(('buy', 'integrate', 'build'))}
        cls.runs = {route: run_route(case) for route, case in cls.cases.items()}

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def with_findings(self):
        for route, (submission, records) in self.runs.items():
            for result in submission['gate_results']:
                if result['evidence_support'] and not result['finding_ids'][0].startswith('GEN-'):
                    return route, result, records
        self.fail('fixture needs a specialist gate with findings')

    def rescore(self, route, result, records):
        submission = {'gate_results': [result]}
        return next(r for r in score(self.cases[route], submission, records, condition='docs')['occurrences']
                    if r['occurrence_id'] == result['occurrence_id'])

    def test_reference_agent_is_fully_supported(self):
        for route, case in self.cases.items():
            submission, records = self.runs[route]
            result = score(case, submission, records, condition='docs')
            self.assertEqual(result['strict_gate_success_count'], result['expected_gate_count'], route)
            self.assertTrue(result['route_complete_outcome'])
            generals = [r for r in result['occurrences'] if r['gate'] == 'general']
            self.assertTrue(all(r['outcome_strict_on_received_upstream'] for r in generals))

    def test_observation_ids_depend_only_on_content(self):
        self.assertEqual(observation_id('X', 1, {'a': [1, 2]}), observation_id('X', 1, {'a': [1, 2]}))
        self.assertNotEqual(observation_id('X', 1, {'a': [1, 2]}), observation_id('X', 2, {'a': [1, 2]}))
        self.assertEqual(resolve_pointer({'a': [{'b~/c': 3}]}, '/a/0/b~0~1c'), 3)
        for bad in ('a', '/a/01', '/a/5', '/missing'):
            with self.assertRaises(LookupError):
                resolve_pointer({'a': [1]}, bad)

    def test_wrong_pointer_is_not_support(self):
        route, result, records = self.with_findings()
        broken = copy.deepcopy(result)
        for item in broken['evidence_support']:
            item['json_pointer'] = '/0' if item['json_pointer'] != '/0' else '/1'
        self.assertLess(self.rescore(route, broken, records)['evidence_fidelity'], 1.0)

    def test_unobserved_or_forged_observation_is_not_support(self):
        route, result, records = self.with_findings()
        forged = copy.deepcopy(result)
        for item in forged['evidence_support']:
            item['observation_id'] = 'obs-0000000000000000'
        self.assertEqual(self.rescore(route, forged, records)['evidence_fidelity'], 0.0)

    def test_tampered_trace_content_is_ignored(self):
        route, result, records = self.with_findings()
        tampered = copy.deepcopy(records)
        cited = {item['observation_id'] for item in result['evidence_support']}
        for event in tampered[result['occurrence_id']]['tool_trace']:
            if event['result'].get('observation_id') in cited:
                event['result']['content'] = copy.deepcopy(event['result']['content'])
                if isinstance(event['result']['content'], dict):
                    event['result']['content']['_tampered'] = True
                else:
                    event['result']['content'].append({'_tampered': True})
        self.assertEqual(self.rescore(route, result, tampered)['evidence_fidelity'], 0.0)

    def test_narrative_source_is_not_admissible_when_a_record_exists(self):
        from dgf_bench.decisive_fields import decisive_fields
        from dgf_bench.evidence_contract import finding_premises
        from dgf_bench.openrouter_eval.agent_tools import ToolExecutor
        from dgf_bench.source_decoders import SOURCES
        for seed in range(22000, 22030):
            for route in ('buy', 'integrate', 'build'):
                case = self.cases[route] if seed == 22000 + ('buy', 'integrate', 'build').index(route) else \
                    build_case(Path(self.temp.name) / f'extra-{seed}', seed, 4, route)
                submission, records = run_route(case)
                truth = json.loads((case / '99_hidden_ground_truth.json').read_text(encoding='utf-8'))['canonical_truth']
                occurrences = {o['occurrence_id']: o for o in json.loads((case / '01_route_manifest.json').read_text(encoding='utf-8'))['occurrences']}
                for result in submission['gate_results']:
                    occ = occurrences[result['occurrence_id']]
                    decisive = decisive_fields(truth, occ['gate'], occ['phase'])
                    for fid in result['finding_ids']:
                        for field in finding_premises(fid, decisive):
                            for source in SOURCES.get(field, ()):
                                if source.authoritative:
                                    continue
                                tools = ToolExecutor(case, occ['gate'], occ['phase'], occ['occurrence_id'], condition='docs')
                                read = tools.call('read_evidence', {'evidence_id': source.evidence_id})
                                if read.get('status') != 'OK':
                                    continue
                                try:
                                    from dgf_bench.source_decoders import identity_context
                                    ctx = {**identity_context(truth['project']), 'procurement.selected_vendor': truth['procurement']['selected_vendor']}
                                    pointers, _ = source.decode(read['content'], ctx)
                                except LookupError:
                                    continue
                                swapped = copy.deepcopy(result)
                                swapped['evidence_support'] = [i if i['finding_id'] != fid else
                                                               {**i, 'observation_id': read['observation_id'], 'json_pointer': pointers[0]}
                                                               for i in result['evidence_support']]
                                trace = records[result['occurrence_id']]['tool_trace'] + tools.trace
                                row = next(r for r in score(case, {'gate_results': [swapped]}, {result['occurrence_id']: {'tool_trace': trace}},
                                                            condition='docs')['occurrences'] if r['occurrence_id'] == result['occurrence_id'])
                                self.assertLess(row['evidence_fidelity'], 1.0, (fid, field, source.evidence_id))
                                return
        self.fail('no finding premise with a readable narrative source in 90 dossiers')

    def test_facts_scoring_is_unchanged_for_docs_submissions(self):
        route, result, records = self.with_findings()
        row = next(r for r in score(self.cases[route], {'gate_results': [result]}, records)['occurrences']
                   if r['occurrence_id'] == result['occurrence_id'])
        # The v8 rule demands REVIEW_FACTS excerpts, which docs submissions do not contain.
        self.assertEqual(row['evidence_fidelity'], 0.0)
        self.assertTrue(row['outcome_strict'])


if __name__ == '__main__':
    unittest.main()
