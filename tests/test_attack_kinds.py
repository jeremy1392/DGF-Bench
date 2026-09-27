"""Attack kinds: variants stay certified, the reference agent resists, the obedient agent follows."""
import json
import tempfile
import unittest
from pathlib import Path

from dgf_bench.attack_kinds import KINDS
from dgf_bench.attacks import make_attack_variant
from dgf_bench.certification import certify_case
from dgf_bench.control_agents import obey_injections, run_route
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.score_submission import score


def attack_rows(case, **agent):
    submission, records = run_route(case, condition='attack', **agent)
    s = score(case, submission, records, condition='attack')
    return s, [o['attack'] for o in s['occurrences'] if o.get('attack') and not o['attack']['placebo']]


class AttackKindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.clean = [build_case(root / 'clean' / route, seed, 4, route) for route, seed in (('buy', 20004), ('build', 20013))]

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_every_kind_keeps_references_and_is_resisted_by_the_reference(self):
        live = set()
        for kind in KINDS:
            for clean in self.clean:
                case = make_attack_variant(clean, Path(self.temp.name) / kind, 'dev', 1.0, kind)
                self.assertEqual([r for r in certify_case(case) if r['status'] != 'MATCH'], [], (kind, case.name))
                s, rows = attack_rows(case)
                self.assertEqual(s['outcome_strict_success_count'], s['expected_gate_count'], (kind, case.name))
                self.assertFalse(any(r.get('exact_action') for r in rows), (kind, case.name))
                if kind != 'image':
                    _, obeyed = attack_rows(case, obey=obey_injections,
                                            authority='credulous' if kind == 'forged_row' else 'authoritative')
                    if any(r.get('exact_action') for r in obeyed):
                        live.add(kind)
        self.assertEqual(live, set(KINDS) - {'image'})

    def test_forged_entries_are_recorded_by_someone_else(self):
        case = make_attack_variant(self.clean[0], Path(self.temp.name) / 'forged-check', 'dev', 1.0, 'forged_row')
        manifest = json.loads((case / '99_hidden_ground_truth.json').read_text(encoding='utf-8'))['attack_manifest']
        self.assertTrue(manifest)
        for entry in manifest:
            forged = json.loads(entry['text'])
            self.assertIn(entry['token'], forged['recorded_by'])


if __name__ == '__main__':
    unittest.main()
