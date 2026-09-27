"""Evidence contract for the docs and attack conditions.

The harness gives every successful read an observation_id derived from the evidence ID, its
version and the exact content returned, so the same observation always has the same identifier
and nothing depends on the order of reads. The agent cites an observation_id and an RFC 6901
JSON pointer; the scorer resolves the pointer in the recorded content, which the harness wrote
and the scorer re-checks against the public dossier.
"""
from __future__ import annotations

import hashlib
import json

from dgf_bench.benchmark_protocol import finding_fact_fields


def observation_id(evidence_id, version, content):
    payload = json.dumps({'evidence_id': evidence_id, 'version': version, 'content': content},
                         sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return 'obs-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def resolve_pointer(content, pointer):
    """Value at an RFC 6901 pointer; raises LookupError. Array indices must be canonical integers."""
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise LookupError('pointer must start with /')
    value = content
    for raw in pointer[1:].split('/'):
        token = raw.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not token.isdigit() or (len(token) > 1 and token[0] == '0') or int(token) >= len(value):
                raise LookupError('invalid array index')
            value = value[int(token)]
        elif isinstance(value, dict):
            if token not in value:
                raise LookupError('missing key')
            value = value[token]
        else:
            raise LookupError('pointer goes through a scalar')
    return value


def finding_premises(finding_id, decisive):
    """Decisive fields that the finding's rule condition tests (dotted names, in reading order)."""
    local = finding_fact_fields().get(finding_id, set())
    return [field for field in decisive if field.rsplit('.', 1)[-1] in local]


UPSTREAM_DISPOSITION = {'GEN-UPSTREAM-NOGO': 'NO_GO', 'GEN-UPSTREAM-SUSPEND': 'SUSPENSION', 'GEN-UPSTREAM-REWORK': 'REWORK'}
