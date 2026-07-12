<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: privacy-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: starter template; replace verified facts and commands, then delete this banner. -->
# Privacy Policy
## Purpose and scope
This policy governs personal, sensitive, telemetry, and regulated data across collection, use, disclosure, retention, and deletion.
## Applicability
Required when the project collects, receives, derives, transmits, or retains
personal, sensitive, telemetry, or regulated data. Legal conclusions require
owner or qualified review and MUST NOT be inferred by an agent.
If governed data handling ceases, retain a dated inactive decision and
reactivation trigger, or remove the policy through reviewed cleanup.
## Default requirements
* Data categories, purposes, lawful or authorized use, recipients, retention, deletion, and owner MUST be documented.
* Collection and retention MUST be minimized to demonstrated product or operational need.
* Logs, analytics, test fixtures, and AI prompts MUST follow the same handling rules as production flows.
* User or operator controls and deletion/export obligations MUST be tested where applicable.
## Project facts to resolve
The lines below are the verified project facts that agents rely on and keep current.
<!-- REPLACE-ME: record data categories, purposes, retention, deletion/export, and review owner, then delete this comment. -->
RALPH-FACT: governed_data: PROJECT-FACT-UNRESOLVED
RALPH-FACT: permitted_purposes: PROJECT-FACT-UNRESOLVED
RALPH-FACT: retention_and_deletion: PROJECT-FACT-UNRESOLVED
RALPH-FACT: user_controls: PROJECT-FACT-UNRESOLVED
RALPH-FACT: privacy_owner: PROJECT-FACT-UNRESOLVED
RALPH-FACT: jurisdiction_and_owner_basis: PROJECT-FACT-UNRESOLVED
RALPH-FACT: processors_and_data_flows: PROJECT-FACT-UNRESOLVED
RALPH-FACT: consent_preferences_and_purpose_change: PROJECT-FACT-UNRESOLVED
RALPH-FACT: breach_response: PROJECT-FACT-UNRESOLVED
RALPH-FACT: applicability_decision: PROJECT-FACT-UNRESOLVED
## AI execution instructions
Agents MUST trace changed data flows, minimize new collection, avoid production data in tests, and update disclosures and controls.
Agents MUST run declared gates, request owner decisions for jurisdictional or
purpose questions, and report unavailable review as a blocker. Agents MUST NOT
invent legal conclusions, consent, retention, deletion, or review evidence.
## Verification
<!-- REPLACE-ME: declare an actual automated privacy/data-flow command when available; manual review uses the separate RALPH-REVIEW line. Then delete this comment. -->
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review data flows, minimization, owner decisions, retention, deletion, and disclosures; evidence: dated privacy review or explicit blocker; owner: privacy owner
## Exceptions
Exceptions require data scope, purpose, impact, mitigation, owner, and expiry.
## Maintenance triggers
Review when data collection, purpose, recipient, retention, deletion, analytics, or AI processing changes.
## Research basis
* publisher: OWASP Foundation
  title: "OWASP Privacy Risk"
  http: https://owasp.org/www-project-top-10-privacy-risks/
  review date: 2026-07-12
* publisher: National Institute of Standards and Technology
  title: "Privacy Framework"
  http: https://www.nist.gov/privacy-framework
  review date: 2026-07-12
## Living document contract
This is a living document. Verified project facts determine implementation details; mandatory outcomes remain unless narrowed by a scoped, owner-approved, expiring exception. Stronger legal, contractual, security, or safety obligations win.
## Ralph markers
* Policy id: `<!-- ralph-policy-id: privacy-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
