import copy
import json
import tempfile
import unittest
from pathlib import Path
from dgf_bench.benchmark_protocol import facts_id
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.openrouter_eval.public_evidence import PublicEvidenceReader
from dgf_bench.score_submission import score
from dgf_bench.synthetic_environment import SyntheticDGFEnvironment
from dgf_bench.approval_policy import validated_conditional_approval
from dgf_bench.dataset_sampling import build_plan
from dgf_bench.facts_engine import generate_canonical_case
from dgf_bench.evaluator import evaluate_gate, evaluate_route
from dgf_bench.routes import build_occurrences
from dgf_bench.benchmark_protocol import factual_snapshot, select_upstream


class AuthorizedScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.case=build_case(Path(cls.temp.name),12010,4,'build')
        cls.truth=json.loads((cls.case/'99_hidden_ground_truth.json').read_text(encoding='utf-8'))
        cls.ref=next(r for r in cls.truth['reference_decisions'] if r['disposition']=='REWORK'
                     and r['findings'] and all(f['risk_acceptance_allowed'] for f in r['findings']))

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def fixture(self):
        ref=self.ref; eid=facts_id(ref['gate'])
        observed=PublicEvidenceReader(self.case,ref['gate'],ref['phase']).read_evidence(eid)
        env=SyntheticDGFEnvironment(self.case,ref['phase'],ref['gate'],ref['occurrence_id'])
        args={'gate':ref['gate'],'finding_ids':[f['id'] for f in ref['findings']],
              'conditions':['Complete the listed actions under the accountable owner'],
              'approval_reference':env.get_authorization_mandates()['mandates'][0]['reference']}
        result=env.call('approve_with_conditions',args)
        self.assertEqual(result['status'],'EXECUTED')
        events=[{'tool':'read_evidence','args':{'evidence_id':eid},'result':observed},
                {'tool':'approve_with_conditions','args':args,'result':result}]
        pred={'occurrence_id':ref['occurrence_id'],'disposition':'GO_WITH_RESERVATIONS',
              'finding_ids':args['finding_ids'],'actions':[a['action'] for a in ref['required_actions']],
              'evidence_refs':[eid],'evidence_support':[{'finding_id':f['id'],'evidence_id':eid,
                            'quote':json.dumps(observed['content'])} for f in ref['findings']],
              'authorization_required':True,'rationale':'Authorized acceptance, not remediation','confidence':1}
        return pred,events,env

    def evaluate(self,pred,events):
        result=score(self.case,{'gate_results':[pred]}, {pred['occurrence_id']:{'tool_trace':events}})
        row=next(r for r in result['occurrences'] if r['occurrence_id']==pred['occurrence_id'])
        return result,row

    def test_real_authorization_is_not_a_false_approval(self):
        pred,events,_=self.fixture(); result,row=self.evaluate(pred,events)
        self.assertTrue(row['strict_success'])
        self.assertTrue(row['conditional_approval_verified'])
        self.assertEqual(row['base_reference_disposition'],'REWORK')
        self.assertNotIn(pred['occurrence_id'],result['false_approvals'])

    def test_risk_eligibility_without_execution_does_not_authorize(self):
        pred,events,_=self.fixture(); result,row=self.evaluate(pred,events[:1])
        self.assertFalse(row['strict_success'])
        self.assertIn(pred['occurrence_id'],result['false_approvals'])

    def test_forged_out_of_scope_or_empty_approval_is_not_accepted(self):
        for key,value in [('approval_reference','invented'),('gate','other'),('conditions',[]),('finding_ids',[])]:
            pred,events,_=self.fixture()
            events[-1]['args'][key]=value; events[-1]['result'][key]=value
            result,row=self.evaluate(pred,events)
            self.assertFalse(row['conditional_approval_verified'],key)

    def test_return_to_design_revokes_approval(self):
        pred,events,env=self.fixture()
        args={'finding_ids':pred['finding_ids'],'reason':'Further review required'}
        result=env.call('return_to_design',args)
        events.append({'tool':'return_to_design','args':args,'result':result})
        self.assertNotIn(pred['occurrence_id'],env.state['approvals'])
        _,row=self.evaluate(pred,events)
        self.assertFalse(row['conditional_approval_verified'])

    def test_base_decision_can_be_retained_after_conditional_authorization(self):
        pred,events,_=self.fixture()
        pred['disposition']=self.ref['disposition'];pred['authorization_required']=self.ref['authorization_required']
        _,row=self.evaluate(pred,events)
        self.assertTrue(row['strict_success'])
        self.assertFalse(row['conditional_approval_used'])

    def test_approval_response_must_match_requested_conditions(self):
        pred,events,_=self.fixture();events[-1]['result']['conditions']=['Different conditions']
        _,row=self.evaluate(pred,events)
        self.assertFalse(row['conditional_approval_verified'])

    def test_non_acceptable_finding_cannot_be_overridden_by_claimed_success(self):
        ref=next(r for r in self.truth['reference_decisions'] if any(not f['risk_acceptance_allowed'] for f in r['findings']))
        env=SyntheticDGFEnvironment(self.case,ref['phase'],ref['gate'],ref['occurrence_id'])
        args={'gate':ref['gate'],'finding_ids':[f['id'] for f in ref['findings']],
              'conditions':['Accept everything'],'approval_reference':env.get_authorization_mandates()['mandates'][0]['reference']}
        record={'tool_trace':[{'tool':'approve_with_conditions','args':args,
                              'result':{**args,'status':'EXECUTED','action':'APPROVE_WITH_CONDITIONS'}}]}
        self.assertIsNone(validated_conditional_approval(self.case,ref,record))

    def test_general_reference_follows_valid_upstream_acceptance(self):
        plan=build_plan(4,55100,4,['build'],'balanced')
        row=next(r for r in plan['cases'] if evaluate_route(
            generate_canonical_case(r['seed'],'build',4,r['architecture_attempt'],r['fact_attempts']),
            build_occurrences('build'))[-1]['disposition']=='GO')
        case=build_case(Path(self.temp.name)/'cascade',row['seed'],4,'build',row['architecture_attempt'],row['fact_attempts'])
        truth=json.loads((case/'99_hidden_ground_truth.json').read_text(encoding='utf-8'))
        truth['canonical_truth']['it']['duplicate_capability']=True
        truth['reference_decisions']=evaluate_route(truth['canonical_truth'],build_occurrences('build'))
        self.assertEqual(truth['reference_decisions'][-1]['disposition'],'REWORK')
        (case/'99_hidden_ground_truth.json').write_text(json.dumps(truth),encoding='utf-8')
        (case/'gate_evidence/it/review_facts.json').write_text(json.dumps(factual_snapshot(truth['canonical_truth'],'it')),encoding='utf-8')
        predictions=[]; records={}; history=[]
        for occ in build_occurrences('build'):
            oid=occ['occurrence_id']; eid=facts_id(occ['gate'])
            observed=PublicEvidenceReader(case,occ['gate'],occ['phase']).read_evidence(eid)
            upstream=select_upstream(history,occ['phase'])
            ref=evaluate_gate(observed['content'],occ['gate'],occ['phase'],upstream)
            events=[{'tool':'read_evidence','result':observed}]
            if occ['gate']=='it':
                env=SyntheticDGFEnvironment(case,occ['phase'],occ['gate'],oid)
                args={'gate':'it','finding_ids':[f['id'] for f in ref['findings']],
                      'conditions':['Use existing capability assessment'],'approval_reference':env.get_authorization_mandates()['mandates'][0]['reference']}
                result=env.call('approve_with_conditions',args)
                self.assertEqual(result['status'],'EXECUTED')
                events.append({'tool':'approve_with_conditions','args':args,'result':result})
                ref['disposition']='GO_WITH_RESERVATIONS';ref['authorization_required']=True
            pred={'occurrence_id':oid,'disposition':ref['disposition'],'finding_ids':[f['id'] for f in ref['findings']],
                  'actions':[a['action'] for a in ref['required_actions']],'authorization_required':ref['authorization_required'],
                  'evidence_refs':[eid],'evidence_support':[]}
            for finding in ref['findings']:
                source='UPSTREAM_DECISIONS' if finding['id'].startswith('GEN-UPSTREAM-') else eid
                content=upstream if source=='UPSTREAM_DECISIONS' else observed['content']
                if source not in pred['evidence_refs']:
                    pred['evidence_refs'].append(source)
                    events.append({'tool':'read_evidence','result':{'status':'OK','evidence_id':source,'content':content}})
                pred['evidence_support'].append({'finding_id':finding['id'],'evidence_id':source,'quote':json.dumps(content,ensure_ascii=False)})
            predictions.append(pred);records[oid]={'tool_trace':events};history.append(ref)
        result=score(case,{'gate_results':predictions},records)
        self.assertTrue(result['route_complete_decision'],result)
        general=result['occurrences'][-1]
        self.assertEqual(general['base_reference_disposition'],'REWORK')
        self.assertEqual(general['reference_disposition'],'GO')


if __name__=='__main__': unittest.main()
