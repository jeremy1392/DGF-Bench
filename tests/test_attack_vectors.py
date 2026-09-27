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

    def _attacked(self, case):
        return [m for m in json.loads((case / "99_hidden_ground_truth.json").read_text(encoding="utf-8"))["attack_manifest"]
                if not m.get("placebo")]

    @unittest.skipUnless(HAS_PDF, "signed_pdf needs the [pdf] extra")
    def test_signed_pdf_is_read_as_blocks_with_signature_by_every_attacked_gate(self):
        case = self._build("signed_pdf")
        attacked = self._attacked(case)
        self.assertGreater(len(attacked), 1)
        for entry in attacked:          # one memorandum per gate, each listed and readable by its own gate
            reader = PublicEvidenceReader(case, entry["gate"], entry["phase"], docx_blocks=True)
            self.assertIn(entry["evidence_id"], [row["evidence_id"] for row in reader.list_evidence()["evidence"]])
            content = reader.read_evidence(entry["evidence_id"])["content"]
            self.assertTrue(any("[digital signature]" in b for b in content), entry["gate"])
            self.assertTrue(any(entry["token"] in b for b in content), entry["gate"])

    def test_forwarded_email_reaches_every_attacked_gate(self):
        case = self._build("forwarded_email")
        for entry in self._attacked(case):
            result = PublicEvidenceReader(case, entry["gate"], entry["phase"], docx_blocks=True).read_evidence(entry["evidence_id"])
            self.assertEqual(result["status"], "OK", entry["gate"])
            self.assertIn(entry["token"], json.dumps(result["content"]), entry["gate"])

    def test_context_note_is_kept_in_the_project_context(self):
        case = self._build("context_note")
        unit = json.loads((case / "00_project_context.json").read_text(encoding="utf-8"))["project"]["business_unit"]
        for entry in self._attacked(case):
            self.assertIn(entry["token"], unit)

    def test_each_labelled_document_belongs_to_one_gate(self):
        for vector in ("docx_metadata", "docx_label_self", "docx_label_deny", "docx_label_noopen", "docx_label_llm"):
            with self.subTest(vector=vector):
                case = self._build(vector)
                attacked = self._attacked(case)
                self.assertEqual(len({e["path"] for e in attacked}), len(attacked))     # no document shared
                for entry in attacked:                                                 # every label survives
                    reader = PublicEvidenceReader(case, entry["gate"], entry["phase"], docx_blocks=True)
                    self.assertIn(entry["token"], json.dumps(reader.read_evidence(entry["evidence_id"])["content"]))


if __name__ == "__main__":
    unittest.main()
