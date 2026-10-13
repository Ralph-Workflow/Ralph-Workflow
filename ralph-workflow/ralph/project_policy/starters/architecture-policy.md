<!-- ralph-policy-schema: v5 -->
<!-- ralph-policy-id: architecture-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: starter template; replace verified facts and
commands, then delete this banner. -->

# Architecture Policy

## Purpose and scope

This policy governs component boundaries, dependency direction, state
ownership, external I/O, and durable contracts. It records this project's
architecture without imposing a named pattern or directory layout.

## Default requirements

* Dependencies MUST follow a documented direction; cycles across declared
  architectural components and forbidden cross-boundary imports are prohibited.
* Domain decisions MUST remain separable from delivery frameworks and external
  I/O where the project has such distinctions.
* Mutable state, transactions, concurrency, caches, and background work MUST
  have explicit owners and lifecycles.
* Public APIs, CLI behavior, protocols, schemas, and persisted formats MUST be
  treated as compatibility boundaries when consumers depend on them.
* New abstractions MUST solve a demonstrated boundary, volatility, testing, or
  reuse need; implementation count alone neither requires nor forbids an
  abstraction.
* Durable contracts (registries, classifiers, name lists) are conditional on
  crossing a documented system boundary where external or decoupled consumers
  depend on them; internal-only implementations need not enter named
  registries or classifiers solely because a starter pattern exists.
* Material, hard-to-reverse architectural decisions MUST be recorded durably.
* Architecture MUST be evaluated against explicit quality-attribute scenarios,
  stakeholder needs, operational context, and known tradeoffs rather than a
  preferred pattern in isolation.
* Relevant static, runtime, deployment, interface, and data-flow views MUST be
  documented when those views affect design or operation.

## Guard inventory

| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |
|---|---|---|---|---|---|
| Process and resource ownership | Architecture owner | process / resource ownership is explicit: every long-lived process, file lock, socket, and signal handler is owned by a named module that closes it on every code path | code review requires every long-lived resource to be owned and closed; resource-lifecycle checks return zero findings | code review requires every long-lived resource to be owned and closed | when the project has no long-lived resources |
| Conditional durable contracts | Architecture owner | durable contracts (registries, classifiers, name lists) are required only when the system that consumes them actually exists in the project | every required registry / classifier has a named consumer; no required registry / classifier is in place solely because a starter said so | every required registry / classifier has a named consumer | when the consumer system is removed |

## Project facts to resolve

The lines below are the verified project facts that agents rely on and keep
current.

<!-- REPLACE-ME: record verified paths and rules; for an unsettled fact record
the current decision and its review trigger, then delete this comment. -->

RALPH-FACT: architecture_style: PROJECT-FACT-UNRESOLVED
RALPH-FACT: component_map: PROJECT-FACT-UNRESOLVED
RALPH-FACT: dependency_direction: PROJECT-FACT-UNRESOLVED
RALPH-FACT: forbidden_dependencies: PROJECT-FACT-UNRESOLVED
RALPH-FACT: state_ownership: PROJECT-FACT-UNRESOLVED
RALPH-FACT: external_io_boundaries: PROJECT-FACT-UNRESOLVED
RALPH-FACT: durable_contracts: PROJECT-FACT-UNRESOLVED
RALPH-FACT: decision_record_location: PROJECT-FACT-UNRESOLVED
RALPH-FACT: quality_attribute_scenarios: PROJECT-FACT-UNRESOLVED
RALPH-FACT: runtime_and_deployment_views: PROJECT-FACT-UNRESOLVED
RALPH-FACT: major_data_flows: PROJECT-FACT-UNRESOLVED
RALPH-FACT: known_risks_and_tradeoffs: PROJECT-FACT-UNRESOLVED
RALPH-FACT: conformance_method: PROJECT-FACT-UNRESOLVED

## AI execution instructions

This section is the architecture-specific procedure only. The five
generic rules (truthful evidence, fail-closed verification, bounded
execution, no weakening of checks, explicit unverified-claim
reporting), the precedence over domain policies, the
selective-applicability rule, and the claim-scope rule are owned by
the kernel starter (`execution-kernel.md`) and MUST be loaded from
there — do not re-derive them here. Tests assert at the narrowest
useful observable boundary (see `testing-policy.md`); exposing a
private method or adding a wrapper solely to satisfy a test is a
violation there, not a fitting architectural response here.

Agents MUST inspect the component map before moving responsibilities, preserve
dependency direction, update affected decision records, and remove superseded
architecture rather than leaving parallel paths. Agents MUST identify affected
quality attributes, runtime/data-flow views, and tradeoffs before claiming an
architectural change is fit.

## Verification

<!-- REPLACE-ME: declare an executable architecture/import/contract command
when available; otherwise use a technically justified RALPH-INAPPLICABLE line.
Always resolve the separate RALPH-REVIEW. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: evaluate quality scenarios, views, boundaries, risks, and tradeoffs; evidence: dated architecture review or explicit blocker; owner: architecture owner

Success means no forbidden dependency, cycle, boundary violation, or unreviewed
contract break.

## Exceptions

An exception requires scope, rationale, owner, and a removal or review trigger.

## Maintenance triggers

Review this policy when components, dependency direction, state ownership,
external boundaries, or durable contracts change.

## Research basis

* publisher: Martin Fowler
  title: "Software Architecture Guide"
  http: https://martinfowler.com/architecture/
  review date: 2026-07-12

* publisher: Carnegie Mellon Software Engineering Institute
  title: "Software Architecture"
  http: https://www.sei.cmu.edu/software-architecture/
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

* Policy id: `<!-- ralph-policy-id: architecture-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v5 -->`
