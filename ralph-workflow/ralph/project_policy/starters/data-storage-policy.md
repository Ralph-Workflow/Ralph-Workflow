<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: data-storage-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: starter template; replace verified facts and commands, then delete this banner. -->
# Data Storage Policy
## Purpose and scope
This policy governs application-owned durable data, schemas, migrations, transactions, backup compatibility, and destructive data operations.
## Applicability
Required for project-owned durable stores or migrations. External stateless
dependencies alone do not trigger it; uncertainty requires owner confirmation.
If durable ownership ends, retain a dated inactive decision and reactivation
trigger, or remove the policy through reviewed cleanup.
## Default requirements
* Schema constraints and transaction boundaries MUST enforce declared invariants
  where supported; otherwise document invariant enforcement and partial failure.
* Migrations MUST be ordered, reviewable, tested against representative prior state, and safe for the deployment strategy.
* Destructive changes MUST have an explicit recovery, backup, or irreversible-change decision.
* Sensitive data handling MUST defer to the privacy and security policies.
* Consistency, conflict, concurrency, and partial-failure behavior MUST be
  explicit for the selected store.
* Where data loss or availability matters, backup restoration MUST be tested
  and recovery objectives documented.
* Retention and deletion MUST be enforceable. Access MUST follow least
  privilege, with encryption or equivalent protection for sensitive data.
* Migrations MUST declare supported source versions and a rollback,
  roll-forward, or explicitly irreversible strategy.
## Project facts to resolve
The lines below are the verified project facts that agents rely on and keep current.
<!-- REPLACE-ME: record stores, schema source, migration command, transaction rules, and recovery strategy, then delete this comment. -->
RALPH-FACT: durable_stores: PROJECT-FACT-UNRESOLVED
RALPH-FACT: schema_source: PROJECT-FACT-UNRESOLVED
RALPH-FACT: migration_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: transaction_boundaries: PROJECT-FACT-UNRESOLVED
RALPH-FACT: recovery_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: consistency_and_concurrency_model: PROJECT-FACT-UNRESOLVED
RALPH-FACT: backup_restore_test_and_rpo_rto: PROJECT-FACT-UNRESOLVED
RALPH-FACT: retention_and_access_control: PROJECT-FACT-UNRESOLVED
RALPH-FACT: applicability_decision: PROJECT-FACT-UNRESOLVED
## AI execution instructions
Agents MUST test forward migration and relevant compatibility behavior, preserve data invariants, and document irreversible operations.
Agents MUST run declared schema/migration gates in safe test environments and
update changed facts. Agents MUST NOT test destructive recovery against
production or invent backup/restore evidence.
## Verification
<!-- REPLACE-ME: declare the real schema and migration gate, then delete this comment. -->
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
## Exceptions
Exceptions require affected data, failure impact, recovery plan, owner, and review date.
## Maintenance triggers
Review when a store, schema, migration framework, transaction boundary, retention rule, or recovery process changes.
## Research basis
* publisher: PostgreSQL Global Development Group
  title: "PostgreSQL Documentation: Data Definition"
  http: https://www.postgresql.org/docs/current/ddl.html
  review date: 2026-07-12
* publisher: National Institute of Standards and Technology
  title: "Contingency Planning Guide for Federal Information Systems"
  http: https://csrc.nist.gov/pubs/sp/800/34/r1/upd1/final
  review date: 2026-07-12
## Living document contract
This is a living document. Verified project facts determine implementation details; mandatory outcomes remain unless narrowed by a scoped, owner-approved, expiring exception. Stronger legal, contractual, security, or safety obligations win.
## Ralph markers
* Policy id: `<!-- ralph-policy-id: data-storage-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
