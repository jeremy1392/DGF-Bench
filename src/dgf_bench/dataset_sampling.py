"""Reproducible, explicitly outcome-stratified synthetic sampling.

Balanced coverage is a case-control benchmark, NOT an estimate of enterprise
prevalence. Rules and labels are never edited: facts are rejection-sampled and
then evaluated. Original natural sampling remains available.
"""
from collections import Counter, defaultdict
import random
import dgf_bench.facts_engine as facts
from dgf_bench.azure_architecture import architecture_signature
from dgf_bench.benchmark_protocol import json_hash
from dgf_bench.dataset_diversity import rule_metadata
from dgf_bench.evaluator import PRIORITY, evaluate_gate, evaluate_route
from dgf_bench.routes import build_occurrences

SAMPLING_VERSION='balanced-coverage-v1'


def _compatible(case,targets):
    a=case['architecture_profile']; p=case['project']; security=case['security']
    target=targets.get('security')
    if target and target!='NO_GO':
        if security['internet_exposed'] and not security['waf_present']: return False
        if p['data_classification'] in ('Confidential','Restricted') and not security['private_endpoints']: return False
        if target in ('GO','GO_WITH_RESERVATIONS'):
            if not a['conditional_access'] or not a['logs_to_siem']: return False
            if security['internet_exposed'] and str(security['waf_mode']).lower()=='detection': return False
    target=targets.get('tech_readiness')
    if target:
        if (target=='NO_GO') != (not a['backup_enabled']): return False
        if target in ('GO','GO_WITH_RESERVATIONS') and (not a['restore_tested'] or (a['multi_region'] and not a['dr_tested'])): return False
    if targets.get('architecture') in ('GO','GO_WITH_RESERVATIONS'):
        f=case['architecture']
        if f['api_gateway_required'] and not f['api_gateway_present']: return False
        if p['business_criticality'] in ('High','Critical') and not (a['multi_az'] or a['multi_region']): return False
    return True


def _gate_facts(case,gate,draw,difficulty):
    project,arch=case['project'],case['architecture_profile']
    if gate=='general': return facts._general(draw,project,difficulty)
    if gate=='procurement': return facts._vendors(draw,project,difficulty)
    if gate=='legal': return facts._legal(draw,project,case['procurement'],difficulty)
    func={'it':facts._it,'architecture':facts._architecture_facts,'security':facts._security,
          'tech_readiness':facts._tech,'compliance':facts._compliance}[gate]
    return func(draw,project,arch,difficulty)


def build_plan(cases_per_route,seed,difficulty,routes,policy='natural'):
    if cases_per_route<1: raise ValueError('cases_per_route must be positive')
    if policy not in ('natural','balanced'): raise ValueError('Unknown sampling policy')
    if policy=='balanced' and 'full_lifecycle' in routes:
        raise ValueError('Balanced sampling currently supports Buy/Integrate/Build; full lifecycle requires natural sampling')
    rng=random.Random(seed+990_001); counts=defaultdict(Counter); used=set(); plan=[]
    for route in routes:
        outcomes=['GO','REWORK','SUSPENSION','NO_GO']
        general_targets=[outcomes[i%len(outcomes)] for i in range(cases_per_route)]
        rng.shuffle(general_targets)
        occurrences=build_occurrences(route)
        for index in range(cases_per_route):
            current_seed=seed+len(plan); targets={}
            if policy=='balanced':
                general_target=general_targets[index]
                eligible={o['gate']:sorted({'GO'}|{m['disposition'] for m in rule_metadata(o['gate'],o['phase'])[1].values() if m['phase_applicable']}) for o in occurrences if o['gate']!='general'}
                forced=rng.choice([g for g,choices in eligible.items() if 'NO_GO' in choices]) if general_target=='NO_GO' else None
                for gate,choices in eligible.items():
                    # A GO General may inherit reservations but no blockers.
                    cap='GO_WITH_RESERVATIONS' if general_target=='GO' else general_target
                    allowed=[choice for choice in choices if PRIORITY[choice]<=PRIORITY[cap]]
                    rng.shuffle(allowed)
                    targets[gate]='NO_GO' if gate==forced else min(allowed,key=lambda x:counts[gate][x])
                targets['general']=general_target
            for architecture_attempt in range(10001):
                case=facts.generate_canonical_case(current_seed,route,difficulty,architecture_attempt)
                signature=architecture_signature(case['architecture_profile'])
                if signature not in used and (policy=='natural' or _compatible(case,targets)): break
            else: raise RuntimeError('Architecture sampling exhausted for '+str(current_seed))
            attempts={}; upstream=[]
            if policy=='balanced':
                for occurrence in occurrences:
                    gate=occurrence['gate']
                    for attempt in range(50001):
                        case[gate]=_gate_facts(case,gate,current_seed+1_000_003*attempt,difficulty)
                        ref=evaluate_gate(case,gate,occurrence['phase'],upstream)
                        if ref['disposition']==targets[gate]: break
                    else: raise RuntimeError(f'Fact sampling exhausted: {current_seed} {gate} {targets[gate]}')
                    attempts[gate]=attempt; upstream.append(ref)
                # Assert reproducibility and route consistency, not just isolated gates.
                recreated=facts.generate_canonical_case(current_seed,route,difficulty,architecture_attempt,attempts)
                if recreated!=case: raise AssertionError('Fact sampling replay mismatch')
                for reference in evaluate_route(case,occurrences):
                    if reference['disposition']!=targets[reference['gate']]: raise AssertionError('Route target mismatch')
                    counts[reference['gate']][reference['disposition']]+=1
            used.add(signature)
            plan.append({'seed':current_seed,'route':route,'difficulty':difficulty,
                         'architecture_attempt':architecture_attempt,'architecture_signature':signature,
                         'fact_attempts':attempts,'canonical_sha256':json_hash(case)})
    return {'schema':'DGF-Sampling-Plan-v1','policy':policy,
            'sampling_version':SAMPLING_VERSION if policy=='balanced' else 'natural-v1',
            'seed':seed,'cases_per_route':cases_per_route,'difficulty':difficulty,'cases':plan,
            'note':'Balanced: equal General outcome strata, approximately balanced feasible specialist decisions conditional on upstream consistency; fact rejection sampling, never label editing. Not representative prevalence.' if policy=='balanced' else 'Original generator distribution, with unique architecture rejection sampling.'}
