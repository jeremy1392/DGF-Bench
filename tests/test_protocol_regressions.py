"""Offline semantic and operational regressions for the v8 protocol."""
import copy
import contextlib
import io
import json
import math
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from dgf_bench.benchmark_protocol import facts_id, factual_snapshot, select_upstream, PROTOCOL_VERSION, dataset_fingerprint
from dgf_bench.generate_dgfbench_v6 import build_case
from dgf_bench.evaluator import evaluate_gate
from dgf_bench.score_submission import score
from dgf_bench.openrouter_eval.agent_runner import AgentConfig, run_occurrence
from dgf_bench.openrouter_eval.public_evidence import PublicEvidenceReader
from dgf_bench.openrouter_eval.json_utils import normalize_submission
from dgf_bench.openrouter_eval.openrouter_client import Usage, BudgetStopped
from dgf_bench.openrouter_eval import benchmark_runner as br
from dgf_bench.openrouter_eval.aggregate import aggregate
from dgf_bench.run_full_experiment import collect_paper_metrics, write_paper_outputs
from dgf_bench.synthetic_environment import SyntheticDGFEnvironment
from dgf_bench.safe_exports import export_case
from dgf_bench.semantic_profiles import apply_overrides, enrich_profile
from dgf_bench.azure_architecture import architecture_signature
from dgf_bench.azure_icon_registry import install_from_zip, safe_extract
from dgf_bench.configure_openrouter import merge_settings
from dgf_bench.statistical_intervals import cluster_interval


def decision(disposition='GO'):
    return {'disposition':disposition,'finding_ids':[],'actions':[],'evidence_refs':[],
            'evidence_support':[],'authorization_required':False,'rationale':'offline test','confidence':0.5}


class Client:
    api_key='unused'
    def __init__(self, tools=0, cost=0.01):
        self.calls=[]; self.tools=tools; self.cost=cost

    def stats_snapshot(self): return {'http_attempts':len(self.calls)}

    def chat(self, body):
        self.calls.append(copy.deepcopy(body))
        fn={'name':'list_evidence','arguments':'{}'} if len(self.calls)<=self.tools else {
            'name':'submit_gate_decision','arguments':json.dumps(decision())}
        return {'model':body['model'],'usage':{'cost':self.cost},'choices':[{'finish_reason':'tool_calls',
                'message':{'role':'assistant','content':'','tool_calls':[{'id':str(len(self.calls)),
                'type':'function','function':fn}]}}]}


CAPS={'input_modalities':['text','image'],'supported_parameters':['tools','tool_choice','max_tokens']}

class PublicPolicyClient(Client):
    """Fake model uses only prompt and tool messages, never filesystem/oracle."""
    def chat(self, body):
        self.calls.append(copy.deepcopy(body))
        prompt=body['messages'][1]['content']
        occurrence=json.loads(prompt.split('CURRENT OCCURRENCE\n',1)[1].split('\n\nGATE CONTRACT',1)[0])
        upstream=json.loads(prompt.split('UPSTREAM AGENT OUTPUTS ALREADY PRODUCED IN THIS RUN\n',1)[1].split('\n\nStart',1)[0])
        read={}
        for message in body['messages']:
            if message['role']=='tool':
                result=json.loads(message['content'])
                if result.get('status')=='OK': read[result['evidence_id']]=result['content']
        eid=facts_id(occurrence['gate'])
        needed=[eid]+(['UPSTREAM_DECISIONS'] if occurrence['gate']=='general' else [])
        missing=next((e for e in needed if e not in read),None)
        if missing:
            fn={'name':'read_evidence','arguments':json.dumps({'evidence_id':missing})}
        else:
            ref=evaluate_gate(read[eid],occurrence['gate'],occurrence['phase'],upstream)
            out=decision(ref['disposition']); out.update(finding_ids=[f['id'] for f in ref['findings']],
                actions=list(dict.fromkeys(a['action'] for a in ref['required_actions'])),
                authorization_required=ref['authorization_required'],evidence_refs=needed)
            for finding in ref['findings']:
                source='UPSTREAM_DECISIONS' if finding['id'].startswith('GEN-UPSTREAM-') else eid
                out['evidence_support'].append({'finding_id':finding['id'],'evidence_id':source,
                                               'quote':json.dumps(read[source],ensure_ascii=False,indent=2)})
            fn={'name':'submit_gate_decision','arguments':json.dumps(out)}
        return {'model':body['model'],'usage':{'cost':.001},'choices':[{'finish_reason':'tool_calls',
            'message':{'role':'assistant','content':'','tool_calls':[{'id':str(len(self.calls)),'type':'function','function':fn}]}}]}


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='dgf-regression-')
        cls.root=Path(cls.temp.name)
        cls.cases={route:build_case(cls.root/'dataset',33100+i,4,route)
                   for i,route in enumerate(('buy','integrate','build','full_lifecycle'))}

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def truth(self, route='build'):
        return json.loads((self.cases[route]/'99_hidden_ground_truth.json').read_text(encoding='utf-8'))

    def test_public_only_reference_agent_all_routes(self):
        # No hidden file is read by this baseline. It evaluates the public policy
        # on factual observations and previous outputs, then the scorer verifies it.
        for route,case in self.cases.items():
            manifest=json.loads((case/'01_route_manifest.json').read_text(encoding='utf-8'))
            history=[]; predictions=[]; records={}
            for occ in manifest['occurrences']:
                eid=facts_id(occ['gate']); reader=PublicEvidenceReader(case,occ['gate'],occ['phase'])
                observed=reader.read_evidence(eid); self.assertEqual(observed['status'],'OK')
                upstream=select_upstream(history,occ['phase'])
                reference=evaluate_gate(observed['content'],occ['gate'],occ['phase'],upstream)
                reference['occurrence_id']=occ['occurrence_id']
                out=decision(reference['disposition']); out.update(occurrence_id=occ['occurrence_id'],
                    finding_ids=[f['id'] for f in reference['findings']],
                    actions=list(dict.fromkeys(a['action'] for a in reference['required_actions'])),
                    authorization_required=reference['authorization_required'],evidence_refs=[eid])
                events=[{'tool':'read_evidence','args':{'evidence_id':eid},'result':observed}]
                for f in reference['findings']:
                    upstream_finding=f['id'].startswith('GEN-UPSTREAM-')
                    source='UPSTREAM_DECISIONS' if upstream_finding else eid
                    content=upstream if upstream_finding else observed['content']
                    if source not in out['evidence_refs']:
                        out['evidence_refs'].append(source)
                        events.append({'tool':'read_evidence','args':{'evidence_id':source},
                                       'result':{'status':'OK','evidence_id':source,'content':content}})
                    out['evidence_support'].append({'finding_id':f['id'],'evidence_id':source,
                                                   'quote':json.dumps(content,ensure_ascii=False,indent=2)})
                predictions.append(out); history.append(reference)
                records[occ['occurrence_id']]={'tool_trace':events}
            result=score(case,{'gate_results':predictions},tool_records=records)
            self.assertTrue(result['route_complete_decision'],(route,result))
            self.assertFalse(result['execution_assessed'])
            self.assertFalse(result['route_complete_execution'])
            without_reads=score(case,{'gate_results':predictions})
            self.assertFalse(without_reads['route_complete_decision'])

    def test_no_current_answers_in_action_register(self):
        for case in self.cases.values():
            rows=(case/'shared/action_register.csv').read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(rows),1)
            svg=(case/'gate_evidence/architecture/Architecture_Diagram_Detailed.svg')
            if svg.exists(): self.assertNotIn('Hidden defect',svg.read_text(encoding='utf-8'))

    def test_previously_missing_facts_are_observable(self):
        canonical=self.truth()['canonical_truth']
        for gate,key,value in [('it','duplicate_capability',not canonical['it']['duplicate_capability']),
                               ('compliance','audit_trail','missing' if canonical['compliance']['audit_trail']!='missing' else 'complete')]:
            other=copy.deepcopy(canonical); other[gate][key]=value
            self.assertNotEqual(factual_snapshot(canonical,gate),factual_snapshot(other,gate))
            self.assertNotEqual(evaluate_gate(canonical,gate,'design'),evaluate_gate(other,gate,'design'))

    def test_policy_has_dispositions_and_authorization(self):
        contracts=json.loads((self.cases['build']/'04_gate_contracts.json').read_text(encoding='utf-8'))
        self.assertIn('NO_GO',contracts['security']['decision_policy']['rule_definition_python'])
        self.assertEqual(contracts['security']['decision_policy']['authorization_required_for'],['GO_WITH_RESERVATIONS','SUSPENSION','NO_GO'])

    def test_copied_answers_with_charter_are_not_strict(self):
        truth=self.truth(); results=[]
        for r in truth['reference_decisions']:
            results.append(dict(occurrence_id=r['occurrence_id'],disposition=r['disposition'],
                                finding_ids=[f['id'] for f in r['findings']],actions=[a['action'] for a in r['required_actions']],
                                evidence_refs=['PROJECT_CHARTER'],authorization_required=r['authorization_required']))
        result=score(self.cases['build'],{'gate_results':results})
        self.assertEqual(result['strict_gate_success_count'],0)

    def test_unknown_duplicate_occurrences_rejected(self):
        with self.assertRaises(ValueError): score(self.cases['build'],{'gate_results':[{'occurrence_id':'fake'}]})
        oid=self.truth()['reference_decisions'][0]['occurrence_id']; out={**decision(),'occurrence_id':oid}
        with self.assertRaises(ValueError): score(self.cases['build'],{'gate_results':[out,out]})

    def test_critical_misses_count_occurrences(self):
        truth=self.truth('full_lifecycle')
        expected=sum(f['severity']=='critical' for r in truth['reference_decisions'] for f in r['findings'])
        result=score(self.cases['full_lifecycle'],{'gate_results':[]})
        self.assertEqual(result['critical_miss_count'],expected)
        self.assertTrue(all('occurrence_id' in x for x in result['critical_misses']))

    def test_empty_and_invented_approval_rejected(self):
        for r in self.truth()['reference_decisions']:
            env=SyntheticDGFEnvironment(self.cases['build'],r['phase'],r['gate'],r['occurrence_id'])
            self.assertEqual(env.approve_with_conditions(r['gate'],[],[],'invented')['status'],'REJECTED')
            self.assertEqual(env.approve_with_conditions(r['gate'],[f['id'] for f in r['findings']],['condition'],'invented')['status'],'REJECTED')

    def test_scope_and_dispatch(self):
        env=SyntheticDGFEnvironment(self.cases['build'],'governance','general','x')
        with self.assertRaises(ValueError): env.call('call',{'tool':'get_cmdb_record'})
        # v9 removes the domain getters: they returned canonical facts outside the evidence files.
        with self.assertRaises(ValueError): env.call('get_iam_assignments',{})
        self.assertEqual(env.request_evidence('IAM_EXPORT')['status'],'NOT_IN_GATE_SCOPE')

    def test_action_state_is_persistent_and_isolated(self):
        case=self.cases['build']; state=self.root/'state'
        ref=next(r for r in self.truth()['reference_decisions'] if r['findings'])
        env=SyntheticDGFEnvironment(case,ref['phase'],ref['gate'],ref['occurrence_id'],state)
        result=env.call('return_to_design',{'finding_ids':[ref['findings'][0]['id']],'reason':'Observed issue'})
        self.assertEqual(result['status'],'EXECUTED')
        env2=SyntheticDGFEnvironment(case,ref['phase'],ref['gate'],ref['occurrence_id'],state)
        self.assertEqual(env2.state['workflow'][ref['occurrence_id']]['status'],'RETURNED_TO_DESIGN')
        self.assertFalse((case/'tool_trace.jsonl').exists())

    def test_vision_obeys_phase(self):
        case=self.cases['build']; manifest=json.loads((case/'01_route_manifest.json').read_text(encoding='utf-8'))
        occ=next(o for o in manifest['occurrences'] if o['gate']=='it' and o['phase']=='opportunity')
        client=Client(); run_occurrence(client,case,occ,CAPS,AgentConfig(model='fake/model',use_vision=True),[])
        self.assertIsInstance(client.calls[0]['messages'][1]['content'],str)

    def test_invalid_submission_types(self):
        for key,value in [('authorization_required','false'),('finding_ids','ABC'),('confidence',float('nan')),('confidence',2),('confidence',True)]:
            bad=decision(); bad[key]=value
            with self.assertRaises(ValueError): normalize_submission(bad,'x')

    def test_exports_do_not_delete(self):
        case=self.cases['build']
        for target in (case,case.parent,case/'child'):
            with self.assertRaises(ValueError): export_case(case,target)
        target=self.root/'exported'; export_case(case,target,'opportunity')
        self.assertFalse((target/'99_hidden_ground_truth.json').exists())
        self.assertFalse((target/'gate_evidence/architecture/Architecture_Diagram_Detailed.png').exists())
        with self.assertRaises(FileExistsError): export_case(case,target)

    def test_manifest_controls_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for name in ('listed','stale'):
                (root/name).mkdir(); (root/name/'00_project_context.json').write_text('{}')
            (root/'dataset_manifest.json').write_text(json.dumps({'cases':[{'case_dir':'listed'}]}))
            self.assertEqual([p.name for p in br.iter_cases(root)],['listed'])

    def test_case_overwrite_rejected(self):
        with self.assertRaises(FileExistsError): build_case(self.root/'dataset',33102,4,'build')

    def test_budget_stops_next_request_and_counts_once(self):
        client=Client(tools=2,cost=1); budget=br.CostBudget(.5); budget.reserve('job')
        wrapped=br.BudgetedClient(client,budget,'job'); wrapped.chat({'model':'fake/model'})
        with self.assertRaises(BudgetStopped): wrapped.chat({'model':'fake/model'})
        self.assertEqual(len(client.calls),1); self.assertEqual(budget.spent,1)

    def test_unknown_cost_is_not_free(self):
        self.assertEqual(Usage.from_response({}).unknown_cost_calls,1)
        budget=br.CostBudget(10); wrapped=br.BudgetedClient(Client(cost=None),budget,'job')
        wrapped.chat({'model':'fake/model'})
        with self.assertRaises(BudgetStopped): wrapped.chat({'model':'fake/model'})

    def test_overrides_signature_and_no_alias(self):
        p=self.truth()['canonical_truth']['architecture_profile']; before=copy.deepcopy(p)
        target='functions' if p['compute_profile']!='functions' else 'aks'
        q=apply_overrides(p,{'compute_profile':target},190,4)
        self.assertEqual(p,before); self.assertIsNot(q['defects'],p['defects'])
        self.assertEqual(q['architecture_signature'],architecture_signature(q))
        self.assertEqual(enrich_profile(q,190,4),enrich_profile(enrich_profile(q,190,4),190,4))

    def test_invalid_icon_install_preserves_old(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'azure-icons').mkdir(); old=root/'azure-icons/old.svg'; old.write_text('old')
            bad=root/'bad.zip'; bad.write_text('invalid')
            with self.assertRaises(zipfile.BadZipFile): install_from_zip(bad,root)
            self.assertTrue(old.exists())
            malicious=root/'badpath.zip'
            with zipfile.ZipFile(malicious,'w') as z: z.writestr('../sibling/file.svg','x')
            with self.assertRaises(ValueError): safe_extract(malicious,root/'destination')

    def test_accounting_retains_infrastructure_only_model(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); path=root/'fake/model/score.json'; path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'model':'fake/model','status':'INFRA_ERROR','openrouter_usage':{'cost':1.25}}))
            out=aggregate(root); self.assertEqual(out['models'][0]['total_cost_usd'],1.25)
            self.assertEqual(out['models'][0]['availability_rate'],0)
            self.assertIsNone(out['models'][0]['mean_gate_csr'])
            overall,gates=collect_paper_metrics(root,out)
            self.assertEqual(overall[0]['cases'],0); self.assertIsNone(overall[0]['gate_csr'])
            write_paper_outputs(root,overall,gates,{})

    def test_cluster_boundaries_use_cases(self):
        low,high=cluster_interval([('build',0,20)]*5)
        self.assertAlmostEqual(low,0); self.assertGreater(high,.4)

    def test_config_preserves_unrelated_values(self):
        self.assertEqual(merge_settings('# test\nOTHER=x\nOPENROUTER_API_KEY=old\n',{'OPENROUTER_API_KEY':'new'}),
                         '# test\nOTHER=x\nOPENROUTER_API_KEY=new\n')

    def test_dataset_hash_covers_documents(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td); (p/'evidence.txt').write_text('before'); a=dataset_fingerprint([p])
            (p/'evidence.txt').write_text('after'); self.assertNotEqual(a,dataset_fingerprint([p]))

    def test_resume_rejects_incompatible_before_network(self):
        out=self.root/'incompatible'; out.mkdir(); (out/'protocol_identity.json').write_text('{}')
        with patch.object(br,'OpenRouterClient',side_effect=AssertionError('Network must not be used')):
            with self.assertRaisesRegex(ValueError,'Resume rejected'):
                br.run_benchmark(self.cases['build'],['fake/model'],out,None,25,20,40,8192,0,'off',None)

    def test_full_harness_public_agent_and_resume(self):
        out=self.root/'end_to_end'; model='fake/public'
        kwargs=dict(dataset=self.root/'dataset',models=[model],out_dir=out,max_cases=None,
                    max_cost_usd=25,max_turns=6,max_tool_calls=10,max_tokens=512,temperature=0,
                    vision='off',reasoning_effort=None,workers=2)
        with patch.object(br,'OpenRouterClient',side_effect=lambda **kw:PublicPolicyClient()), \
             patch.object(br,'load_model_caps',return_value={model:CAPS}), contextlib.redirect_stdout(io.StringIO()):
            first=br.run_benchmark(**kwargs)
            scores=[json.loads(p.read_text(encoding='utf-8')) for p in out.glob('*/*/score.json')]
            self.assertEqual(len(scores),4)
            self.assertTrue(all(s.get('route_complete_decision') for s in scores),[(s.get('status'),s.get('error'),s.get('strict_gate_success_rate')) for s in scores])
            total=sum(s['openrouter_usage']['cost'] for s in scores)
            self.assertAlmostEqual(br._prior_paid_cost(out),total)
            second=br.run_benchmark(**kwargs)
            self.assertEqual(sum(r.get('status')=='RESUMED' for r in second),4)
            self.assertAlmostEqual(br._prior_paid_cost(out),total)
        aggregated=aggregate(out); overall,gates=collect_paper_metrics(out,aggregated)
        self.assertEqual(overall[0]['gate_csr'],1.0)
        write_paper_outputs(out,overall,gates,{})

    def test_job_budget_failure_keeps_paid_usage(self):
        case=self.cases['build']; out=self.root/'budget-job'; budget=br.CostBudget(.5)
        job=br.Job(1,0,0,case,'fake/budget'); budget.reserve(job.id); client=Client(tools=2,cost=1)
        with patch.object(br,'OpenRouterClient',return_value=client), contextlib.redirect_stdout(io.StringIO()):
            row=br._run_case_model_job(job=job,total_jobs=1,case_count=1,model_count=1,api_key='unused',
                caps=CAPS,out_dir=out,budget=budget,max_turns=4,max_tool_calls=10,max_tokens=100,
                temperature=0,vision='off',reasoning_effort=None,handoff_mode='agent',resume=False)
        self.assertEqual(row['status'],'BUDGET_STOP'); self.assertEqual(len(client.calls),1)
        sc=json.loads(next(out.glob('*/*/score.json')).read_text(encoding='utf-8'))
        self.assertEqual(sc['openrouter_usage']['cost'],1); self.assertEqual(budget.spent,1)
        self.assertEqual(br._prior_paid_cost(out),1)

    def test_legitimate_mandate_persists_approval(self):
        for route,case in self.cases.items():
            refs=self.truth(route)['reference_decisions']
            for r in refs:
                if r['findings'] and all(f['risk_acceptance_allowed'] for f in r['findings']):
                    env=SyntheticDGFEnvironment(case,r['phase'],r['gate'],r['occurrence_id'],self.root/'approved-state')
                    mandate=env.get_authorization_mandates()['mandates'][0]
                    result=env.call('approve_with_conditions',{'gate':r['gate'],'finding_ids':[f['id'] for f in r['findings']],
                        'conditions':['Assigned owner and due date for all findings'],'approval_reference':mandate['reference']})
                    self.assertEqual(result['status'],'EXECUTED')
                    self.assertIn(r['occurrence_id'],env.state['approvals'])
                    return
        self.fail('Test fixture needs a gate eligible for standing acceptance')

    def test_unscheduled_model_remains_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'benchmark_manifest.json').write_text(json.dumps({'models':['not_started'],'case_count':15}))
            out=aggregate(root); model=out['models'][0]
            self.assertEqual(model['planned_cases'],15)
            self.assertEqual(model['unscheduled_cases'],15)
            self.assertIsNone(model['gate_csr_micro'])
            overall,gates=collect_paper_metrics(root,out); write_paper_outputs(root,overall,gates,{})

    def test_invalid_public_snapshot_rejected_before_network(self):
        from dgf_bench.validate_case import validate_case
        case=self.cases['build']
        self.assertEqual(validate_case(case),[])
        with patch('dgf_bench.validate_case.factual_snapshot',return_value={'corrupted':True}):
            self.assertTrue(any('Factual source mismatch' in error for error in validate_case(case)))

    def test_unrelated_field_does_not_support_findings(self):
        case=self.cases['build']; ref=next(r for r in self.truth()['reference_decisions'] if r['findings'] and r['gate']!='general')
        eid=facts_id(ref['gate']); observed=PublicEvidenceReader(case,ref['gate'],ref['phase']).read_evidence(eid)
        quote='"project_name": '+json.dumps(observed['content']['project']['project_name'],ensure_ascii=False)
        self.assertIn(quote,json.dumps(observed['content'],ensure_ascii=False,indent=2))
        out={**decision(ref['disposition']),'occurrence_id':ref['occurrence_id'],
             'finding_ids':[f['id'] for f in ref['findings']],'actions':[a['action'] for a in ref['required_actions']],
             'authorization_required':ref['authorization_required'],'evidence_refs':[eid],
             'evidence_support':[{'finding_id':f['id'],'evidence_id':eid,'quote':quote} for f in ref['findings']]}
        result=score(case,{'gate_results':[out]},tool_records={ref['occurrence_id']:{'tool_trace':[{'tool':'read_evidence','result':observed}]}})
        self.assertEqual(next(r for r in result['occurrences'] if r['occurrence_id']==ref['occurrence_id'])['evidence_fidelity'],0)

    def test_interrupted_billing_and_mixed_availability_report(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); models=['fake/ok','fake/failed','fake/interrupted']
            (root/'benchmark_manifest.json').write_text(json.dumps({'models':models,'case_count':2}))
            for model,status in zip(models[:2],['OK','INFRA_ERROR']):
                p=root/br.slug(model)/'case_build'; p.mkdir(parents=True)
                (p/'score.json').write_text(json.dumps({'model':model,'status':status,'openrouter_usage':{'cost':.1},
                    'occurrences':[{'gate':'it','attempted':True,'strict_success':False}]}))
            p=root/br.slug(models[2])/'case_build'; p.mkdir(parents=True)
            (p/'usage_ledger.jsonl').write_text(json.dumps({'cost':.7,'unknown_cost_calls':0})+'\n')
            out=aggregate(root); self.assertAlmostEqual(sum(m['total_cost_usd'] for m in out['models']),.9)
            self.assertEqual(next(m for m in out['models'] if m['model']==models[2])['excluded_cases'],1)
            overall,gates=collect_paper_metrics(root,out); write_paper_outputs(root,overall,gates,{})


if __name__=='__main__': unittest.main()
