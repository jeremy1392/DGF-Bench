"""Revalidate recorded conditional approvals against occurrence-scoped mandates."""
import copy
from dgf_bench.synthetic_environment import SyntheticDGFEnvironment


def validated_conditional_approval(case_dir, reference, record):
    events=[e for e in record.get('tool_trace',[]) if e.get('tool') in ('approve_with_conditions','return_to_design')]
    if not events: return None
    # The effective reference (General's can change after valid upstream risk acceptance) adds the
    # check that an approval covers exactly the open findings; the agent-facing tool cannot do it.
    env=SyntheticDGFEnvironment(case_dir,reference['phase'],reference['gate'],reference['occurrence_id'],reference=reference)
    for event in events:
        actual=event.get('result') or {}; args=event.get('args')
        if not isinstance(actual,dict) or actual.get('status')!='EXECUTED' or not isinstance(args,dict): continue
        previous=copy.deepcopy(env.state)
        try: checked=getattr(env,event['tool'])(**args)
        except (TypeError,ValueError,KeyError):
            env.state=previous; continue
        # New explanatory fields were absent in archived traces; verify the
        # original action payload, rather than trusting a bare EXECUTED string.
        fields=('status','action','gate','finding_ids','conditions','approval_reference') if event['tool']=='approve_with_conditions' else ('status','action','finding_ids','reason')
        if checked.get('status')!='EXECUTED' or any(actual.get(k)!=checked.get(k) for k in fields):
            env.state=previous
    return env.state['approvals'].get(reference['occurrence_id'])
