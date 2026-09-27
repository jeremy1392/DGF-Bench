"""Certify that every scheduled gate of a dossier can be decided from its public sources.

For each occurrence, every decisive field (see decisive_fields) must be decodable from an
authoritative source that is in the gate's scope, visible at its phase and available, and the
decoded value must equal the canonical fact. Snapshots (REVIEW_FACTS) are never read.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from dgf_bench.decisive_fields import decisive_fields, get_field
from dgf_bench.openrouter_eval.public_evidence import PublicEvidenceReader
from dgf_bench.source_decoders import SOURCES, decode_facts, identity_context


def observe_authoritative(case_dir, gate, phase, fields):
    """Read every authoritative source of the given fields that the gate may read at this phase."""
    reader = PublicEvidenceReader(case_dir, gate, phase, docx_blocks=True)
    observations = {}
    for field in fields:
        for source in SOURCES.get(field, ()):
            if not source.authoritative or source.evidence_id in observations:
                continue
            result = reader.read_evidence(source.evidence_id)
            if result.get('status') == 'OK':
                observations[source.evidence_id] = result['content']
    return observations


def certify_case(case_dir):
    """Rows (occurrence, field, status) for one dossier; status MATCH, MISMATCH or NOT_ESTABLISHED."""
    case_dir = Path(case_dir)
    truth = json.loads((case_dir / '99_hidden_ground_truth.json').read_text(encoding='utf-8'))
    case = truth['canonical_truth']
    rows = []
    for ref in truth['reference_decisions']:
        fields = decisive_fields(case, ref['gate'], ref['phase'])
        observations = observe_authoritative(case_dir, ref['gate'], ref['phase'], fields)
        decoded = decode_facts(fields, observations, identity_context(case['project']))
        for field in fields:
            if field not in SOURCES:
                status, source = 'NO_DECODER', None
            elif field not in decoded:
                status, source = 'NOT_ESTABLISHED', None
            else:
                source, _, value = decoded[field]
                status = 'MATCH' if value == get_field(case, field) else 'MISMATCH'
            rows.append({'case': case_dir.name, 'occurrence_id': ref['occurrence_id'], 'gate': ref['gate'],
                         'phase': ref['phase'], 'field': field, 'source': source, 'status': status})
    return rows


def certify_dataset(dataset):
    dataset = Path(dataset)
    cases = sorted(p for p in dataset.iterdir() if (p / '99_hidden_ground_truth.json').is_file()) \
        if not (dataset / '99_hidden_ground_truth.json').is_file() else [dataset]
    rows = [row for case in cases for row in certify_case(case)]
    by_occurrence = collections.defaultdict(list)
    for row in rows:
        by_occurrence[(row['case'], row['occurrence_id'])].append(row['status'])
    certified = sum(all(s == 'MATCH' for s in statuses) for statuses in by_occurrence.values())
    failures = [r for r in rows if r['status'] != 'MATCH']
    return {'cases': len(cases), 'occurrences': len(by_occurrence), 'certified_occurrences': certified,
            'field_checks': len(rows), 'failures': failures,
            'failure_counts': dict(collections.Counter((r['field'], r['status']) for r in failures).most_common())}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='dgf-bench certify', description=__doc__)
    ap.add_argument('--dataset', type=Path, required=True, help='A dossier or a directory of dossiers')
    ap.add_argument('--output', type=Path, default=None, help='Write the full report as JSON')
    ns = ap.parse_args(argv)
    report = certify_dataset(ns.dataset)
    if ns.output:
        ns.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    summary = {k: v for k, v in report.items() if k != 'failures'}
    print(json.dumps(summary, indent=2))
    return 0 if report['certified_occurrences'] == report['occurrences'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
