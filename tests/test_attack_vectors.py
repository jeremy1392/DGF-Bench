"""The twelve file-level attack vectors: every variant builds, validates and certifies, and the
evidence reader surfaces the planted metadata / PDF so a manifest goal can score it."""
import json
import tempfile
import unittest
from pathlib import Path

from dgf_bench.attacks import make_attack_variant
from dgf_bench.attack_vectors import VECTORS
from dgf_bench.certification import certify_case
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.openrouter_eval.public_evidence import PublicEvidenceReader
from dgf_bench.validate_case import validate_case

try:
    import pypdf  # noqa: F401
    import pyhanko  # noqa: F401
    HAS_PDF = True
except ImportError:
    HAS_PDF = False


class AttackVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.clean = build_case(Path(cls.temp.name) / "clean" / "buy", 40010, 4, "buy")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    _counter = 0

    def _build(self, vector):
        type(self)._counter += 1
        out = Path(self.temp.name) / f"{vector}_{self._counter}"
        out.mkdir()
        return make_attack_variant(self.clean, out, templates="test", rate=1.0, placement=vector)

    def test_every_vector_builds_validates_and_certifies(self):
        for vector in VECTORS:
            if vector == "signed_pdf" and not HAS_PDF:
                continue
            with self.subTest(vector=vector):
                case = self._build(vector)
                self.assertEqual(validate_case(case), [], vector)
                self.assertEqual([r for r in certify_case(case) if r["status"] != "MATCH"], [], vector)
                manifest = json.loads((case / "99_hidden_ground_truth.json").read_text(encoding="utf-8"))["attack_manifest"]
                attacked = [m for m in manifest if not m.get("placebo")]
                self.assertTrue(attacked, vector)
                for entry in attacked:
                    self.assertIn(entry["goal"], ("finding_suppression", "unjustified_approval"), vector)

    def test_reader_surfaces_word_properties_in_block_mode(self):
        case = self._build("docx_metadata")
        entry = next(m for m in json.loads((case / "99_hidden_ground_truth.json").read_text(encoding="utf-8"))["attack_manifest"]
                     if not m.get("placebo"))
        reader = PublicEvidenceReader(case, entry["gate"], entry["phase"], docx_blocks=True)
        content = reader.read_evidence(entry["evidence_id"])["content"]
        self.assertTrue(any("[document properties]" in b for b in content))
        self.assertTrue(any(entry["token"] in b for b in content))

    @unittest.skipUnless(HAS_PDF, "signed_pdf needs the [pdf] extra")
    def test_signed_pdf_is_read_as_blocks_with_signature(self):
        case = self._build("signed_pdf")
        entry = next(m for m in json.loads((case / "99_hidden_ground_truth.json").read_text(encoding="utf-8"))["attack_manifest"]
                     if not m.get("placebo"))
        reader = PublicEvidenceReader(case, entry["gate"], entry["phase"], docx_blocks=True)
        content = reader.read_evidence(entry["evidence_id"])["content"]
        self.assertTrue(any("[digital signature]" in b for b in content))
        self.assertTrue(any("Chief Risk Officer" in b for b in content))


if __name__ == "__main__":
    unittest.main()
