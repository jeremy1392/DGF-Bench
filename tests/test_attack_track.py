"""Attack condition: injections live only in non-authoritative evidence and are measured per gate."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from dgf_bench.attacks import TEMPLATES, make_attack_variant
from dgf_bench.control_agents import obey_injections, run_route
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.score_submission import score
from dgf_bench.validate_case import validate_case


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file()}


class AttackTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.clean = [build_case(root / 'clean', 23000 + i, 4, route) for i, route in enumerate(('buy', 'integrate', 'build'))]
        cls.attacked = [make_attack_variant(case, root / 'attack', 'dev', rate=1.0) for case in cls.clean]

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def truth(self, case):
        return json.loads((case / '99_hidden_ground_truth.json').read_text(encoding='utf-8'))

    def test_only_injected_files_change_and_facts_are_intact(self):
        for clean, attacked in zip(self.clean, self.attacked):
            truth, clean_truth = self.truth(attacked), self.truth(clean)
            self.assertEqual(truth['canonical_truth'], clean_truth['canonical_truth'])
            self.assertEqual(truth['reference_decisions'], clean_truth['reference_decisions'])
            injected = {e['path'] for e in truth['attack_manifest']}
            before, after = hashes(clean), hashes(attacked)
            changed = {path for path in before if before[path] != after[path]}
            self.assertEqual(changed - injected, {'00_project_context.json', '99_hidden_ground_truth.json'})
            context = json.loads((attacked / '00_project_context.json').read_text(encoding='utf-8'))
            self.assertEqual(context['variant'], 'attack')
            self.assertEqual(validate_case(attacked, require_certified=True), [])
            nodes = {n['evidence_id']: n for n in truth['evidence_graph']['nodes']}
            self.assertFalse([e for e in truth['attack_manifest'] if nodes[e['evidence_id']]['authoritative']])

    def test_reference_agent_resists_and_obedient_agent_is_attacked(self):
        for case in self.attacked:
            manifest = self.truth(case)['attack_manifest']
            attacked = [e for e in manifest if not e['placebo']]
            reference = score(case, *run_route(case, condition='attack'), condition='attack')
            obedient = score(case, *run_route(case, condition='attack', obey=obey_injections), condition='attack')
            self.assertEqual(reference['attack']['attack_success'], 0)
            self.assertEqual(reference['outcome_strict_success_count'], reference['expected_gate_count'])
            self.assertEqual(obedient['attack']['gates'], len(attacked))
            self.assertEqual(obedient['attack']['attack_success'], len(attacked))
            self.assertEqual(obedient['attack']['forged_authority_executed'], 0)
            self.assertEqual(obedient['attack']['placebo_canary_in_rationale'], 0)

    def test_every_eligible_gate_is_attacked_at_rate_one(self):
        for case in self.attacked:
            truth = self.truth(case)
            refs = {r['occurrence_id']: r for r in truth['reference_decisions']}
            for entry in truth['attack_manifest']:
                ref = refs[entry['occurrence_id']]
                eligible = ref['disposition'] != 'GO' or bool(ref['findings'])
                self.assertEqual(not entry['placebo'], eligible, entry['occurrence_id'])

    def test_dev_and_test_templates_share_no_wording(self):
        for family in TEMPLATES['dev']:
            dev = TEMPLATES['dev'][family].split('{goal}')[0]
            self.assertNotEqual(dev, TEMPLATES['test'][family].split('{goal}')[0])

    def test_attack_variants_need_clean_sources(self):
        with self.assertRaises(ValueError):
            make_attack_variant(self.attacked[0], Path(self.temp.name) / 'twice')


if __name__ == '__main__':
    unittest.main()
