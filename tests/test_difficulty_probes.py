"""Difficulty probes: compositional records stay certified; reading conventions can be omitted."""
import json
import tempfile
import unittest
from pathlib import Path

from dgf_bench.benchmark_protocol import public_policy
from dgf_bench.certification import certify_case
from dgf_bench.control_agents import run_route
from dgf_bench.difficulty import compose_records, exit_clause
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.score_submission import score


class CompositionalRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cases = []
        for i, route in enumerate(('buy', 'build')):
            case = build_case(Path(cls.temp.name) / route, 21100 + i, 4, route)
            truth = json.loads((case / '99_hidden_ground_truth.json').read_text(encoding='utf-8'))
            compose_records(case, truth['canonical_truth'])
            cls.cases.append(case)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_composed_dossiers_are_certified_and_solved_by_the_reference(self):
        for case in self.cases:
            self.assertEqual([r for r in certify_case(case) if r['status'] != 'MATCH'], [], case.name)
            submission, records = run_route(case, condition='docs')
            s = score(case, submission, records, condition='docs')
            self.assertEqual(s['strict_gate_success_count'], s['expected_gate_count'], case.name)

    def test_facts_are_no_longer_stated(self):
        for case in self.cases:
            root = case / 'gate_evidence'
            self.assertNotIn('approved_keur', (root / 'general/budget_approval.csv').read_text(encoding='utf-8'))
            for name in ('it/capacity_report.csv', 'it/lifecycle_eol.csv'):
                if (root / name).is_file():
                    text = (root / name).read_text(encoding='utf-8')
                    self.assertNotIn('capacity_headroom_pct', text)
                    self.assertNotIn('eol_months', text)

    def test_exit_clause_wording(self):
        self.assertEqual(exit_clause(0), 'No exit assistance is provided under this agreement.')
        self.assertIn('ninety (90) days', exit_clause(90))


class ReadingConventionTests(unittest.TestCase):
    def test_conventions_can_be_omitted(self):
        self.assertIn('reading_records', public_policy('it', 'docs'))
        bare = public_policy('it', 'docs', 'code', reading_conventions=False)
        self.assertNotIn('reading_records', bare)
        self.assertEqual({k: v for k, v in public_policy('it', 'docs').items() if k != 'reading_records'}, bare)


if __name__ == '__main__':
    unittest.main()
