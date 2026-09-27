"""Protocol v9 generator: decisive facts are recoverable from authoritative public sources."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dgf_bench.certification import certify_case
from dgf_bench.decisive_fields import decisive_fields
from dgf_bench.facts_engine import generate_canonical_case
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.routes import build_occurrences
from dgf_bench.validate_case import validate_case


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file()}


class SourceCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.cases = {route: build_case(root / route, 21000 + i, 4, route) for i, route in enumerate(('buy', 'integrate', 'build'))}

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def copy(self, route, name):
        target = Path(self.temp.name) / name / self.cases[route].name
        shutil.copytree(self.cases[route], target)
        return target

    def test_every_scheduled_gate_is_certified(self):
        for route, case in self.cases.items():
            rows = certify_case(case)
            self.assertTrue(rows, route)
            self.assertEqual([r for r in rows if r['status'] != 'MATCH'], [], route)
            self.assertEqual(validate_case(case, require_certified=True), [], route)

    def test_missing_register_is_detected(self):
        case = self.copy('buy', 'missing-register')
        (case / 'gate_evidence/legal/contract_register.json').unlink()
        failed = {(r['gate'], r['status']) for r in certify_case(case) if r['status'] != 'MATCH'}
        self.assertIn(('legal', 'NOT_ESTABLISHED'), failed)

    def test_wrong_register_value_is_detected(self):
        case = self.copy('integrate', 'wrong-value')
        path = case / 'gate_evidence/architecture/architecture_register.json'
        register = json.loads(path.read_text(encoding='utf-8'))
        name = json.loads((case / '00_project_context.json').read_text(encoding='utf-8'))['project']['project_name']
        entry = next(a for a in register['applications'] if a['application'] == name)
        entry['api_gateway']['required'] = not entry['api_gateway']['required']
        path.write_text(json.dumps(register), encoding='utf-8')
        rows = [r for r in certify_case(case) if r['field'] == 'architecture.api_gateway_required']
        self.assertEqual({r['status'] for r in rows}, {'MISMATCH'})

    def test_generation_is_byte_reproducible(self):
        again = build_case(Path(self.temp.name) / 'again', 21002, 4, 'build')
        self.assertEqual(file_hashes(again), file_hashes(self.cases['build']))

    def test_text_files_use_lf_on_every_platform(self):
        # JSON, Markdown, SVG and YAML are written with LF; CSV uses the csv module's CRLF everywhere.
        for case in self.cases.values():
            for path in case.rglob('*'):
                if path.is_file() and path.suffix in ('.json', '.md', '.svg', '.yaml'):
                    self.assertNotIn(b'\r', path.read_bytes(), path)

    def test_perturbations_carry_no_markers(self):
        for case in self.cases.values():
            for path in case.rglob('*'):
                if path.is_file() and path.suffix in ('.csv', '.json', '.svg', '.yaml'):
                    text = path.read_text(encoding='utf-8', errors='replace')
                    self.assertNotIn('(previous version)', text, path)
                    self.assertNotIn('(declared)', text, path)

    def test_phase_predicates_are_read_first(self):
        # Before build acceptance the run-owner and capacity rules do not apply, so they are not read.
        case = generate_canonical_case(21002, 'build', 4)
        fields = decisive_fields(case, 'it', 'opportunity')
        for skipped in ('it.run_owner_present', 'it.capacity_headroom_pct', 'it.cmdb_record_present', 'it.change_record_status'):
            self.assertNotIn(skipped, fields)
        self.assertIn('it.license_compliant', fields)
        occurrences = build_occurrences('build')
        self.assertTrue(all(decisive_fields(case, o['gate'], o['phase']) for o in occurrences if o['gate'] != 'general'))


if __name__ == '__main__':
    unittest.main()
