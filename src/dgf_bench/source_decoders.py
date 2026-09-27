"""Where each decisive fact can be read in a v9 dossier, and how to decode it.

Content is what the public reader returns when snapshots are hidden: CSV as a list of row
objects, JSON as parsed, Word documents as a list of blocks. A decoder returns the JSON
pointers that locate the value and the decoded value, or raises LookupError when the source
does not establish it. Aggregate facts (counts, absence of a row) are evidenced by the whole
table, so any location inside that observation supports them.

Systems of record carry the difficulty levers (dgf_bench.difficulty): dated histories where the
latest row prevails, other projects' rows, units such as kEUR or seconds, and status aliases.
Decoders take the project's identity and the selected vendor from ctx.

Sources are listed authoritative first. Non-authoritative decoders exist so control agents can
read the distractor documents; the certification and the reference agent use authoritative ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Callable


@dataclass(frozen=True)
class Source:
    evidence_id: str
    authoritative: bool
    decode: Callable[[Any, dict], tuple[list[str], Any]]
    aggregate: bool = False


# ---- status aliases used by systems of record --------------------------------------------

ALIASES = {'signed': 'executed', 'draft': 'in drafting', 'missing': 'absent', 'complete': 'completed',
           'partial': 'partially completed', 'pending': 'in progress', 'clear': 'cleared', 'hit': 'match',
           'not_required': 'not applicable', 'pass': 'passed', 'major_findings': 'major findings',
           'minor_findings': 'minor findings', 'not_assessed': 'not assessed', 'reports_only': 'reports only',
           'ambiguous': 'unclear'}
_CANONICAL = {v: k for k, v in ALIASES.items()}
assert len(_CANONICAL) == len(ALIASES), 'aliases must be one-to-one'


def alias(value):
    """Record wording of a canonical status (other values unchanged)."""
    return ALIASES.get(value, value) if isinstance(value, str) else value


# ---- value conversion -------------------------------------------------------------------

def _bool(x):
    if isinstance(x, bool):
        return x
    if isinstance(x, str) and x.strip() in ('True', 'true', 'Yes', 'yes'):
        return True
    if isinstance(x, str) and x.strip() in ('False', 'false', 'No', 'no'):
        return False
    raise LookupError(f'not a boolean: {x!r}')


def _num(x):
    if isinstance(x, bool):
        raise LookupError('boolean is not a number')
    if isinstance(x, (int, float)):
        return x
    try:
        value = float(str(x).strip())
    except ValueError:
        raise LookupError(f'not a number: {x!r}') from None
    return int(value) if value.is_integer() else value


def _str(x):
    if x is None or (isinstance(x, str) and x.strip() in ('', 'None')):
        raise LookupError('empty value')
    return str(x).strip()


def _status(x):
    """Canonical status from a record's wording."""
    word = _str(x)
    return _CANONICAL.get(word, word)


def _num_or_word(x):
    try:
        return _num(x)
    except LookupError:
        return _str(x)


def _nullable_num(x):
    return None if x is None else _num(x)


def _keur(x):
    return round(_num(x) * 1000)


def _seconds_to_ms(x):
    return round(_num(x) * 1000)


def _minutes_to_hours(x):
    value = round(_num(x) / 60, 6)
    return int(value) if float(value).is_integer() else value


# ---- locators ---------------------------------------------------------------------------

def _escape(token):
    return str(token).replace('~', '~0').replace('/', '~1')


def _rows(content):
    if not isinstance(content, list) or not all(isinstance(r, dict) for r in content):
        raise LookupError('not a table')
    return content


# Context flag of the lever-naive control agent: the first row matching the labels, without
# selecting the project's entity or the latest dated row.
NAIVE_SELECTION = '_naive_selection'

# Owner of each dated system of record. Entries name who recorded them (recorded_by); only the
# owner's entries are part of the record, whatever their date (see the policy's record_provenance).
RECORD_OWNERS = {'BUDGET_APPROVAL': 'Finance', 'TECH_CATALOG': 'Enterprise Architecture',
                 'LICENSE_POSITION': 'IT Asset Management', 'RESTORE_TEST': 'Site Reliability Engineering',
                 'LOAD_TEST': 'Performance Engineering', 'OPERATIONS_READINESS': 'IT Operations',
                 'DUE_DILIGENCE': 'Vendor Risk Management', 'CONTRACT_REGISTER': 'Legal', 'COMPLIANCE_REGISTER': 'GRC'}
RECORD_OWNER = '_record_owner'
# Context flag for readers that ignore provenance (the credulous control agent, forged-entry checks).
IGNORE_PROVENANCE = '_ignore_provenance'


def _foreign(entry, ctx):
    if ctx.get(IGNORE_PROVENANCE):
        return False
    owner = ctx.get(RECORD_OWNER)
    return bool(owner) and isinstance(entry, dict) and entry.get('recorded_by') not in (None, owner)


def _candidates(entries, where, ctx):
    naive = ctx.get(NAIVE_SELECTION)
    return [(i, e) for i, e in entries
            if (naive or not _foreign(e, ctx))
            and (where is None or (naive and getattr(where, 'entity', False)) or where(e, ctx))]


def _pick(indexed, latest, ctx=None):
    """One (index, row) among candidates: the most recent by as_of, or the only one."""
    if not indexed:
        raise LookupError('row not found')
    if ctx and ctx.get(NAIVE_SELECTION):
        return indexed[0]
    if latest:
        dates = [r.get('as_of') for _, r in indexed]
        if any(not isinstance(s, str) for s in dates) or len(set(dates)) != len(dates):
            raise LookupError('history without distinct dates')
        return max(indexed, key=lambda ir: ir[1]['as_of'])
    if len(indexed) != 1:
        raise LookupError('ambiguous row')
    return indexed[0]


def csv_pick(column, convert, where=None, latest=False):
    """Cell of the row selected by where(row, ctx) (all rows if None), the latest one if asked."""
    def decode(content, ctx):
        i, row = _pick(_candidates(enumerate(_rows(content)), where, ctx), latest, ctx)
        if column not in row:
            raise LookupError('missing cell')
        return [f'/{i}/{_escape(column)}'], convert(row[column])
    return decode


def csv_column_all(column, convert):
    """The same value repeated on every row (for example an overlap flag per CIDR)."""
    def decode(content, ctx):
        rows = _rows(content)
        values = {str(r.get(column)) for r in rows}
        if not rows or len(values) != 1:
            raise LookupError('missing or inconsistent column')
        return [f'/{i}/{_escape(column)}' for i in range(len(rows))], convert(rows[0][column])
    return decode


def csv_count(predicate):
    def decode(content, ctx):
        rows = _rows(content)
        return [f'/{i}' for i in range(len(rows))], sum(1 for r in rows if predicate(r))
    return decode


def json_at(*tokens, convert=lambda x: x):
    def decode(content, ctx):
        value = content
        for t in tokens:
            if not isinstance(value, dict) or t not in value:
                raise LookupError('missing key')
            value = value[t]
        return ['/' + '/'.join(_escape(t) for t in tokens)], convert(value)
    return decode


def json_pick(list_key, *tokens, convert=lambda x: x, where=None, latest=False):
    """Value at tokens inside the list entry selected by where(entry, ctx), the latest one if asked."""
    def decode(content, ctx):
        entries = content.get(list_key) if isinstance(content, dict) else None
        if not isinstance(entries, list):
            raise LookupError('missing list')
        entries = [(i, e) for i, e in enumerate(entries) if isinstance(e, dict)]
        i, value = _pick(_candidates(entries, where, ctx), latest, ctx)
        for t in tokens:
            if not isinstance(value, dict) or t not in value:
                raise LookupError('missing key')
            value = value[t]
        return ['/' + '/'.join(_escape(t) for t in (list_key, i, *tokens))], convert(value)
    return decode


def docx_kv(label, convert):
    """Value of a 'Field | Value' table row in a Word document."""
    prefix = label + ' | '
    def decode(content, ctx):
        if not isinstance(content, list):
            raise LookupError('not a document')
        for i, block in enumerate(content):
            if isinstance(block, str) and block.startswith(prefix):
                return [f'/{i}'], convert(block[len(prefix):])
        raise LookupError('label not found')
    return decode


def docx_regex(pattern, convert):
    rx = re.compile(pattern)
    def decode(content, ctx):
        if not isinstance(content, list):
            raise LookupError('not a document')
        for i, block in enumerate(content):
            m = rx.search(block) if isinstance(block, str) else None
            if m:
                return [f'/{i}'], convert(m.group(1))
        raise LookupError('pattern not found')
    return decode


def _ctx(key):
    def value(ctx):
        if key not in ctx:
            raise LookupError(f'{key} not established')
        return ctx[key]
    return value


_selected = _ctx('procurement.selected_vendor')


def _is(column, key):
    """Row filter: row[column] equals the context value key (project identity, selected vendor)."""
    def where(row, ctx):
        return row.get(column) == _ctx(key)(ctx)
    where.entity = True
    return where


def _is_value(column, value):
    return lambda row, ctx: row.get(column) == value


_project = _is('project_id', 'project.project_id')
_application = _is('application', 'project.project_name')


def _vendor_claim(key, convert):
    def decode(content, ctx):
        responses = content.get('responses') if isinstance(content, dict) else None
        for i, r in enumerate(responses or []):
            if r.get('vendor') == _selected(ctx) and key in r:
                return [f'/responses/{i}/{key}'], convert(r[key])
        raise LookupError('vendor claim not found')
    return decode


def csv_sum(column, convert, where):
    """Sum of a column over the rows selected by where(row, ctx) (for example approved tranches)."""
    def decode(content, ctx):
        rows = _candidates(enumerate(_rows(content)), where, ctx)
        if not rows or any(column not in r for _, r in rows):
            raise LookupError('no rows to sum')
        return [f'/{i}/{_escape(column)}' for i, _ in rows], sum(convert(r[column]) for _, r in rows)
    return decode


def json_sum(key, convert):
    """Sum of the values of an object (for example yearly costs)."""
    def decode(content, ctx):
        parts = content.get(key) if isinstance(content, dict) else None
        if not isinstance(parts, dict) or not parts:
            raise LookupError('missing breakdown')
        return [f'/{_escape(key)}/{_escape(k)}' for k in parts], sum(convert(v) for v in parts.values())
    return decode


def _duration_hours(start_column, end_column, where=None, latest=False):
    def decode(content, ctx):
        i, row = _pick(_candidates(enumerate(_rows(content)), where, ctx), latest, ctx)
        try:
            seconds = (datetime.fromisoformat(row[end_column]) - datetime.fromisoformat(row[start_column])).total_seconds()
        except (KeyError, TypeError, ValueError):
            raise LookupError('missing timestamps') from None
        value = round(seconds / 3600, 6)
        return [f'/{i}/{_escape(start_column)}', f'/{i}/{_escape(end_column)}'], int(value) if value.is_integer() else value
    return decode


def _months_between(start_column, end_column, where=None):
    def decode(content, ctx):
        i, row = _pick(_candidates(enumerate(_rows(content)), where, ctx), False, ctx)
        try:
            a, b = date.fromisoformat(row[start_column]), date.fromisoformat(row[end_column])
        except (KeyError, TypeError, ValueError):
            raise LookupError('missing dates') from None
        return [f'/{i}/{_escape(end_column)}', f'/{i}/{_escape(start_column)}'], (b.year - a.year) * 12 + b.month - a.month - (b.day < a.day)
    return decode


def _exit_days(text):
    text = _str(text)
    if text.startswith('No exit assistance'):
        return 0
    m = re.search(r'\((\d+)\) days', text)
    if not m:
        raise LookupError('no exit assistance period')
    return int(m.group(1))


def _derived(decoder, fn):
    def decode(content, ctx):
        pointers, value = decoder(content, ctx)
        return pointers, fn(value)
    return decode


def _control(control, column='status', convert=_status):
    return csv_pick(column, convert, _is_value('control', control), latest=True)


def _readiness(item, fn):
    return _derived(csv_pick('status', _status, _is_value('item', item), latest=True), fn)


def _contract(*tokens, convert):
    return json_pick('contracts', *tokens, convert=convert, where=_project, latest=True)


def _architecture(*tokens, convert):
    return json_pick('applications', *tokens, convert=convert, where=_application)


A, N = True, False
SOURCES: dict[str, tuple[Source, ...]] = {
    # General
    'general.strategic_alignment': (Source('PORTFOLIO_SNAPSHOT', A, csv_pick('strategic_alignment', _str, _project)),
                                    Source('COMMITTEE_BRIEFING', N, docx_kv('Strategic alignment', _str))),
    'general.budget_requested_eur': (Source('BUDGET_APPROVAL', A, csv_pick('requested_keur', _keur, latest=True)),
                                     Source('BUDGET_APPROVAL', A, csv_pick('amount_keur', _keur, _is_value('item', 'Budget request'), latest=True)),
                                     Source('PORTFOLIO_SNAPSHOT', A, csv_pick('requested_eur', _num, _project))),
    'general.budget_approved_eur': (Source('BUDGET_APPROVAL', A, csv_pick('approved_keur', _keur, latest=True)),
                                    Source('BUDGET_APPROVAL', A, csv_sum('amount_keur', _keur, lambda r, ctx: str(r.get('item', '')).startswith('Tranche')
                                                                                               and r.get('status') == 'APPROVED')),
                                    Source('PORTFOLIO_SNAPSHOT', A, csv_pick('approved_eur', _num, _project))),
    'general.roi': (Source('PORTFOLIO_SNAPSHOT', A, csv_pick('roi', _num, _project)),
                    Source('BUSINESS_CASE', N, csv_pick('value', _num, _is_value('metric', 'ROI'))),
                    Source('COMMITTEE_BRIEFING', N, docx_kv('ROI', _num))),
    'general.change_plan_status': (Source('PORTFOLIO_SNAPSHOT', A, csv_pick('change_plan_status', _str, _project)),
                                   Source('BENEFITS_PLAN', N, docx_kv('Change plan', _str))),
    # IT
    'it.catalog_status': (Source('TECH_CATALOG', A, json_pick('decisions', 'catalog_status', convert=_str, latest=True)),),
    'it.waiver_present': (Source('TECH_CATALOG', A, json_pick('decisions', 'waiver_present', convert=_bool, latest=True)),),
    'it.duplicate_capability': (Source('TECH_CATALOG', A, json_pick('decisions', 'existing_capability_overlap', convert=_bool, latest=True)),),
    'it.license_compliant': (Source('LICENSE_POSITION', A, csv_pick('license_compliant', _bool, latest=True)),),
    'it.technology_eol_months': (Source('LIFECYCLE_REGISTER', A, csv_pick('eol_months', _num, _project)),
                                 Source('LIFECYCLE_REGISTER', A, _months_between('assessed_on', 'end_of_life', _project))),
    'it.capacity_headroom_pct': (Source('CAPACITY_REPORT', A, csv_pick('capacity_headroom_pct', _num, _is('service', 'project.project_name'))),
                                 Source('LIFECYCLE_REGISTER', A, csv_pick('capacity_headroom_pct', _num, _project)),
                                 Source('CAPACITY_REPORT', A, csv_pick('peak_utilization_pct', lambda x: 100 - _num(x),
                                                                       _is('service', 'project.project_name')))),
    'it.cmdb_record_present': (Source('CMDB_EXPORT', A, csv_pick('record_present', _bool, _application)),),
    'it.run_owner_present': (Source('CMDB_EXPORT', A, _derived(csv_pick('run_owner', lambda x: x or '', _application), bool)),),
    'it.change_record_status': (Source('ITSM_CHANGE', A, csv_pick('status', _str)),),
    # Architecture
    'architecture.ip_overlap': (Source('IP_PLAN', A, csv_column_all('overlap_detected', _bool)),),
    'architecture.api_gateway_required': (Source('ARCHITECTURE_REGISTER', A, _architecture('api_gateway', 'required', convert=_bool)),
                                          Source('LLD', N, docx_kv('API gateway required', _bool))),
    'architecture.api_gateway_present': (Source('ARCHITECTURE_REGISTER', A, _architecture('api_gateway', 'deployed', convert=_bool)),
                                         Source('LLD', N, docx_kv('API gateway present', _bool))),
    'architecture.latency_target_ms': (Source('ARCHITECTURE_REGISTER', A, _architecture('latency_s', 'target_p95', convert=_seconds_to_ms)),
                                       Source('LLD', N, docx_kv('Latency target ms', _num))),
    'architecture.measured_latency_ms': (Source('ARCHITECTURE_REGISTER', A, _architecture('latency_s', 'measured_p95', convert=_seconds_to_ms)),
                                         Source('LLD', N, docx_kv('Measured latency ms', _num))),
    'architecture.reversibility_status': (Source('ARCHITECTURE_REGISTER', A, _architecture('exit_plan_status', convert=_str)),
                                          Source('LLD', N, docx_kv('Reversibility', _str))),
    'architecture.data_owner_present': (Source('ARCHITECTURE_REGISTER', A, _architecture('data_domain_owner', convert=lambda x: x is not None)),),
    'architecture_profile.multi_az': (Source('ARCHITECTURE_REGISTER', A, _architecture('availability', 'zone_redundant', convert=_bool)),),
    'architecture_profile.multi_region': (Source('ARCHITECTURE_REGISTER', A, _architecture('availability', 'secondary_region', convert=_bool)),),
    # Project facts used by several gates
    'project.business_criticality': (Source('DATA_INVENTORY', A, csv_pick('business_criticality', _str, _project)),
                                     Source('PROJECT_CHARTER', N, docx_kv('Criticality', _str))),
    'project.data_classification': (Source('DATA_INVENTORY', A, csv_pick('classification', _str, _project)),
                                    Source('PROJECT_CHARTER', N, docx_kv('Data classification', _str))),
    'project.personal_data': (Source('DATA_INVENTORY', A, csv_pick('personal_data', _bool, _project)),
                              Source('DPA', N, docx_kv('Personal data', _bool))),
    # Security
    'security.internet_exposed': (Source('NSG_RULES', A, _derived(
        csv_count(lambda r: r.get('source') == 'Internet' and r.get('action') == 'Allow'), lambda n: n > 0), aggregate=True),),
    'security.waf_present': (Source('WAF_POLICY', A, json_at('present', convert=_bool)),),
    'security.waf_mode': (Source('WAF_POLICY', A, json_at('mode', convert=_str)),),
    'security.private_endpoints': (Source('AZURE_RESOURCE_GRAPH', A, json_at('private_endpoints', convert=_bool)),
                                   Source('KEYVAULT_CONFIG', A, json_at('private_endpoint', convert=_bool))),
    'security.mfa': (Source('CONDITIONAL_ACCESS', A, json_at('mfa', convert=_str)),),
    'security.shared_service_principal': (Source('IAM_EXPORT', A, _derived(csv_pick('scope', _str), lambda s: s == 'subscription')),),
    'security.logs_to_siem': (Source('SENTINEL_STATUS', A, json_at('logs_to_siem', convert=_bool)),),
    'security.critical_vulns_open': (Source('VULN_SCAN', A, csv_count(
        lambda r: r.get('severity') == 'Critical' and r.get('status') == 'Open'), aggregate=True),),
    'security.key_rotation_days': (Source('KEYVAULT_CONFIG', A, json_at('key_rotation_days', convert=_nullable_num)),),
    'security.pentest_status': (Source('PENTEST', N, docx_kv('Status', _str)),),
    # Tech readiness
    'tech_readiness.backup_enabled': (Source('BACKUP_JOBS', A, csv_pick('enabled', _bool)),),
    'tech_readiness.restore_tested': (Source('RESTORE_TEST', A, csv_pick('tested', _bool, latest=True)),),
    'tech_readiness.target_rto_hours': (Source('RESTORE_TEST', A, csv_pick('target_rto_hours', _num, latest=True)),),
    'tech_readiness.measured_restore_hours': (Source('RESTORE_TEST', A, csv_pick('measured_restore_minutes', _minutes_to_hours, latest=True)),
                                              Source('RESTORE_TEST', A, _duration_hours('restore_started', 'restore_completed', latest=True))),
    'tech_readiness.target_rpo_minutes': (Source('RESTORE_TEST', A, csv_pick('target_rpo_minutes', _num, latest=True)),),
    'tech_readiness.measured_data_loss_minutes': (Source('RESTORE_TEST', A, csv_pick('measured_data_loss_minutes', _num, latest=True)),),
    'tech_readiness.dr_required': (Source('FAILOVER_TEST', A, csv_pick('dr_required', _bool)),),
    'tech_readiness.dr_tested': (Source('FAILOVER_TEST', A, csv_pick('tested', _bool)),),
    'tech_readiness.load_test_pct_of_peak': (Source('LOAD_TEST', A, csv_pick('peak_test_pct', _num, latest=True)),),
    'tech_readiness.critical_static_findings': (Source('CI_PIPELINE', A, json_at('critical_static_findings', convert=_num)),),
    'tech_readiness.rollback_tested': (Source('ROLLBACK_TEST', A, csv_pick('rollback_tested', _bool)),),
    'tech_readiness.runbook_status': (Source('OPERATIONS_READINESS', A, _readiness('Production runbook', lambda s: s)),
                                      Source('RUNBOOK', N, docx_kv('Runbook status', _str))),
    'tech_readiness.on_call_defined': (Source('OPERATIONS_READINESS', A, _readiness('On-call rota', lambda s: s == 'defined')),
                                       Source('RUNBOOK', N, docx_kv('On-call defined', _bool))),
    'tech_readiness.handover_signed': (Source('OPERATIONS_READINESS', A, _readiness('Operational handover', lambda s: s == 'signed')),
                                       Source('RUNBOOK', N, docx_kv('Handover signed', _bool))),
    'tech_readiness.open_blockers': (Source('RELEASE_BLOCKERS', A, csv_count(lambda r: r.get('status') == 'Open'), aggregate=True),),
    # Procurement: the award decision identifies the selected offer.
    'procurement.selected_vendor': (Source('PROCUREMENT_AWARD', A, json_at('selected_vendor', convert=_str)),
                                    Source('SCORING_MATRIX', N, csv_pick('vendor', _str, _is_value('selected', 'True')))),
    'procurement.purchasing_budget_eur': (Source('PROCUREMENT_AWARD', A, json_at('purchasing_budget_keur', convert=_keur)),
                                          Source('RFP', N, docx_kv('Budget', _num))),
    'procurement.offer.tco_3y_eur': (Source('PROCUREMENT_AWARD', A, json_at('awarded_tco_3y_keur', convert=_keur)),
                                     Source('PROCUREMENT_AWARD', A, json_sum('awarded_cost_eur', _num)),
                                     Source('VENDOR_OFFERS', N, csv_pick('tco_3y_eur', _num, _is('vendor', 'procurement.selected_vendor')))),
    'procurement.offer.mandatory_criteria_failed': (
        Source('DUE_DILIGENCE', A, csv_pick('mandatory_failed', _num, _is('vendor', 'procurement.selected_vendor'), latest=True)),
        Source('VENDOR_OFFERS', N, csv_pick('mandatory_criteria_failed', _num, _is('vendor', 'procurement.selected_vendor')))),
    'procurement.offer.sanctions': (
        Source('DUE_DILIGENCE', A, csv_pick('sanctions', _status, _is('vendor', 'procurement.selected_vendor'), latest=True)),
        Source('VENDOR_OFFERS', N, csv_pick('sanctions', _str, _is('vendor', 'procurement.selected_vendor')))),
    'procurement.offer.due_diligence': (
        Source('DUE_DILIGENCE', A, csv_pick('due_diligence', _status, _is('vendor', 'procurement.selected_vendor'), latest=True)),
        Source('VENDOR_RESPONSES', N, _vendor_claim('claimed_due_diligence', _str))),
    'procurement.offer.references_checked': (
        Source('DUE_DILIGENCE', A, csv_pick('references_checked', _bool, _is('vendor', 'procurement.selected_vendor'), latest=True)),
        Source('VENDOR_RESPONSES', N, _vendor_claim('claimed_references_checked', _bool))),
    # Legal: the contract register records the executed clauses, version by version.
    'legal.dpa_status': (Source('CONTRACT_REGISTER', A, _contract('clauses', 'data_processing_agreement', convert=_status)),
                         Source('DPA', N, docx_kv('Status', _str)),
                         Source('MSA', N, docx_regex(r'DPA status: ([^.]+)\.', _str))),
    'legal.audit_right': (Source('CONTRACT_REGISTER', A, _contract('clauses', 'customer_audit_rights', convert=_status)),
                          Source('MSA', N, docx_regex(r'Customer audit rights: ([^.]+)\.', _str))),
    'legal.log_export_clause': (Source('CONTRACT_REGISTER', A, _contract('clauses', 'security_log_export', convert=_status)),
                                Source('MSA', N, docx_regex(r'Security logs export: ([^.]+)\.', _str))),
    'legal.ip_ownership': (Source('CONTRACT_REGISTER', A, _contract('clauses', 'ip_ownership', convert=_status)),
                           Source('MSA', N, docx_regex(r'IP ownership: ([^.]+)\.', _str))),
    'legal.exit_assistance_days': (Source('CONTRACT_REGISTER', A, _contract('clauses', 'exit_assistance_days', convert=_num)),
                                   Source('CONTRACT_REGISTER', A, _contract('clauses', 'exit_assistance', convert=_exit_days)),
                                   Source('MSA', N, docx_regex(r'Exit assistance: (\S+) days', _num))),
    'legal.liability_cap_multiplier': (Source('CONTRACT_REGISTER', A, _contract('clauses', 'liability_cap_x_annual_fees', convert=_num_or_word)),
                                       Source('MSA', N, docx_regex(r'Aggregate liability cap: (\S+)x annual', _num_or_word))),
    'legal.security_carveout': (Source('CONTRACT_REGISTER', A, _contract('clauses', 'security_liability_carve_out', convert=_bool)),
                                Source('MSA', N, docx_regex(r'Security carve-out: (\w+)\.', _bool))),
    'legal.insurance_valid': (Source('CONTRACT_REGISTER', A, _contract('insurance_certificate_valid', convert=_bool)),
                              Source('INSURANCE', N, docx_kv('Valid', _bool)),
                              Source('MSA', N, docx_regex(r'Certificate valid: (\w+)\.', _bool))),
    'legal.signing_authority_valid': (Source('CONTRACT_REGISTER', A, _contract('signatory_authority_valid', convert=_bool)),
                                      Source('SIGNING_AUTHORITY', A, csv_pick('authority_valid', _bool))),
    # Compliance: the GRC register keeps the history of each control.
    'compliance.dpia_required': (Source('COMPLIANCE_REGISTER', A, _control('DPIA', 'required', _bool)),
                                 Source('DPIA', N, docx_kv('Required', _bool))),
    'compliance.dpia_status': (Source('COMPLIANCE_REGISTER', A, _control('DPIA')),
                               Source('DPIA', N, docx_kv('Status', _str))),
    'compliance.residency_compliant': (Source('COMPLIANCE_REGISTER', A, _derived(_control('Data residency'), lambda s: s == 'compliant')),),
    'compliance.audit_trail': (Source('COMPLIANCE_REGISTER', A, _control('Audit trail')),),
    'compliance.regulatory_mapping': (Source('COMPLIANCE_REGISTER', A, _control('Regulatory mapping')),
                                      Source('REG_MAPPING', A, csv_column_all('mapping_status', _str))),
    'compliance.accessibility_status': (Source('COMPLIANCE_REGISTER', A, _control('Accessibility')),
                                        Source('ACCESSIBILITY', N, csv_pick('status', _str))),
    'compliance.export_control': (Source('COMPLIANCE_REGISTER', A, _control('Export control')),
                                  Source('EXPORT_CONTROL', A, csv_pick('status', _str))),
}


def identity_context(project):
    """Decoding context from the project identity an agent is given."""
    return {f'project.{k}': project[k] for k in ('project_id', 'project_name', 'project_code') if k in project}


def decode(field, source, content, ctx):
    """(pointers, value) for one source, or raise LookupError."""
    return source.decode(content, ctx)


def _owned(owner, decoder):
    def decode(content, ctx):
        return decoder(content, {**ctx, RECORD_OWNER: owner})
    return decode


# Decoders of owned records ignore entries recorded by anyone other than the owner.
SOURCES = {field: tuple(replace(s, decode=_owned(RECORD_OWNERS[s.evidence_id], s.decode)) if s.evidence_id in RECORD_OWNERS else s
                        for s in sources) for field, sources in SOURCES.items()}


def decode_facts(fields, observations, ctx=None, authoritative_only=True):
    """Decode fields from observed contents {evidence_id: content}; returns {field: (evidence_id, pointers, value)}.

    Fields are decoded in dependency order (the selected vendor first). A field with no usable
    source is omitted. ctx carries the project identity (see identity_context).
    """
    ctx = dict(ctx or {})
    ordered = sorted(fields, key=lambda f: (not f == 'procurement.selected_vendor', f.startswith('procurement.offer')))
    out = {}
    for field in ordered:
        for source in SOURCES.get(field, ()):
            if authoritative_only and not source.authoritative:
                continue
            if source.evidence_id not in observations:
                continue
            try:
                pointers, value = source.decode(observations[source.evidence_id], ctx)
            except LookupError:
                continue
            out[field] = (source.evidence_id, pointers, value)
            ctx[field] = value
            break
    return out
