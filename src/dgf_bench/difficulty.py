"""Protocol v9 difficulty levers for systems of record (pre-registered; enabled after pilot stage 1).

The pilot showed that the best models decided almost every docs gate correctly when each
authoritative record held exactly one clean row for the project. These levers make the records
look like enterprise system exports, without changing any fact:

- history: several dated rows or versions; the most recent one prevails; row order is shuffled;
- units and aliases: amounts in kEUR, latencies in seconds, restore times in minutes, and status
  words such as "executed" for signed or "absent" for missing (canonical units and vocabularies
  are given in the policy glossary);
- entities: registers also list other projects, applications or vendors, so the agent must
  select the project's own row.

Older rows and other entities are derived from the canonical facts with seeded hashes, so
generation stays deterministic and the canonical case (and every REVIEW_FACTS snapshot) is
unchanged. The records are rewritten after the normal emission.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from dgf_bench.document_factory import write_csv, write_json
from dgf_bench.source_decoders import RECORD_OWNERS, alias

OTHER_APPLICATIONS = ['Payroll Hub', 'Vendor Portal', 'Claims Workbench', 'Field Service App', 'Treasury Ledger',
                      'Learning Portal', 'Asset Tracker', 'Pricing Engine', 'Contract Vault', 'Travel Desk']


class _Draw:
    """Seeded choices keyed by name, so each value is independent of the others' order."""

    def __init__(self, case):
        self.seed = case['seed']

    def unit(self, *key):
        h = hashlib.sha256(f"{self.seed}:difficulty:{':'.join(map(str, key))}".encode()).digest()
        return int.from_bytes(h[:8], 'big') / 2 ** 64

    def pick(self, options, *key):
        return options[int(self.unit(*key) * len(options))]

    def count(self, low, high, *key):
        return low + int(self.unit(*key) * (high - low + 1))

    def shuffled(self, rows, *key):
        return sorted(rows, key=lambda row: self.unit(*key, json.dumps(row, sort_keys=True, default=str)))


def _dates(case, n):
    """n increasing ISO dates after the project start; the last one is the current record."""
    start = date.fromisoformat(case['project']['start_date'])
    return [(start + timedelta(days=12 + 17 * i)).isoformat() for i in range(n)]


def _history(draw, case, key, current, older):
    """Rows of a dated record: len(older) superseded versions, then the current one, shuffled."""
    stamps = _dates(case, len(older) + 1)
    rows = [{'as_of': stamps[i], **row} for i, row in enumerate(older)] + [{'as_of': stamps[-1], **current}]
    return draw.shuffled(rows, key)


def _owned(rows, evidence_id):
    """Mark entries as recorded by the record's owner."""
    return [{**row, 'recorded_by': RECORD_OWNERS[evidence_id]} for row in rows]


def _others(draw, case, key, n):
    """Identifiers and names of other projects in the same registers."""
    out = []
    for i in range(n):
        tag = hashlib.sha256(f"{case['seed']}:{key}:{i}".encode()).hexdigest()[:5].upper()
        out.append({'project_id': f"DGF-PRT-{tag}", 'name': f"{draw.pick(OTHER_APPLICATIONS, key, 'name', i)} [{tag}]"})
    return out


def _older_status(value, vocabulary, draw, *key):
    choices = [v for v in vocabulary if v != value]
    return draw.pick(choices, *key) if choices else value


def rewrite_records(case_dir: Path, case):
    d = _Draw(case); p = case['project']; root = Path(case_dir) / 'gate_evidence'
    g, it, af, sec, tr = case['general'], case['it'], case['architecture'], case['security'], case['tech_readiness']
    le, co, pr, arch = case['legal'], case['compliance'], case['procurement'], case['architecture_profile']
    keur = lambda eur: round(eur / 1000, 3)

    # General: approval history in kEUR; the portfolio lists other projects.
    older = [{'requested_keur': keur(g['budget_requested_eur']), 'approved_keur': keur(int(g['budget_approved_eur'] * f)),
              'approver': 'CFO Office', 'status': 'SUPERSEDED'} for f in (0.6, 0.85)[:d.count(1, 2, 'budget')]]
    write_csv(root / 'general/budget_approval.csv', _owned(_history(d, case, 'budget', {
        'requested_keur': keur(g['budget_requested_eur']), 'approved_keur': keur(g['budget_approved_eur']),
        'approver': 'CFO Office', 'status': 'APPROVED' if g['budget_approved_eur'] >= g['budget_requested_eur'] else 'PARTIAL'}, older),
        'BUDGET_APPROVAL'), ['as_of', 'requested_keur', 'approved_keur', 'approver', 'status', 'recorded_by'])
    portfolio = [{'project_id': p['project_id'], 'priority': g['portfolio_priority'], 'strategic_alignment': g['strategic_alignment'],
                  'requested_eur': g['budget_requested_eur'], 'approved_eur': g['budget_approved_eur'], 'roi': g['roi'],
                  'change_plan_status': g['change_plan_status'], 'target_go_live': p['target_go_live']}]
    for i, o in enumerate(_others(d, case, 'portfolio', 3)):
        requested = 100000 + int(d.unit('portfolio', 'req', i) * 900000)
        portfolio.append({'project_id': o['project_id'], 'priority': d.pick(['P0', 'P1', 'P2', 'P3'], 'portfolio', 'prio', i),
                          'strategic_alignment': d.pick(['strong', 'partial', 'weak'], 'portfolio', 'align', i),
                          'requested_eur': requested, 'approved_eur': int(requested * d.pick([0.8, 1.0, 1.2], 'portfolio', 'appr', i)),
                          'roi': round(d.unit('portfolio', 'roi', i) * 0.3, 3),
                          'change_plan_status': d.pick(['complete', 'partial', 'draft', 'missing'], 'portfolio', 'plan', i),
                          'target_go_live': p['target_go_live']})
    write_csv(root / 'general/portfolio_snapshot.csv', d.shuffled(portfolio, 'portfolio'), list(portfolio[0]))

    # IT: CMDB, capacity and lifecycle list other services; catalog decisions and licence position have histories.
    cmdb = [{'application': p['project_name'], 'record_present': it['cmdb_record_present'],
             'run_owner': p['service_owner'] if it['run_owner_present'] else '', 'support_model': it['support_model'],
             'continuity_class': it['service_continuity_class']}]
    for i, o in enumerate(_others(d, case, 'cmdb', 2)):
        cmdb.append({'application': o['name'], 'record_present': d.unit('cmdb', 'rec', i) < .8,
                     'run_owner': f'Service Owner {i + 1}' if d.unit('cmdb', 'own', i) < .8 else '',
                     'support_model': d.pick(['24x7_internal', 'business_hours', 'product_team', 'vendor_only'], 'cmdb', 'sup', i),
                     'continuity_class': d.pick(['gold', 'silver', 'bronze'], 'cmdb', 'cls', i)})
    write_csv(root / 'it/cmdb_export.csv', d.shuffled(cmdb, 'cmdb'), list(cmdb[0]))
    decisions = [{'catalog_status': _older_status(it['catalog_status'], ['standard', 'approved_exception', 'non_standard'], d, 'cat', i),
                  'waiver_present': not it['waiver_present'] if d.unit('cat', 'waiver', i) < .5 else it['waiver_present'],
                  'waiver_expiry_days': it['waiver_expiry_days'],
                  'existing_capability_overlap': not it['duplicate_capability'] if d.unit('cat', 'dup', i) < .5 else it['duplicate_capability']}
                 for i in range(d.count(1, 2, 'cat'))]
    write_json(root / 'it/technology_catalog.json', {'technology': it['hosting_model'], 'hosting_model': it['hosting_model'],
        'decisions': _owned(_history(d, case, 'catalog', {'catalog_status': it['catalog_status'], 'waiver_present': it['waiver_present'],
                                                          'waiver_expiry_days': it['waiver_expiry_days'],
                                                          'existing_capability_overlap': it['duplicate_capability']}, decisions),
                            'TECH_CATALOG')})
    write_csv(root / 'it/license_position.csv', _owned(_history(d, case, 'licence',
        {'license_compliant': it['license_compliant'], 'annual_cost_eur': it['annual_license_cost_eur']},
        [{'license_compliant': not it['license_compliant'] if d.unit('lic', i) < .6 else it['license_compliant'],
          'annual_cost_eur': int(it['annual_license_cost_eur'] * 0.9)} for i in range(d.count(1, 2, 'lic'))]), 'LICENSE_POSITION'),
        ['as_of', 'license_compliant', 'annual_cost_eur', 'recorded_by'])
    lifecycle = [{'project_id': p['project_id'], 'technology': it['hosting_model'], 'eol_months': it['technology_eol_months'],
                  'capacity_headroom_pct': it['capacity_headroom_pct']}]
    capacity = [{'service': p['project_name'], 'capacity_headroom_pct': it['capacity_headroom_pct'],
                 'continuity_class': it['service_continuity_class'], 'support_model': it['support_model']}]
    for i, o in enumerate(_others(d, case, 'it-services', 2)):
        headroom = 5 + int(d.unit('cap', i) * 40)
        lifecycle.append({'project_id': o['project_id'], 'technology': d.pick(['aks', 'app_service', 'vm_ha', 'functions'], 'eol', 'tech', i),
                          'eol_months': 3 + int(d.unit('eol', i) * 48), 'capacity_headroom_pct': headroom})
        capacity.append({'service': o['name'], 'capacity_headroom_pct': headroom,
                         'continuity_class': d.pick(['gold', 'silver', 'bronze'], 'cap', 'cls', i),
                         'support_model': d.pick(['24x7_internal', 'business_hours'], 'cap', 'sup', i)})
    write_csv(root / 'it/lifecycle_eol.csv', d.shuffled(lifecycle, 'eol'), list(lifecycle[0]))
    write_csv(root / 'it/capacity_report.csv', d.shuffled(capacity, 'cap'), list(capacity[0]))

    # Architecture: one register entry per application; latencies in seconds.
    apps = [{'application': p['project_name'], 'api_gateway': {'required': af['api_gateway_required'], 'deployed': af['api_gateway_present']},
             'latency_s': {'target_p95': af['latency_target_ms'] / 1000, 'measured_p95': af['measured_latency_ms'] / 1000, 'measurement_source': 'APM'},
             'exit_plan_status': af['reversibility_status'], 'data_domain_owner': p['business_owner'] if af['data_owner_present'] else None,
             'availability': {'zone_redundant': arch['multi_az'], 'secondary_region': arch['multi_region']}}]
    for i, o in enumerate(_others(d, case, 'arch', 2)):
        target = d.pick([50, 100, 200, 300], 'arch', 'target', i)
        apps.append({'application': o['name'], 'api_gateway': {'required': d.unit('arch', 'req', i) < .5, 'deployed': d.unit('arch', 'dep', i) < .6},
                     'latency_s': {'target_p95': target / 1000, 'measured_p95': int(target * (0.5 + d.unit('arch', 'lat', i))) / 1000, 'measurement_source': 'APM'},
                     'exit_plan_status': d.pick(['tested', 'documented', 'draft', 'missing'], 'arch', 'exit', i),
                     'data_domain_owner': f'Data Owner {i + 1}' if d.unit('arch', 'own', i) < .7 else None,
                     'availability': {'zone_redundant': d.unit('arch', 'az', i) < .6, 'secondary_region': d.unit('arch', 'dr', i) < .3}})
    write_json(root / 'architecture/architecture_register.json', {'applications': d.shuffled(apps, 'arch')})

    # Security: the scan keeps remediated critical findings, which do not count as open.
    vulns = [{'id': f'CVE-2026-{8000 + i}', 'severity': 'Critical', 'status': 'Open'} for i in range(sec['critical_vulns_open'])]
    vulns += [{'id': f'CVE-2026-{9000 + i}', 'severity': 'High', 'status': 'Open'} for i in range(sec['high_vulns_open'])]
    vulns += [{'id': f'CVE-2026-{7000 + i}', 'severity': 'Critical', 'status': 'Remediated'} for i in range(d.count(0, 2, 'vuln'))]
    vulns += [{'id': 'CVE-2026-0001', 'severity': 'Medium', 'status': 'Remediated'}]
    write_csv(root / 'security/vulnerability_scan.csv', d.shuffled(vulns, 'vuln'), ['id', 'severity', 'status'])

    # Tech readiness: test histories; restore time in minutes; readiness items with aliases and history.
    restore = {'tested': tr['restore_tested'], 'target_rto_hours': tr['target_rto_hours'],
               'measured_restore_minutes': round(tr['measured_restore_hours'] * 60, 3), 'target_rpo_minutes': tr['target_rpo_minutes'],
               'measured_data_loss_minutes': tr['measured_data_loss_minutes']}
    older = [{**restore, 'tested': False if d.unit('restore', i) < .5 else tr['restore_tested'],
              'measured_restore_minutes': round(tr['measured_restore_hours'] * 60 * (1.3 + .4 * i), 3),
              'measured_data_loss_minutes': tr['measured_data_loss_minutes'] + 5 * (i + 1)} for i in range(d.count(1, 2, 'restore'))]
    write_csv(root / 'tech_readiness/restore_test.csv', _owned(_history(d, case, 'restore', restore, older), 'RESTORE_TEST'),
              ['as_of', *restore, 'recorded_by'])
    load = {'peak_test_pct': tr['load_test_pct_of_peak'], 'p95_ms': tr['p95_ms'], 'test_completion_pct': tr['test_completion_pct']}
    older = [{'peak_test_pct': max(10, tr['load_test_pct_of_peak'] - 20 * (i + 1)), 'p95_ms': tr['p95_ms'],
              'test_completion_pct': tr['test_completion_pct']} for i in range(d.count(1, 2, 'load'))]
    write_csv(root / 'tech_readiness/load_test.csv', _owned(_history(d, case, 'load', load, older), 'LOAD_TEST'),
              ['as_of', *load, 'recorded_by'])
    items = []
    for item, current, vocabulary in (('Production runbook', tr['runbook_status'], ['approved', 'draft', 'missing']),
                                      ('On-call rota', 'defined' if tr['on_call_defined'] else 'not_defined', ['defined', 'not_defined']),
                                      ('Operational handover', 'signed' if tr['handover_signed'] else 'unsigned', ['signed', 'unsigned'])):
        older = [{'item': item, 'status': alias(_older_status(current, vocabulary, d, 'ops', item, i)), 'owner': 'Operations'}
                 for i in range(d.count(0, 2, 'ops', item))]
        items += _history(d, case, f'ops:{item}', {'item': item, 'status': alias(current), 'owner': 'Operations'}, older)
    write_csv(root / 'tech_readiness/operational_readiness.csv', _owned(d.shuffled(items, 'ops'), 'OPERATIONS_READINESS'),
              ['as_of', 'item', 'status', 'owner', 'recorded_by'])
    blockers = [{'blocker_id': f"BLK-{p['project_code']}-{i:02d}", 'severity': 'medium', 'status': 'Closed', 'owner': 'Product Team'}
                for i in range(d.count(1, 3, 'blk'))]
    blockers += [{'blocker_id': f"BLK-{p['project_code']}-{50 + i:02d}", 'severity': 'high', 'status': 'Open', 'owner': 'Product Team'}
                 for i in range(tr['open_blockers'])]
    write_csv(root / 'tech_readiness/release_blockers.csv', d.shuffled(blockers, 'blk'), ['blocker_id', 'severity', 'status', 'owner'])

    # Procurement: award amounts in kEUR; due-diligence history per vendor with aliases.
    selected = next(o for o in pr['offers'] if o['vendor'] == pr['selected_vendor'])
    write_json(root / 'procurement/award_decision.json', {'selected_vendor': pr['selected_vendor'],
        'purchasing_budget_keur': keur(pr['purchasing_budget_eur']), 'awarded_tco_3y_keur': keur(selected['tco_3y_eur']),
        'decision_status': 'provisional'})
    rows = []
    for o in pr['offers']:
        current = {'vendor': o['vendor'], 'mandatory_failed': o['mandatory_criteria_failed'], 'sanctions': alias(o['sanctions']),
                   'due_diligence': alias(o['due_diligence']), 'references_checked': o['references_checked']}
        older = [{**current, 'sanctions': alias('pending'), 'due_diligence': alias('pending'), 'references_checked': False}
                 for _ in range(d.count(0, 1, 'dd', o['vendor']))]
        rows += _history(d, case, f"dd:{o['vendor']}", current, older)
    write_csv(root / 'procurement/due_diligence.csv', _owned(d.shuffled(rows, 'dd'), 'DUE_DILIGENCE'),
              ['as_of', 'vendor', 'mandatory_failed', 'sanctions', 'due_diligence', 'references_checked', 'recorded_by'])

    # Legal: contract versions for this project and another project's contract, with aliases.
    def contract(project_id, version, values):
        return {'project_id': project_id, 'contract_version': version, 'clauses': {
            'data_processing_agreement': alias(values['dpa_status']), 'customer_audit_rights': alias(values['audit_right']),
            'security_log_export': alias(values['log_export_clause']), 'ip_ownership': alias(values['ip_ownership']),
            'exit_assistance_days': values['exit_assistance_days'], 'liability_cap_x_annual_fees': values['liability_cap_multiplier'],
            'security_liability_carve_out': values['security_carveout']},
            'insurance_certificate_valid': values['insurance_valid'], 'signatory_authority_valid': values['signing_authority_valid']}
    older_terms = {**le, 'dpa_status': _older_status(le['dpa_status'], ['signed', 'draft', 'missing'], d, 'msa', 'dpa'),
                   'audit_right': _older_status(le['audit_right'], ['full', 'limited', 'reports_only', 'none'], d, 'msa', 'audit'),
                   'exit_assistance_days': d.pick([0, 30, 60, 90], 'msa', 'exit'), 'insurance_valid': not le['insurance_valid']}
    versions = _history(d, case, 'contracts', contract(p['project_id'], le['contract_version'], le),
                        [contract(p['project_id'], le['contract_version'] + '-draft', older_terms)])
    other = _others(d, case, 'contract', 1)[0]
    other_terms = {**le, 'dpa_status': d.pick(['signed', 'draft'], 'other', 'dpa'), 'audit_right': d.pick(['full', 'limited'], 'other', 'audit'),
                   'exit_assistance_days': d.pick([30, 90], 'other', 'exit'), 'insurance_valid': True, 'signing_authority_valid': True}
    versions.append({'as_of': _dates(case, 1)[0], **contract(other['project_id'], 'MSA-OTHER-1.0', other_terms)})
    write_json(root / 'legal/contract_register.json', {'contracts': _owned(d.shuffled(versions, 'contracts'), 'CONTRACT_REGISTER')})

    # Compliance: data inventory lists other projects' datasets; the GRC register keeps control histories with aliases.
    inventory = [{'project_id': p['project_id'], 'dataset': 'Core business data', 'classification': p['data_classification'],
                  'business_criticality': p['business_criticality'], 'personal_data': p['personal_data'],
                  'actual_region': p['primary_region'], 'required_residency': co['required_residency']}]
    for i, o in enumerate(_others(d, case, 'inventory', 2)):
        inventory.append({'project_id': o['project_id'], 'dataset': f'{o["name"]} data',
                          'classification': d.pick(['Public', 'Internal', 'Confidential', 'Restricted'], 'inv', 'cls', i),
                          'business_criticality': d.pick(['Low', 'Medium', 'High', 'Critical'], 'inv', 'crit', i),
                          'personal_data': d.unit('inv', 'pd', i) < .5, 'actual_region': d.pick(['West Europe', 'UK South', 'UAE North'], 'inv', 'reg', i),
                          'required_residency': d.pick(['EU', 'UK', 'No restriction'], 'inv', 'res', i)})
    write_csv(root / 'compliance/data_inventory.csv', d.shuffled(inventory, 'inventory'), list(inventory[0]))
    controls = []
    for control, required, current, vocabulary, detail in (
            ('DPIA', co['dpia_required'], co['dpia_status'], ['complete', 'draft', 'missing', 'not_required'], 'Data protection impact assessment'),
            ('Data residency', True, 'compliant' if co['residency_compliant'] else 'non_compliant', ['compliant', 'non_compliant'],
             f"Required: {co['required_residency']}; actual: {p['primary_region']}"),
            ('Audit trail', True, co['audit_trail'], ['complete', 'partial', 'missing'], 'Compliance audit evidence'),
            ('Regulatory mapping', True, co['regulatory_mapping'], ['complete', 'partial', 'missing'], 'Obligations mapped to controls'),
            ('Accessibility', True, co['accessibility_status'], ['pass', 'minor_findings', 'major_findings', 'not_assessed'], 'WCAG 2.2 AA assessment'),
            ('Export control', True, co['export_control'], ['clear', 'pending', 'restricted'], 'Export-control screening')):
        older = [{'control': control, 'required': required, 'status': alias(_older_status(current, vocabulary, d, 'grc', control, i)), 'detail': detail}
                 for i in range(d.count(0, 2, 'grc', control))]
        controls += _history(d, case, f'grc:{control}', {'control': control, 'required': required, 'status': alias(current), 'detail': detail}, older)
    write_csv(root / 'compliance/compliance_register.csv', _owned(d.shuffled(controls, 'grc'), 'COMPLIANCE_REGISTER'),
              ['as_of', 'control', 'required', 'status', 'detail', 'recorded_by'])


# ---- compositional records (difficulty probe) -------------------------------------------------
# Six facts are no longer stated but must be derived from what a record holds: approved budget as
# the sum of approved tranches, three-year TCO as the sum of yearly costs, restore time from start
# and end timestamps, capacity headroom as 100 minus peak utilization, months to end of life from
# two dates, and exit assistance from the contract clause text. Applied to a dossier already
# rewritten by rewrite_records; the canonical case is unchanged.

_NUMBER_WORDS = {30: 'thirty', 60: 'sixty', 90: 'ninety', 180: 'one hundred and eighty'}


def exit_clause(days):
    if days == 0:
        return 'No exit assistance is provided under this agreement.'
    return (f'The Supplier shall provide exit assistance for {_NUMBER_WORDS.get(days, str(days))} ({days}) days '
            'after termination or expiry of this agreement.')


def _add_months(day, months):
    total = day.month - 1 + months
    return date(day.year + total // 12, total % 12 + 1, day.day)


def _read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def compose_records(case_dir: Path, case):
    d = _Draw(case); p = case['project']; root = Path(case_dir) / 'gate_evidence'
    g, it, tr, le, pr = case['general'], case['it'], case['tech_readiness'], case['legal'], case['procurement']
    keur = lambda eur: round(eur / 1000, 3)

    # Budget: request history and approval tranches (one rejected tranche as a distractor).
    requested, approved = g['budget_requested_eur'], g['budget_approved_eur']
    first = approved * 6 // 10
    stamps = _dates(case, 4)
    rows = [{'as_of': stamps[0], 'item': 'Budget request', 'amount_keur': keur(int(requested * 0.8)), 'status': 'SUPERSEDED'},
            {'as_of': stamps[1], 'item': 'Budget request', 'amount_keur': keur(requested), 'status': 'SUBMITTED'},
            {'as_of': stamps[2], 'item': 'Tranche 1', 'amount_keur': keur(first), 'status': 'APPROVED'},
            {'as_of': stamps[3], 'item': 'Tranche 2', 'amount_keur': keur(approved - first), 'status': 'APPROVED'},
            {'as_of': stamps[3], 'item': 'Tranche 3', 'amount_keur': keur(max(1000, requested // 5)), 'status': 'REJECTED'}]
    write_csv(root / 'general/budget_approval.csv', _owned(d.shuffled(rows, 'compose-budget'), 'BUDGET_APPROVAL'),
              ['as_of', 'item', 'amount_keur', 'status', 'recorded_by'])

    # Procurement: yearly costs of the awarded offer instead of its total.
    award_path = root / 'procurement/award_decision.json'
    if award_path.is_file():
        award = json.loads(award_path.read_text(encoding='utf-8'))
        tco = next(o for o in pr['offers'] if o['vendor'] == pr['selected_vendor'])['tco_3y_eur']
        year_1, year_2 = tco * 4 // 10, tco * 3 // 10
        award.pop('awarded_tco_3y_keur', None)
        award['awarded_cost_eur'] = {'year_1': year_1, 'year_2': year_2, 'year_3': tco - year_1 - year_2}
        write_json(award_path, award)

    # Tech readiness: restore start and end instead of the measured duration.
    restore_path = root / 'tech_readiness/restore_test.csv'
    if restore_path.is_file():
        rows, fields = _read_csv(restore_path)
        for row in rows:
            seconds = round(float(row.pop('measured_restore_minutes')) * 60)
            start = datetime.fromisoformat(row['as_of'] + 'T01:30:00')
            row['restore_started'] = start.isoformat()
            row['restore_completed'] = (start + timedelta(seconds=seconds)).isoformat()
        fields = [f for f in fields if f != 'measured_restore_minutes'] + ['restore_started', 'restore_completed']
        write_csv(restore_path, rows, fields)

    # IT: peak utilization instead of headroom; end-of-life date instead of months remaining.
    capacity_path = root / 'it/capacity_report.csv'
    if capacity_path.is_file():
        rows, fields = _read_csv(capacity_path)
        for row in rows:
            row['peak_utilization_pct'] = 100 - int(float(row.pop('capacity_headroom_pct')))
        write_csv(capacity_path, rows, [f if f != 'capacity_headroom_pct' else 'peak_utilization_pct' for f in fields])
    lifecycle_path = root / 'it/lifecycle_eol.csv'
    if lifecycle_path.is_file():
        rows, fields = _read_csv(lifecycle_path)
        start = date.fromisoformat(_dates(case, 1)[0])
        assessed = date(start.year, start.month, min(start.day, 28))
        for row in rows:
            row.pop('capacity_headroom_pct', None)
            row['assessed_on'] = assessed.isoformat()
            row['end_of_life'] = _add_months(assessed, int(float(row.pop('eol_months')))).isoformat()
        fields = [f for f in fields if f not in ('capacity_headroom_pct', 'eol_months')] + ['assessed_on', 'end_of_life']
        write_csv(lifecycle_path, rows, fields)

    # Legal: the exit clause as contract text.
    contract_path = root / 'legal/contract_register.json'
    if contract_path.is_file():
        register = json.loads(contract_path.read_text(encoding='utf-8'))
        for version in register['contracts']:
            version['clauses']['exit_assistance'] = exit_clause(version['clauses'].pop('exit_assistance_days'))
        write_json(contract_path, register)
