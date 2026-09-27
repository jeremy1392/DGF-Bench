"""Offline self-test: deterministic rules and evidence graphs, harness parsing, and scoring of a
generated dossier. Makes no model calls. Dossier generation is skipped when the Cairo graphics
library is unavailable, unless --require-rendering is given."""
from __future__ import annotations

import argparse
import collections
import json
import tempfile
from pathlib import Path

from dgf_bench import evaluator
from dgf_bench.evaluator import evaluate_route
from dgf_bench.evidence_graph import build_evidence_graph
from dgf_bench.facts_engine import generate_canonical_case
from dgf_bench.openrouter_eval.finding_catalog import build_catalog
from dgf_bench.openrouter_eval.json_utils import normalize_submission, parse_json_object
from dgf_bench.routes import build_occurrences

ROUTES = ['buy', 'integrate', 'build', 'full_lifecycle']
DISPOSITIONS = {'GO', 'GO_WITH_RESERVATIONS', 'REWORK', 'SUSPENSION', 'NO_GO'}
GATES = {'general', 'it', 'architecture', 'security', 'tech_readiness', 'procurement', 'legal', 'compliance'}


def check_profiles(n):
    """The evaluator is deterministic, evidence IDs are unique, and every disposition and gate occurs."""
    disp = collections.Counter(); gates = collections.Counter(); modes = collections.Counter(); routes = collections.Counter()
    for i in range(n):
        route = ROUTES[i % len(ROUTES)]; difficulty = 1 + (i % 5); seed = 90000 + i
        case = generate_canonical_case(seed, route, difficulty); occ = build_occurrences(route)
        r1 = evaluate_route(case, occ); r2 = evaluate_route(case, occ)
        assert r1 == r2, 'evaluator must be deterministic'
        for r in r1:
            disp[r['disposition']] += 1; gates[r['gate']] += 1
        graph = build_evidence_graph(case, difficulty)
        ids = [x['evidence_id'] for x in graph['nodes']]
        assert len(ids) == len(set(ids)), 'evidence IDs must be unique'
        for x in graph['nodes']:
            modes[x['_hidden_mode']] += 1
        routes[route] += 1
    assert DISPOSITIONS.issubset(disp), disp
    assert GATES.issubset(gates), gates
    return {'profiles_tested': n, 'route_distribution': dict(routes), 'dispositions': dict(disp),
            'gate_occurrences': dict(gates), 'evidence_modes': dict(modes)}


def check_harness():
    """The finding catalog is extracted from the packaged rules, and model output parsing works."""
    cat = build_catalog(Path(evaluator.__file__))
    assert 'security' in cat and any(x['id'] == 'SEC-WAF-001' for x in cat['security'])
    assert 'legal' in cat and 'general' in cat
    obj = parse_json_object('prefix {"disposition":"GO","finding_ids":[],"actions":[],"evidence_refs":[],'
                            '"authorization_required":false,"confidence":0.8,"rationale":"No findings"} suffix')
    assert obj and normalize_submission(obj, 'X')['disposition'] == 'GO'
    return {'finding_catalog_gates': sorted(cat), 'finding_count': sum(len(v) for v in cat.values())}


def rendering_available():
    from dgf_bench.azure_architecture import svg_to_png
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>'
    with tempfile.TemporaryDirectory() as td:
        try:
            svg_to_png(svg, Path(td) / 'probe.png', 4, 4)
        except RuntimeError:
            return False
    return True


def check_generated_case():
    """Declared reference answers without observed evidence reads score 0.9 and never pass strictly."""
    from dgf_bench.generate_dgfbench_v6 import build_case
    from dgf_bench.score_submission import score
    with tempfile.TemporaryDirectory() as td:
        cdir = build_case(Path(td), 99991, 4, 'build')
        gt = json.loads((cdir / '99_hidden_ground_truth.json').read_text(encoding='utf-8'))
        valid = next(n['evidence_id'] for n in gt['evidence_graph']['nodes'])
        sub = {'case_id': gt['case_id'], 'gate_results': [
            {'occurrence_id': r['occurrence_id'], 'disposition': r['disposition'],
             'finding_ids': [f['id'] for f in r['findings']], 'actions': [a['action'] for a in r['required_actions']],
             'evidence_refs': [] if not r['findings'] else [valid], 'authorization_required': r['authorization_required']}
            for r in gt['reference_decisions']]}
        s = score(cdir, sub)
        assert s['overall_score'] == 0.9 and s['strict_gate_success_count'] == 0, s
        assert not s['execution_assessed'], 'Declared actions cannot prove execution'
    return {'overall_score': s['overall_score'], 'strict_gate_success_count': s['strict_gate_success_count']}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='dgf-bench selftest', description=__doc__)
    ap.add_argument('--profiles', type=int, default=320, help='Canonical profiles to evaluate (default: 320)')
    ap.add_argument('--require-rendering', action='store_true', help='Fail instead of skipping dossier generation without Cairo')
    ap.add_argument('--report', type=Path, default=None, help='Also write the JSON report to this file')
    ns = ap.parse_args(argv)
    report = {'profiles': check_profiles(ns.profiles), 'harness': check_harness()}
    if ns.require_rendering or rendering_available():
        report['generated_case'] = check_generated_case()
    else:
        report['generated_case'] = 'SKIPPED: Cairo graphics library not available (see `dgf-bench doctor`)'
    report['status'] = 'PASS'
    text = json.dumps(report, indent=2)
    if ns.report:
        ns.report.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
