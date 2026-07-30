<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: release-deployment-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: starter template; replace verified facts and
commands, then delete this banner. -->

# Release and Deployment Policy

## Purpose and scope

This policy governs versioning, build provenance, release artifacts, deployment
promotion, rollback, and user-visible release communication.

## Applicability

Required when the project publishes packages, binaries, images, or deploys an
operational service. Library-only and deployment-only facts may be explicitly
inapplicable to the other project type. Encode a fact-level non-applicability
as `inapplicable: <reason>; review trigger: <condition>`. If all
release/deployment surfaces cease, retain a dated inactive decision or remove
the policy through reviewed cleanup.

## Default requirements

* Release inputs and artifacts MUST be reproducible or carry documented
  provenance and integrity evidence.
* Promotion environments, approvals, secrets, migrations, rollback, and
  post-deploy verification MUST be explicit where deployment exists.
* Published versions and artifacts MUST be immutable.
* User-visible and compatibility-affecting changes MUST be communicated through
  the project's declared channel.
* Every published artifact MUST preserve source identity, provenance, and an
  integrity-verification path. SBOM and signing requirements follow the
  distribution ecosystem and threat model with explicit inapplicability.
* Deployable services MUST define staged promotion, rollback or roll-forward,
  and post-deploy ownership when partial promotion is supported.
* Projects with an emergency or hotfix path MUST define its authorization,
  verification, audit, and return-to-normal process.

## Project facts to resolve

The lines below are the verified project facts that agents rely on and keep
current.

<!-- REPLACE-ME: record artifacts, version source, release/deploy commands,
provenance, rollback, and communication channel, then delete this comment. -->

RALPH-FACT: release_artifacts: PROJECT-FACT-UNRESOLVED
RALPH-FACT: version_source: PROJECT-FACT-UNRESOLVED
RALPH-FACT: promotion_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: provenance_and_integrity: PROJECT-FACT-UNRESOLVED
RALPH-FACT: rollback_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: release_communication: PROJECT-FACT-UNRESOLVED
RALPH-FACT: signing_sbom_and_source_identity: PROJECT-FACT-UNRESOLVED
RALPH-FACT: staged_rollout_and_post_deploy_owner: PROJECT-FACT-UNRESOLVED
RALPH-FACT: emergency_release_process: PROJECT-FACT-UNRESOLVED
RALPH-FACT: applicability_decision: PROJECT-FACT-UNRESOLVED

## AI execution instructions

Agents MUST verify the exact artifact, preserve provenance, account for
migrations, and record release and rollback evidence. Agents MUST run the
required profile for the artifact or environment and update changed facts.
Agents MUST NOT claim signing, provenance, deployment, rollback, or post-deploy
evidence that was not produced.

## Verification

<!-- REPLACE-ME: declare the real build/release/deployment verification gate,
then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

## Exceptions

Exceptions require affected artifact or environment, risk, mitigation, owner,
and review date.

## Maintenance triggers

Review when artifact formats, versioning, CI/CD, signing, promotion, rollback,
or communication changes.

## Research basis

* publisher: OpenSSF
  title: "Supply-chain Levels for Software Artifacts"
  http: https://slsa.dev/
  review date: 2026-07-12

* publisher: Google
  title: "Site Reliability Engineering: Release Engineering"
  http: https://sre.google/sre-book/release-engineering/
  review date: 2026-07-12

* publisher: National Institute of Standards and Technology
  title: "Secure Software Development Framework"
  http: https://csrc.nist.gov/projects/ssdf
  review date: 2026-07-12

## Living document contract

This is a living document. Verified project facts determine implementation
details; mandatory outcomes remain unless narrowed by a scoped, owner-approved,
expiring exception. Stronger legal, contractual, security, or safety
obligations win.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: release-deployment-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
