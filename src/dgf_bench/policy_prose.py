"""The gate rules written as a governance handbook, for the prose policy form.

Each requirement restates, in the words of a review standard, exactly when one finding of
evaluator.py is raised: same facts, thresholds, value sets and phases, without code or field
names. tests/test_policy_prose.py checks that every finding of the catalog has one requirement.
Reviews (phases) run in the order opportunity, framing, design, build_acceptance,
deployment_closure, governance; a requirement tied to reviews names every review where it applies.
"""
from __future__ import annotations

DECISION_RULE = (
    'Raise every finding whose requirement is not met at this review. The gate decision is the most severe '
    'disposition among the findings raised (GO < GO_WITH_RESERVATIONS < REWORK < SUSPENSION < NO_GO), or GO when '
    'no finding is raised. Propose, for each finding raised, its action code from the candidate catalog. A requirement '
    'that names no review applies at every review; one that names reviews applies only at those reviews.')

REQUIREMENTS = {
    'it': [
        ('IT-CATALOG-001', 'A technology classified as non-standard in the enterprise catalog may only be used under an '
                           'approved technology waiver. Non-standard technology without such a waiver is a finding; standard '
                           'technologies and approved exceptions need no waiver.'),
        ('IT-RATIONAL-001', 'When an existing enterprise capability already covers the need, the overlap must be assessed: '
                            'raise a finding.'),
        ('IT-LICENSE-001', 'The licence position must be compliant; a non-compliant licence position is a finding.'),
        ('IT-CMDB-001', 'At the design, build acceptance and deployment closure reviews only, the service must have a CMDB '
                        'record; a missing record is then a finding. The requirement does not apply at the opportunity, '
                        'framing or governance reviews.'),
        ('IT-RUN-001', 'At the build acceptance and deployment closure reviews only, an accountable run owner must be '
                       'recorded for the service; otherwise raise a finding.'),
        ('IT-EOL-001', 'A technology that reaches end of life in less than 12 months requires a convergence plan: raise a '
                       'finding. Twelve months or more is acceptable.'),
        ('IT-CAPACITY-001', 'At the build acceptance and deployment closure reviews only, production capacity headroom must '
                            'be at least 15%; headroom below 15% is a finding.'),
        ('IT-CHANGE-001', 'At the deployment closure review only, the production change must be approved or scheduled; a '
                          'change record still in draft, or no change record, is a finding.'),
    ],
    'architecture': [
        ('ARCH-IP-001', "The project's address space must not overlap an existing network range; any overlap is a finding."),
        ('ARCH-API-001', 'When the architecture requires a governed API gateway, one must be deployed; a required gateway '
                         'that is absent is a finding.'),
        ('ARCH-DATA-001', 'An accountable owner must be defined for the governed data domain; if none is defined, raise a '
                          'finding.'),
        ('ARCH-PERF-001', 'Measured p95 latency must not exceed the target p95 latency; a measurement above the target is a '
                          'finding (a measurement equal to the target is compliant).'),
        ('ARCH-HA-001', 'A service of High or Critical business criticality must be redundant across availability zones or '
                        'have a secondary region; with neither, raise a finding. Low and Medium criticality services are exempt.'),
        ('ARCH-REV-001', 'The exit and reversibility design must at least be documented; a design that is only a draft, or '
                         'missing, is a finding (documented or tested designs are compliant).'),
    ],
    'security': [
        ('SEC-WAF-001', 'A workload that allows inbound Internet traffic must be protected by a web application firewall; '
                        'an Internet-exposed workload without one is a finding.'),
        ('SEC-WAF-002', 'When an Internet-exposed workload has a web application firewall, it must run in prevention mode; '
                        'a firewall in detection-only mode is a finding.'),
        ('SEC-NET-001', 'When the data processed is classified Confidential or Restricted, data services must be reachable '
                        'only through private endpoints; otherwise raise a finding. Public and Internal data are exempt.'),
        ('SEC-IAM-001', 'Multi-factor authentication must be mandatory for the service; optional or missing MFA is a finding.'),
        ('SEC-IAM-002', 'Each workload needs its own identity: a service principal shared across workloads (a '
                        'subscription-wide role assignment) is a finding.'),
        ('SEC-LOG-001', 'Security-relevant logs must be exported to the SIEM; otherwise raise a finding.'),
        ('SEC-VULN-001', 'Any open critical vulnerability is a finding; remediated vulnerabilities and lower severities do '
                         'not count.'),
        ('SEC-PENTEST-001', 'At the build acceptance review only, the penetration test must be completed (passed, with or without '
                            'findings); a pending or missing penetration test is then a finding. The requirement does not '
                            'apply at other reviews.'),
        ('SEC-KEY-001', 'Keys must be rotated more often than once a year: no configured rotation, or a rotation period of '
                        '365 days, is a finding. Rotation every 180 days or more often is compliant.'),
    ],
    'tech_readiness': [
        ('TR-BACKUP-001', 'Production backup must be enabled; otherwise raise a finding.'),
        ('TR-RESTORE-001', 'A successful restore test must exist; if there is none, raise a finding.'),
        ('TR-RTO-001', 'When a successful restore test exists, the measured restore time must not exceed the committed '
                       'recovery time objective; a longer restore is a finding. Without a successful restore test this '
                       'requirement is not assessed.'),
        ('TR-RPO-001', 'Data loss measured in the recovery test must not exceed the committed recovery point objective; '
                       'more data loss is a finding.'),
        ('TR-DR-001', 'When disaster recovery to another region is required, a failover test must have passed; otherwise '
                      'raise a finding.'),
        ('TR-LOAD-001', 'The load test must reach at least 100% of the expected production peak; less is a finding.'),
        ('TR-CODE-001', 'Any open critical static-analysis finding is a finding.'),
        ('TR-OPS-001', 'The operational package must be complete: an approved production runbook, a defined on-call rota and '
                       'a signed operational handover. If any of the three is lacking, raise one finding.'),
        ('TR-ROLLBACK-001', 'The rollback procedure must have been tested; otherwise raise a finding.'),
        ('TR-BLOCKER-001', 'Any open production blocker is a finding; closed blockers do not count.'),
    ],
    'procurement': [
        ('PROC-MAND-001', 'The selected supplier must pass every mandatory tender criterion; one or more failed mandatory '
                          'criteria is a finding.'),
        ('PROC-SAN-001', 'A sanctions screening hit for the selected supplier is a finding.'),
        ('PROC-SAN-002', 'A sanctions screening of the selected supplier that is still pending is a finding (a hit is covered '
                         'by the previous requirement; a clear screening is compliant).'),
        ('PROC-DD-001', "Third-party due diligence of the selected supplier must be completed; due diligence that is partially "
                        'completed or still in progress is a finding.'),
        ('PROC-BUDGET-001', "The three-year total cost of ownership of the selected supplier's offer must not exceed 150% of "
                            'the purchasing envelope; a cost above one and a half times the envelope is a finding.'),
        ('PROC-REF-001', "The selected supplier's references must have been checked; otherwise raise a finding."),
    ],
    'legal': [
        ('LEGAL-DPA-001', 'When personal data is processed, the data processing agreement must be signed; a draft or missing '
                          'agreement is then a finding. Without personal data no agreement is required.'),
        ('LEGAL-LIAB-001', 'A liability cap of half the annual fees is acceptable only if security and data breaches are carved '
                           'out of the cap; such a cap without the carve-out is a finding. Caps of one year of fees or more, '
                           'or unlimited liability, are compliant.'),
        ('LEGAL-AUDIT-001', 'The customer must hold at least limited audit rights; audit rights restricted to receiving '
                            'reports, or no audit right, is a finding.'),
        ('LEGAL-EXIT-001', 'The contract must provide exit assistance; no exit assistance (zero days) is a finding.'),
        ('LEGAL-IP-001', 'Ownership of intellectual property must be clear (customer, vendor or shared); ambiguous ownership '
                         'is a finding.'),
        ('LEGAL-INS-001', "The supplier's insurance certificate must be valid; an invalid or expired certificate is a finding."),
        ('LEGAL-SIGN-001', 'The proposed signatory must hold recorded signing authority; otherwise raise a finding.'),
        ('LEGAL-LOG-001', 'For a service of High or Critical business criticality, the contract must commit the supplier to '
                          'export security logs; a missing commitment is then a finding (a limited commitment is acceptable). '
                          'Low and Medium criticality services are exempt.'),
    ],
    'compliance': [
        ('COMP-DPIA-001', 'When a data protection impact assessment is required, it must be complete; a required assessment '
                          'that is not complete is a finding.'),
        ('COMP-RESID-001', 'The actual data region must satisfy the required residency; a violation is a finding.'),
        ('COMP-AUDIT-001', 'The required compliance audit trail must exist; a missing audit trail is a finding (a partial one '
                           'is acceptable at this review).'),
        ('COMP-MAP-001', 'Applicable obligations must be mapped to controls; a missing mapping is a finding (a partial mapping '
                         'is acceptable at this review).'),
        ('COMP-A11Y-001', 'An accessibility assessment with major findings is a finding; a pass, minor findings or no '
                          'assessment yet are acceptable.'),
        ('COMP-EXPORT-001', 'An export-control screening that indicates a restricted condition is a finding.'),
        ('COMP-EXPORT-002', 'An export-control screening that is still pending is a finding (a restricted result is covered by '
                            'the previous requirement; a clear screening is compliant).'),
    ],
    'general': [
        ('GEN-STRAT-001', 'A project whose strategic alignment in the portfolio is weak is a finding; strong or partial '
                          'alignment is acceptable.'),
        ('GEN-BUDGET-001', 'Requested funding must not exceed the budget approved by finance; any excess is a finding.'),
        ('GEN-BC-001', 'At every review except deployment closure, the expected return on investment of the business case '
                       'must be at least 5%; a lower return is a finding.'),
        ('GEN-UPSTREAM-NOGO', 'Consolidate the upstream specialist decisions you received: if at least one is NO_GO, raise '
                              'this finding.'),
        ('GEN-UPSTREAM-SUSPEND', 'Otherwise, if at least one upstream decision is SUSPENSION, raise this finding.'),
        ('GEN-UPSTREAM-REWORK', 'Otherwise, if at least one upstream decision is REWORK, raise this finding. Upstream GO and '
                                'GO_WITH_RESERVATIONS decisions raise no consolidation finding.'),
        ('GEN-BENEFITS-001', 'At the deployment closure review only, a benefits and change plan must exist; a missing plan is a finding (a '
                             'draft, partial or complete plan is acceptable).'),
    ],
}


def review_standard(gate):
    """The gate's requirements as 'FINDING-ID: requirement' lines."""
    return [f'{fid}: {text}' for fid, text in REQUIREMENTS[gate]]
