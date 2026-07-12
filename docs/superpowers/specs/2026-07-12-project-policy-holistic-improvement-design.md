# Project Policy Holistic Improvement Design

## Purpose

Improve Ralph Workflow's initialized project-policy system so it establishes
strong software-engineering defaults without forcing one language, tool,
architecture pattern, deployment model, or product shape onto unrelated
codebases.

The result must remain deterministic at the readiness boundary: initialization
and validation may inspect repository evidence and policy markers, but they
must not claim to understand unstructured policy prose probabilistically.

## Success criteria

The initialized policy set must:

* cover cross-cutting engineering risks that apply to nearly every software
  project;
* apply specialized policies only when deterministic repository evidence makes
  the domain relevant;
* state enforceable outcomes while allowing projects to select suitable tools
  and implementation patterns;
* distinguish universal principles from project-specific facts;
* make standard quality gates mandatory whenever the language or project
  surface supports a suitable maintained tool;
* permit inapplicability only for a concrete technical reason, never preference,
  inconvenience, missing configuration, or unwillingness to adopt the gate;
* avoid duplicate ownership between policies;
* remain structurally validated, testable through the public project-policy
  readiness surface, and compatible with the existing remediation workflow.

## Policy taxonomy

### Core policies

Every software project requires the following policies:

1. testing
2. type checking
3. linting
4. dependency management
5. verification
6. agent behavior
7. clean code
8. documentation
9. security
10. architecture

Architecture is core because every codebase has dependency direction, module
boundaries, state ownership, and external boundaries even when it is a small
script or a single-package library. The architecture policy must not prescribe
Clean Architecture, hexagonal architecture, domain-driven design,
microservices, dependency-injection containers, or a directory layout.

### Conditional policies

The following policies are required only when deterministic evidence identifies
the corresponding surface:

* design system for projects with a graphical styling or component surface;
* UX for substantial interactive application flows;
* accessibility for user-facing graphical or web interfaces;
* API compatibility for published libraries, externally consumed APIs, stable
  CLIs, schemas, protocols, or persisted public formats;
* data storage for projects with application-owned databases, migrations, or
  durable data models;
* reliability and observability for deployed services, workers, daemons, or
  other long-running operational processes;
* privacy for projects with repository evidence of personal, sensitive,
  telemetry, or regulated data handling;
* release and deployment for projects that publish packages, binaries, images,
  or deployable services;
* performance and memory usage under their existing evidence-based triggers.

Conditional detection must be conservative. A broad dependency-name substring
or incidental file must not impose an unrelated policy. When reliable
deterministic detection is not possible, the domain should remain opt-in rather
than relying on fuzzy inference.

## Core policy responsibilities

### Architecture

The architecture policy owns:

* the project's architectural style and component map;
* allowed dependency direction and forbidden cross-boundary dependencies;
* placement of domain decisions, orchestration, infrastructure, and external
  I/O;
* ownership and lifecycle of mutable state, concurrency, transactions, caches,
  and background work;
* public and persisted contracts that constrain internal changes;
* error propagation across boundaries;
* extension points and the evidence required for new abstractions;
* triggers for an architecture decision record or equivalent durable decision;
* architecture verification, such as import checks, cycle detection, contract
  tests, or a documented review gate.

Clean-code policy continues to own local naming, readability, function and
module responsibility, error clarity, logging conventions, and dead-code
removal. It must refer architectural boundary questions to the architecture
policy instead of duplicating them.

### Type checking

Every detected programming language must declare its static-checking status.
When the language ecosystem offers a suitable maintained checker, compiler
check, or equivalent static-analysis gate, the project MUST select and run one.

Selection is project-specific and must consider:

* active maintenance and security responsiveness;
* compatibility with the project's language and framework versions;
* meaningful coverage of the project's first-party source;
* diagnostic quality and CI suitability;
* interoperability with generated code and third-party dependencies;
* ecosystem adoption or official support where that evidence is relevant.

The starter must not name one tool as universally preferred. If the language
supports a suitable maintained type checker, compiler check, or equivalent
static type-analysis tool, selecting and running one is mandatory. A language
may be marked inapplicable only when no suitable maintained checker exists or
static checking is technically inapplicable, with a concrete reason and a
review trigger. Team preference, migration effort, pre-existing type errors,
and lack of current configuration do not make type checking inapplicable.
Existing projects may adopt stricter checking incrementally, but they must
establish an enforced baseline immediately and prevent new code from expanding
untyped or unchecked scope.

Generated or vendored code may be excluded with a documented reason. First-party
migrations, compatibility code, and ordinary tests are not excluded merely
because of their category.

### Testing

Testing policy must preserve behavior-first tests, regression coverage, TDD,
determinism, and bounded execution while distinguishing test layers:

* unit tests isolate external I/O;
* integration, contract, system, and end-to-end tests may use controlled real
  resources when that interaction is the behavior under test;
* real resources must be isolated, reproducible, bounded, and cleaned up;
* positive coverage is required for new behavior, while negative coverage is
  required when rejection, failure, permission, boundary, or recovery behavior
  exists.

Automated testing is mandatory for first-party software behavior. A repository
may not declare testing inapplicable merely because it has no current test
framework or because code is difficult to test; it must establish the smallest
real test seam and gate. Only content with no executable or behavior-bearing
surface may document testing as technically inapplicable.

### Linting and formatting

Every detected programming language must have a maintained linter or equivalent
static quality gate whenever its ecosystem provides one. Tool selection must be
based on maintenance, compatibility, diagnostic value, configurability, and CI
suitability rather than a hard-coded product preference. Missing configuration,
legacy findings, or team preference are not valid inapplicability reasons; a
project with existing debt may establish a ratcheted baseline that blocks new
violations while recording a bounded remediation path.

Formatting must be enforced by an established formatter or by a linter with
equivalent deterministic formatting checks whenever the ecosystem supports it.
Projects must avoid competing formatters with overlapping ownership.

### Dependencies

Dependency policy must assess maintenance, provenance, license compatibility,
security, compatibility, footprint, and replaceability without requiring recent
release activity as a proxy for health. Deployable applications require
reproducible dependency resolution. Published libraries must document how they
verify supported dependency ranges and whether lockfiles are used for
development, release, or both.

### Verification and suppressions

Every project needs one authoritative verification entry point. It must run all
applicable declared gates and report failures accurately. Suppressions and
configuration exclusions must be narrow, justified, and reviewable.

The authoritative entry point must include testing and every supported
language's available maintained type-checking, linting, and formatting gates.
It must also include applicable dependency, security, documentation, build,
contract, and domain-specific gates. A supported gate cannot be omitted through
`RALPH-INAPPLICABLE`; projects with legacy debt must adopt a non-regression
baseline and a bounded strengthening plan instead.

Custom bypass-detection tooling is required only when supported by existing
tools or justified by repository risk. A small project must not be forced to
invent a hollow grep gate merely to satisfy policy structure.

### Remaining existing policies

All remaining starters must be reviewed for:

* unjustified universal tool or pattern choices;
* requirements that confuse applications with libraries;
* requirements that confuse unit tests with integration or system tests;
* vague adjectives that cannot guide a future agent;
* exclusions that accidentally remove first-party code from quality gates;
* duplicated ownership between policies;
* mandatory commands for domains where a documented review procedure is the
  only honest gate;
* exception rules that are proportional to risk and have an owner or review
  trigger when appropriate.

Destructive UX actions must use a risk-appropriate safeguard such as
confirmation, undo, soft deletion, versioning, or an explicit non-interactive
override; confirmation dialogs are not universally required. Documentation and
release-note requirements must depend on the project's actual public surfaces.
Security requirements remain fail-closed at trust boundaries while avoiding
irrelevant threat-surface boilerplate.

## Initialization and validation

The starter inventory, required-heading map, readiness evidence, cache
signature, remediation prompt, and tests must stay synchronized.

The validator should prove only deterministic facts:

* required file presence;
* schema and policy identifiers;
* required section presence;
* absence of starter banners and placeholders;
* resolved structured facts;
* applicable per-language declarations;
* usable command form or a technically justified inapplicability declaration
  only where the policy permits inapplicability;
* citation structure;
* conditional-domain evidence;
* migration resolution.

It must not imply that an allowlisted first command token proves a command
exists or succeeds. Remediation must run declared commands and report actual
outcomes. Policy text and diagnostics should describe this distinction
accurately.

The expanded conditional set should be added only where deterministic signals
can be both conservative and explainable. Each detector must return the exact
evidence that triggered it and participate in readiness-cache invalidation.

## Testing strategy

Implementation follows red-green-refactor:

1. Add failing black-box tests for the ten-policy core inventory and expanded
   complete starter inventory.
2. Add failing starter-contract tests for architecture headings, facts,
   command/inapplicability handling, and research structure.
3. Add failing semantic guard tests proving that testing is mandatory and that
   type-checking, linting, and formatting gates cannot be declared inapplicable
   when a supported language has a suitable maintained tool.
4. Add failing evidence and preflight tests for each accepted conditional
   detector, including negative cases that prevent false positives.
5. Add semantic guard tests for other load-bearing starter requirements where a
   future edit could silently reintroduce over-prescriptive behavior.
6. Implement the smallest marker, starter, evidence, preflight, validation, and
   remediation changes that satisfy each failing test.
7. Run focused project-policy tests, lint, and type checking before the full
   repository verification gate.

Tests must use the workspace seam and existing in-memory doubles. They must not
perform network access, real subprocess execution, sleeps, or uncontrolled file
I/O.

## Documentation and migration

Update durable project-policy traceability and any maintained documentation
that states the number or names of initialized policies. Existing customized
project policies must not be overwritten. A schema-version change is required
only if the compatibility contract for already-customized canonical files
cannot safely accommodate the added policy inventory and headings; that choice
must be established by tests before implementation.

All edited public Markdown must pass the fabrication guard before and after
editing. New external references require Level 2 existence verification.

## Independent final review

After implementation and full verification, perform a standalone review of the
resulting initialized policy system. The review must not describe the editing
process, compare old and new wording, or mention which changes were made. It
must evaluate the resulting system as encountered for the first time against:

* engineering-risk coverage;
* applicability across small scripts, libraries, applications, services, and
  polyglot repositories;
* tool and ecosystem neutrality;
* deterministic enforceability;
* clarity of exceptions and inapplicability;
* duplication and ownership boundaries;
* conditional-policy false-positive and false-negative risk;
* maintenance burden and policy sprawl;
* remaining missing policy domains.

Any issue surfaced by that review is a real project issue: fix it, rerun the
relevant focused checks and full verification, then repeat the independent
review until no unresolved material finding remains.
