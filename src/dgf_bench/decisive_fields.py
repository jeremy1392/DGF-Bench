"""Case fields a gate decision depends on, for one case, gate and phase.

The rules evaluate phase predicates first and short-circuit, so the fields a rule reads while
deciding one occurrence are exactly the facts a reviewer executing that rule must establish.
Reads are recorded by evaluating the rule on a wrapped case. The upstream decisions consumed by
the General gate are not case fields and are not reported here.
"""
from __future__ import annotations

from dgf_bench.evaluator import evaluate_gate

SELECTED_OFFER = 'procurement.offer'   # prefix for fields of the selected vendor's offer
# Output metadata, not a decision input.
_IGNORED = {'project.risk_owner'}


class _Recorder:
    """Read-only mapping that records the dotted path of every key read."""

    def __init__(self, data, path, reads):
        self._data, self._path, self._reads = data, path, reads

    def _wrap(self, value, path):
        if isinstance(value, dict):
            return _Recorder(value, path, self._reads)
        if isinstance(value, list):
            return [self._wrap(v, f'{path}[{i}]') for i, v in enumerate(value)]
        return value

    def __getitem__(self, key):
        path = f'{self._path}.{key}' if self._path else str(key)
        self._reads.append(path)
        return self._wrap(self._data[key], path)

    def get(self, key, default=None):
        return self[key] if key in self._data else default

    def __contains__(self, key):
        return key in self._data


def _canonical(path, case):
    """Offer reads become the selected offer's fields; reading vendors means finding the selected one."""
    if path.startswith('procurement.offers['):
        index = int(path[len('procurement.offers['):path.index(']')])
        field = path.split('].', 1)[1] if '].' in path else None
        if field == 'vendor':
            return 'procurement.selected_vendor'
        if field is None or case['procurement']['offers'][index]['vendor'] != case['procurement']['selected_vendor']:
            return None
        return f'{SELECTED_OFFER}.{field}'
    return path


def get_field(case, name):
    if name.startswith(SELECTED_OFFER + '.'):
        selected = case['procurement']['selected_vendor']
        offer = next(o for o in case['procurement']['offers'] if o['vendor'] == selected)
        return offer[name.split('.', 2)[2]]
    section, key = name.split('.', 1)
    return case[section][key]


def set_field(case, name, value):
    if name.startswith(SELECTED_OFFER + '.'):
        selected = case['procurement']['selected_vendor']
        offer = next(o for o in case['procurement']['offers'] if o['vendor'] == selected)
        offer[name.split('.', 2)[2]] = value
        return
    section, key = name.split('.', 1)
    case[section][key] = value


def decisive_fields(case, gate, phase, upstream=None):
    """Scalar case fields read while evaluating this occurrence, in reading order."""
    reads = []
    evaluate_gate(_Recorder(case, '', reads), gate, phase, upstream or [])
    fields = []
    for path in reads:
        name = _canonical(path, case)
        if not name or name in _IGNORED or '.' not in name or name in fields:
            continue
        if isinstance(get_field(case, name), (dict, list)):
            continue
        fields.append(name)
    return fields
