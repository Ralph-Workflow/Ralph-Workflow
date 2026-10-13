<!-- ralph-policy-schema: v5 -->
<!-- ralph-policy-id: api-compatibility-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: starter template; replace verified facts and
commands, then delete this banner. -->

# API Compatibility Policy

## Purpose and scope

This policy governs externally consumed APIs, libraries, CLIs, protocols,
schemas, and persisted public formats.

## Applicability

Required when a project publishes or promises a contract to external consumers.
Internal-only surfaces are excluded only with recorded consumer evidence. If
consumers disappear, retain a dated inactive decision and reactivation trigger,
or remove the policy through reviewed cleanup.

## Default requirements

* Public contracts and supported compatibility windows MUST be explicit.
* Breaking changes MUST be detected, versioned, and communicated; provide a
  migration path or explicitly document why none can exist under the declared
  process.
* Additive changes MUST still be checked for semantic, serialization, and
  consumer compatibility.
* Deprecations MUST include an alternative and removal condition.

## Guard inventory

| Guard | Owner | Protected outcome | Detected condition | Retirement trigger  | Claim scope |
|---|---|---|---|--- |---|
| Public API contract test  | API owner  | every claimed public API has a contract test that fails on a documented breaking change  | public API surface is enumerated; contract test covers each surface; reviewer can name the breaking change the test catches  | when the public API is removed or absorbed by the kernel  | public API surface is enumerated |

## Project facts to resolve

The lines below are the verified project facts that agents rely on and keep
current.

<!-- REPLACE-ME: record public surfaces, compatibility window, versioning, and
deprecation rules, then delete this comment. -->

RALPH-FACT: public_contracts: PROJECT-FACT-UNRESOLVED
RALPH-FACT: compatibility_window: PROJECT-FACT-UNRESOLVED
RALPH-FACT: versioning_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: deprecation_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: consumer_inventory: PROJECT-FACT-UNRESOLVED
RALPH-FACT: compatibility_dimensions: PROJECT-FACT-UNRESOLVED
RALPH-FACT: applicability_decision: PROJECT-FACT-UNRESOLVED

## AI execution instructions

This section is the api-compatibility-specific procedure only. The five
generic rules (truthful evidence, fail-closed verification, bounded
execution, no weakening of checks, explicit unverified-claim
reporting), the precedence over domain policies, the
selective-applicability rule, and the claim-scope rule are owned by
the kernel starter (`execution-kernel.md`) and MUST be loaded from
there — do not re-derive them here.

Agents MUST identify affected consumers, add contract or compatibility
evidence, and update migration guidance for breaking changes. Agents MUST run
declared compatibility gates and update changed facts. Agents MUST NOT infer
that an undocumented surface has no consumers or claim consumer verification
that was not performed.

## Verification

<!-- REPLACE-ME: declare the real contract or compatibility gate, then delete
this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

## Exceptions

Exceptions require affected contracts, consumer impact, migration path, owner,
and deadline.

## Maintenance triggers

Review when a public surface, compatibility window, versioning strategy, or
serialization format changes.

## Research basis

* publisher: Semantic Versioning
  title: "Semantic Versioning 2.0.0"
  http: https://semver.org/
  review date: 2026-07-12

* publisher: Internet Engineering Task Force
  title: "RFC 9745: The Deprecation HTTP Response Header Field"
  http: https://datatracker.ietf.org/doc/rfc9745/
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
`policy-portfolio.toml`; generic rule protection and non-weakening are
governed by `execution-kernel.md §Kernel rules`.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: api-compatibility-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v5 -->`
