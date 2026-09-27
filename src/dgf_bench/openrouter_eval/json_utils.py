from __future__ import annotations
import json, re, math


def parse_json_object(text: str):
    text=(text or "").strip()
    try:
        obj=json.loads(text)
        if isinstance(obj,dict): return obj
    except Exception:
        pass
    m=re.search(r"\{.*\}",text,re.S)
    if m:
        try:
            obj=json.loads(m.group(0))
            if isinstance(obj,dict): return obj
        except Exception:
            pass
    return None


def normalize_submission(obj:dict, occurrence_id:str, condition:str='facts'):
    if not isinstance(obj, dict): raise ValueError('Submission must be an object')
    required = {'disposition','finding_ids','actions','evidence_refs','authorization_required','rationale','confidence'}
    if required - obj.keys(): raise ValueError('Missing fields: ' + ', '.join(sorted(required - obj.keys())))
    if obj.keys() - required - {'occurrence_id','evidence_support'}: raise ValueError('Unknown submission fields')
    for name in ('finding_ids','actions','evidence_refs'):
        if not isinstance(obj[name],list) or any(not isinstance(x,str) or not x for x in obj[name]):
            raise ValueError(name + ' must be an array of nonempty strings')
        if len(set(obj[name])) != len(obj[name]): raise ValueError(name + ' contains duplicates')
    if type(obj['authorization_required']) is not bool: raise ValueError('authorization_required must be boolean')
    if not isinstance(obj['rationale'],str): raise ValueError('rationale must be text')
    confidence=obj['confidence']
    if type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('confidence must be finite and between 0 and 1')
    support=obj.get('evidence_support',[])
    if not isinstance(support,list): raise ValueError('evidence_support must be an array')
    if condition == 'facts':
        for item in support:
            if not isinstance(item,dict) or set(item) != {'finding_id','evidence_id','quote'} or any(not isinstance(v,str) or not v.strip() for v in item.values()):
                raise ValueError('Evidence support requires finding_id, evidence_id and a nonempty exact quote')
    else:
        for item in support:
            if not isinstance(item,dict) or set(item) != {'finding_id','observation_id','json_pointer'} or any(not isinstance(v,str) or not v.strip() for v in item.values()):
                raise ValueError('Evidence support requires finding_id, observation_id and json_pointer')
            if not item['json_pointer'].startswith('/'):
                raise ValueError('json_pointer must start with /')
    allowed={"GO","GO_WITH_RESERVATIONS","REWORK","SUSPENSION","NO_GO"}
    out={
        "occurrence_id":occurrence_id,
        "disposition":obj['disposition'],
        "finding_ids":[str(x) for x in (obj.get("finding_ids") or [])],
        "actions":[str(x) for x in (obj.get("actions") or [])],
        "evidence_refs":[str(x) for x in (obj.get("evidence_refs") or [])],
        "authorization_required":bool(obj.get("authorization_required",False)),
        "rationale":str(obj.get("rationale") or "")[:4000],
        "confidence":float(obj.get("confidence") or 0.0),
        "evidence_support":support,
    }
    if out["disposition"] not in allowed:
        raise ValueError(f"Invalid disposition: {out['disposition']!r}")
    return out
