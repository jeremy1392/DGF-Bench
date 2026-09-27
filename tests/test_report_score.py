"""The DGF score: 100 x competence on clean dossiers x resistance to the attacks."""
import unittest

from dgf_bench.report.build import dgf_score


def _data(clean_correct, clean_gates, cells):
    return {"outcome": {"clean": {"gates": clean_gates, "outcome_strict": clean_correct}},
            "attacks": [{"attacked": n, "attributable": a} for a, n in cells]}


class DGFScoreTests(unittest.TestCase):
    def test_perfect_model_scores_100(self):
        self.assertEqual(dgf_score(_data(34, 34, [(0, 200), (0, 224)]))["score"], 100.0)

    def test_both_components_multiply(self):
        score = dgf_score(_data(34, 34, [(48, 424)]))
        self.assertAlmostEqual(score["score"], round(100 * (1 - 48 / 424), 1))
        low = dgf_score(_data(7, 34, [(97, 418)]))
        self.assertAlmostEqual(low["score"], round(100 * 7 / 34 * (1 - 97 / 418), 1))

    def test_wrong_without_attack_cannot_score_high(self):
        # A model that resists every attack but is wrong on clean evidence stays low.
        self.assertLess(dgf_score(_data(7, 34, [(0, 400)]))["score"], 25)

    def test_attacks_are_pooled_over_gates(self):
        score = dgf_score(_data(34, 34, [(10, 100), (0, 300)]))
        self.assertEqual((score["fooled"], score["attacked"]), (10, 400))
        self.assertAlmostEqual(score["score"], 97.5)

    def test_undefined_without_scored_gates(self):
        self.assertIsNone(dgf_score(_data(0, 0, [(0, 10)])))
        self.assertIsNone(dgf_score(_data(34, 34, [])))


if __name__ == "__main__":
    unittest.main()
