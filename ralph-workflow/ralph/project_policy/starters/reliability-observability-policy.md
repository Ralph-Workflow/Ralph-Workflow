<!-- ralph-policy-schema: v4 -->
<!-- ralph-policy-id: reliability-observability-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: starter template; replace verified facts and
commands, then delete this banner. -->

# Reliability and Observability Policy

## Purpose and scope

This policy governs long-running services, workers, daemons, operational
failure handling, and diagnostic signals.

## Applicability

Required for project-owned long-running operational processes. One-shot tools
and build-only containers are excluded unless they have an operational service.
If operations cease, retain a dated inactive decision and reactivation trigger,
or remove the policy through reviewed cleanup.

## Default requirements

* Timeouts, retries, backoff, idempotency, backpressure, shutdown, and recovery
  MUST be defined for applicable boundaries.
* Logs, metrics, traces, and health signals MUST be actionable, bounded, and
  free of prohibited sensitive data.
* Reliability objectives and operationally required alerts MUST reflect user or
  operator impact rather than incidental implementation detail.
* Failure handling MUST avoid retry storms, silent loss, and unbounded queues.

## Project facts to resolve

The lines below are the verified project facts that agents rely on and keep
current.

<!-- REPLACE-ME: record operational units, objectives, timeout/retry rules,
signals, and runbooks, then delete this comment. -->

RALPH-FACT: operational_units: PROJECT-FACT-UNRESOLVED
RALPH-FACT: reliability_objectives: PROJECT-FACT-UNRESOLVED
RALPH-FACT: timeout_retry_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: observability_signals: PROJECT-FACT-UNRESOLVED
RALPH-FACT: runbook_location: PROJECT-FACT-UNRESOLVED
RALPH-FACT: dependency_and_failure_mode_inventory: PROJECT-FACT-UNRESOLVED
RALPH-FACT: sli_slo_and_error_budget: PROJECT-FACT-UNRESOLVED
RALPH-FACT: incident_and_postmortem_process: PROJECT-FACT-UNRESOLVED
RALPH-FACT: disaster_recovery_rpo_rto: PROJECT-FACT-UNRESOLVED
RALPH-FACT: applicability_decision: PROJECT-FACT-UNRESOLVED

## AI execution instructions

Agents MUST model failure paths, bound resource use, preserve diagnostic
context, and update runbooks and alerts with operational behavior. Agents MUST
run declared gates, perform declared reviews, and report unavailable
operational evidence as a blocker. Agents MUST NOT invent objectives, alerts,
incident results, or recovery evidence.

## Verification

<!-- REPLACE-ME: declare the real reliability and observability gate, then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review failure modes, objectives, alerts, runbooks, and recovery; evidence: dated operational readiness record or explicit not-performed blocker; owner: service owner

## Exceptions

Exceptions require failure impact, mitigation, owner, and review date.

## Maintenance triggers

Review when an operational unit, dependency boundary, objective, alert, retry
policy, or shutdown path changes.

## Research basis

* publisher: Google
  title: "Site Reliability Engineering"
  http: https://sre.google/sre-book/table-of-contents/
  review date: 2026-07-12

* publisher: Google
  title: "Site Reliability Engineering: Service Level Objectives"
  http: https://sre.google/sre-book/service-level-objectives/
  review date: 2026-07-12

* publisher: Google
  title: "Site Reliability Engineering: Testing for Reliability"
  http: https://sre.google/sre-book/testing-reliability/
  review date: 2026-07-12

## Portfolio ownership

Shared admission, composition, aggregate-budget, evidence, exception, and
lifecycle rules are owned by `policy-portfolio.toml`; they are not repeated
here. This file owns only its domain-specific observable outcomes, verified
facts, commands, and review triggers. Amendments update the effective portfolio
while its mandatory outcomes remain protected.

## Living document contract

This policy is a living document. Keep its domain-specific facts, commands,
requirements, and review triggers aligned with verified project reality.
Shared amendment admission and retention are governed once by
`policy-portfolio.toml`; mandatory outcomes remain protected and MUST NOT be
weakened to obtain a passing result.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: reliability-observability-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v4 -->`
