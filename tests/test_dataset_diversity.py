import copy
import json
import tempfile
import unittest
from pathlib import Path
from dgf_bench.dataset_diversity import audit_canonical, rule_metadata, write_report
from dgf_bench.facts_engine import generate_canonical_case
from dgf_bench.rescore_experiment import rescore_experiment
from dgf_bench.dataset_sampling import build_plan
from dgf_bench.evaluator import evaluate_route
from dgf_bench.routes import build_occurrences
from dgf_bench.benchmark_protocol import json_hash


class DiversityTests(unittest.TestCase):
    def test_identity_changes_do_not_create_semantic_diversity(self):
        case=generate_canonical_case(24000,'build',4)
        other=copy.deepcopy(case)
        other['project'].update(project_id='DIFFERENT',project_name='Different name',risk_owner='Different person')
        report=audit_canonical([case,other])
        self.assertEqual(report['unique_project_ids'],2)
        self.assertEqual(report['unique_whole_case_decision_fact_signatures'],1)
        for row in report['gates'].values():
            self.assertEqual(row['unique_relevant_fact_signatures'],1)

    def test_decision_relevant_changes_are_detected(self):
        case=generate_canonical_case(24000,'build',4); other=copy.deepcopy(case)
        other['project']['project_id']='OTHER'
        other['it']['duplicate_capability']=not case['it']['duplicate_capability']
        report=audit_canonical([case,other])
        self.assertEqual(report['gates']['it']['unique_relevant_fact_signatures'],2)
        self.assertEqual(report['gates']['it']['unique_finding_patterns'],2)

    def test_all_eight_gates_audited_and_phase_rules_separated(self):
        report=audit_canonical([generate_canonical_case(24100+i,route,4)
                               for i,route in enumerate(('buy','integrate','build'))])
        self.assertEqual(len(report['gates']),8)
        self.assertIn('IT-CHANGE-001',report['gates']['it']['phase_inapplicable_rules'])
        self.assertIn('SEC-PENTEST-001',report['gates']['security']['phase_inapplicable_rules'])
        self.assertNotIn('IT-CHANGE-001',report['gates']['it']['uncovered_phase_applicable_rules'])
        self.assertFalse(rule_metadata('it','opportunity')[1]['IT-RUN-001']['phase_applicable'])
        self.assertTrue(rule_metadata('it','deployment_closure')[1]['IT-RUN-001']['phase_applicable'])

    def test_constant_decisions_are_flagged_and_report_written(self):
        case=generate_canonical_case(24000,'build',4)
        report=audit_canonical([case])
        self.assertEqual(report['gates']['it']['decision_entropy_bits'],0)
        self.assertIn('it',report['concentrated_gates'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audit.json'; write_report(report,path)
            self.assertEqual(json.loads(path.read_text())['case_count'],1)
            self.assertTrue(path.with_suffix('.md').exists())

    def test_reanalysis_rejects_overwrite_and_nested_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); original=root/'original'; original.mkdir()
            for target in (original,original/'child',root):
                with self.assertRaises(ValueError): rescore_experiment(original,target)
            other=root/'existing'; other.mkdir()
            with self.assertRaises(FileExistsError): rescore_experiment(original,other)

    def test_balanced_plan_is_reproducible_and_balances_real_evaluated_facts(self):
        plan=build_plan(12,45100,4,['buy','integrate','build'],'balanced')
        self.assertEqual(plan,build_plan(12,45100,4,['buy','integrate','build'],'balanced'))
        cases=[]
        for row in plan['cases']:
            case=generate_canonical_case(row['seed'],row['route'],row['difficulty'],row['architecture_attempt'],row['fact_attempts'])
            self.assertEqual(json_hash(case),row['canonical_sha256'])
            cases.append(case)
        report=audit_canonical(cases)
        self.assertEqual(report['gates']['general']['decision_counts'],{'GO':9,'NO_GO':9,'REWORK':9,'SUSPENSION':9})
        self.assertEqual(report['unique_architecture_signatures'],36)
        self.assertEqual(report['unique_whole_case_decision_fact_signatures'],36)
        self.assertEqual(report['concentrated_gates'],[])

    def test_balanced_sampler_does_not_claim_unsupported_lifecycle(self):
        with self.assertRaises(ValueError): build_plan(4,45100,4,['full_lifecycle'],'balanced')

    def test_natural_plan_preserves_existing_fact_generation(self):
        plan=build_plan(2,45100,4,['buy','integrate','build'],'natural')
        for row in plan['cases']:
            case=generate_canonical_case(row['seed'],row['route'],4,row['architecture_attempt'])
            self.assertEqual(json_hash(case),row['canonical_sha256'])
            self.assertEqual(row['fact_attempts'],{})


if __name__=='__main__': unittest.main()
