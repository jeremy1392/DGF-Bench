"""The prose policy form: one written requirement per catalog finding, no code, no field names."""
import json
import unittest
from pathlib import Path

from dgf_bench import evaluator
from dgf_bench.benchmark_protocol import FIELD_GLOSSARY, POLICY_FORMS, public_policy
from dgf_bench.openrouter_eval.finding_catalog import build_catalog
from dgf_bench.policy_prose import REQUIREMENTS

GATES = ('it', 'architecture', 'security', 'tech_readiness', 'procurement', 'legal', 'compliance', 'general')


class PolicyProseTests(unittest.TestCase):
    def test_every_catalog_finding_has_one_requirement(self):
        catalog = build_catalog(Path(evaluator.__file__))
        for gate in GATES:
            written = [fid for fid, _ in REQUIREMENTS[gate]]
            self.assertEqual(len(written), len(set(written)), gate)
            self.assertEqual(sorted(written), sorted(f['id'] for f in catalog[gate]), gate)

    def test_prose_policy_has_no_code_or_field_names(self):
        for condition in ('facts', 'docs', 'attack'):
            for gate in GATES:
                policy = public_policy(gate, condition, 'prose')
                text = json.dumps(policy)
                self.assertNotIn('rule_definition_python', policy)
                self.assertNotIn('field_glossary', policy)
                self.assertIn('review_standard', policy)
                for token in ('def evaluate', 'case[', "f['", 'finding(', 'phase in'):
                    self.assertNotIn(token, text, (gate, condition))
                for field in FIELD_GLOSSARY:
                    self.assertNotIn(field, text, (gate, condition))

    def test_code_form_is_the_default_and_unchanged(self):
        self.assertEqual(POLICY_FORMS, ('code', 'prose'))
        for gate in GATES:
            self.assertEqual(public_policy(gate, 'docs'), public_policy(gate, 'docs', 'code'))
            self.assertIn('rule_definition_python', public_policy(gate, 'docs'))
        with self.assertRaises(ValueError):
            public_policy('it', 'docs', 'handbook')


if __name__ == '__main__':
    unittest.main()
