<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: typechecking-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Type-Checking Policy

## Purpose and scope

This policy governs every maintained first-party language for which static type checking
applies. It defines the selected type checker, the exact commands, the
source categories included, the strictness level, and the rules around
suppressions, ignores, casts, generated code, and untyped dependencies. It
also defines how first-party code MUST be written and structured so the
checker can prove as much as possible: richer types, total functions,
isolated side effects, and no dynamic constructs that create unchecked holes.

## Default requirements

Every maintained first-party language MUST be statically type-checked (the
tooling and enforcement rules follow), AND first-party code MUST be written to
be maximally checkable. The discipline below applies to each language to the
extent its checker can express and enforce it; where a language cannot enforce
a rule statically, the intent still binds and is met by the nearest available
construct.

### Type-checkability discipline

**Make illegal states unrepresentable.**

* MUST model a closed set of values as an enum, literal-union, or sealed type
  — never a bare string or magic number for a known, finite domain.
* MUST keep dynamic-type escapes (`Any`, widening `unknown`, `interface{}`,
  bare `object`, `Dict[str, Any]`, and equivalents) out of checked
  first-party code, except at a boundary that is IMMEDIATELY narrowed to a
  precise type.
* SHOULD represent alternatives and absence with sum types (tagged unions,
  `Result`/`Option`-style types) instead of nullable-everything, sentinel
  values, or a boolean flag paired with optional fields. Model "or" as a sum
  type and "and" as a product type.
* SHOULD give domain primitives distinct types (newtype, branded type,
  `NewType`, or a wrapper struct) — identifiers, quantities, units — so values
  of different meaning cannot be transposed.

**Prefer total functions.**

* MUST make every match/switch over a closed type exhaustive, compiler-
  enforced where the language supports it; do not add a catch-all default that
  silently swallows new variants.
* SHOULD represent "may fail" or "may be absent" in the return type
  (`Result`/`Option`) rather than exceptions-as-control-flow, `null`, or
  unchecked index / first-element / dictionary access.

**Isolate side effects so the core stays pure and checkable.**

* SHOULD keep a pure functional core and push side effects — I/O, mutation,
  clock, randomness, global state — to a thin shell at the edges. The bulk of
  logic SHOULD be pure functions of their inputs, which the checker and tests
  can verify in isolation.
* SHOULD treat data as immutable by default (frozen/readonly/const, enforced
  where the language supports it) and pass dependencies explicitly rather than
  reaching through globals, singletons, or module-level mutable state.

**Structure code for static analysis, not runtime dynamism.**

* MUST avoid reflection, monkeypatching, dynamic attribute get/set
  (`getattr`/`setattr`/`__getattr__` and equivalents), `eval`/`exec`,
  string-keyed dynamic dispatch, and runtime metaprogramming that hides types
  from the checker, in checked first-party code. Use explicit
  interfaces/protocols/traits, typed dispatch tables, enums, and plain data
  instead.
* MUST give a structural or duck-typed expectation an explicit typed contract
  (protocol, interface, or trait) so the checker verifies the expected shape
  rather than trusting it at runtime.

A genuinely required dynamic or effectful boundary — a serialization library,
plugin loader, FFI surface, or framework hook — is NOT an open exception to
the rules above: contain it behind a typed adapter, record it in
`sanctioned_dynamic_boundaries`, and annotate it per the suppression and
live-entry rules below. That set of boundaries is ratcheted; it MUST NOT grow
without a documented justification.

### Tooling, suppressions, and enforcement

A maintained first-party language includes interpreted or compiled production
and test source owned by the project. Generated/vendored code, embedded
snippets, and template/IDL/SQL/shell sources are classified explicitly in
`first_party_languages` and `excluded_language_evidence` according to whether
the project maintains and can statically check them.

* The agent MUST declare a `RALPH-LANG:` block for EVERY language used for
  maintained first-party executable or compiled source, followed by a `RALPH-COMMAND:` or an
  explicit `RALPH-INAPPLICABLE:` line with a reason.
* When a maintained first-party language supports a suitable maintained type checker,
  compiler check, or equivalent static type-analysis tool, the project
  MUST select and run one. Missing configuration, migration effort,
  existing findings, or team preference do not make checking inapplicable.
* Selection MUST consider active maintenance, security responsiveness,
  compatibility, first-party source coverage, diagnostic quality, and CI
  suitability. No product is universally preferred by this policy.
* Suppressions MUST carry the narrowest tool-supported diagnostic identifier
  and a documented rationale. Blanket suppressions are forbidden.
* A suppression is a last resort after fixing the type, narrowing the dynamic
  boundary, adding validation, or improving a local adapter/stub has been tried.
  Every new suppression MUST name the tool limitation or external boundary,
  owner, and removal/review trigger. Suppression count MUST NOT increase
  without a documented exception; legacy debt uses a ratcheted checked
  baseline that rejects new unchecked scope.
* Unused, unreachable, impossible, or obsolete-path diagnostics MUST be
  investigated under the linting policy's zero-tolerance section. Verified
  dead code MUST be removed. A proven live reflection/plugin/FFI/platform or
  external entry point MAY use only the linting policy's evidence-backed,
  narrow live-entry annotation; this is not a dead-code exception.
* Generated and vendored code MAY be excluded with a documented reason.
  First-party migrations, compatibility code, and tests MUST NOT be
  excluded merely because of their category.
* Untyped third-party values MUST be contained by typed adapters, protocols,
  validation, or stubs sufficient to prevent unchecked values escaping into
  checked first-party code. Blanket global silencing is forbidden.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If a fact cannot be resolved yet (project too young, tool not chosen, value
not knowable), defer it with the RALPH-PENDING form "RALPH-PENDING (assumed
<date>); review trigger: <trigger>" — it reaches readiness and a dev-cycle
agent resolves it when its trigger fires. Then
delete this comment. -->

RALPH-FACT: typechecker_per_language: PROJECT-FACT-UNRESOLVED
RALPH-FACT: strictness_level: PROJECT-FACT-UNRESOLVED
RALPH-FACT: excluded_paths: PROJECT-FACT-UNRESOLVED
RALPH-FACT: stubbed_dependencies: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suppression_rationale_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suppression_inventory_and_baseline: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_gate_integration: PROJECT-FACT-UNRESOLVED
RALPH-FACT: maintenance_evidence: PROJECT-FACT-UNRESOLVED
RALPH-FACT: first_party_languages: PROJECT-FACT-UNRESOLVED
RALPH-FACT: excluded_language_evidence: PROJECT-FACT-UNRESOLVED
RALPH-FACT: type_modeling_conventions: PROJECT-FACT-UNRESOLVED
RALPH-FACT: sanctioned_dynamic_boundaries: PROJECT-FACT-UNRESOLVED

For each language, `maintenance_evidence` records tool and version range,
official maintenance source, compatibility and first-party coverage evidence,
selection date, and recheck trigger. Recheck on major language/tool changes,
incompatibility, abandonment, or a relevant security signal.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* DECLARE one `RALPH-LANG:` block per language with the exact checker
  command. Do not invent languages; do not omit maintained first-party languages.
* MODEL closed value sets, alternatives, and domain primitives with precise
  types (enums, sum types, newtypes) before reaching for strings, flags, or
  broad containers, and keep side effects at the edges so the core stays pure.
* PREFER an established project checker when it remains maintained and
  suitable. Record selection evidence when adopting or replacing one.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the type checker, strictness level, or
  exclusion patterns.

An agent MUST NOT:

* Add `ignore_missing_imports`, `follow_imports = silent`, or similar
  global silencers without a per-dependency rationale.
* Use a blanket suppression. Require the narrowest tool-supported identifier
  plus rationale; when no identifier exists, use the narrowest scope and
  document that tool limitation.
* Add a suppression merely to make the checker pass, silence first-party
  design errors globally, or expand the established unchecked baseline.
* Suppress verified dead code or annotate a speculative/unverified consumer.
* Introduce reflection, monkeypatching, dynamic attribute access,
  `eval`/`exec`, or string-keyed dynamic dispatch into checked first-party
  code to avoid writing an explicit typed contract.
* Reach for a bare string, boolean flag, or broad container (`Any`,
  `object`, `interface{}`, `Dict[str, Any]`) where an enum, sum type, or
  precise type expresses the value.
* Weaken the strictness level to obtain a passing result.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: per-language template. Keep one block per maintained first-party language
with the real checker command (first token must be an approved gate tool;
wrap others in `make`, `uv run`, or `npx`), add blocks for detected
languages missing below, drop blocks for languages the project does not
use, and record genuinely unchecked languages as inapplicable with a
reason.
You are FILLING OUT THIS FORM, not fixing the project: record the real
command and confirm it EXISTS (you MAY run it once as a bounded probe to
check that it resolves). Do NOT fix failing checks — type errors, failing
tests, lint findings, audit failures — and do NOT run a suite to green; a
failing or slow gate is the project's problem to address later, not a
form-filling blocker. Run only the commands you declare here, and if you
write a helper script to wire a gate, cover it with a unit test. Then
delete this comment. -->

RALPH-LANG: Python
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: TypeScript
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: Rust
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: Go
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean type check (exit 0) on the
current code base. Never mask type errors with silencers.

## Exceptions

A maintained first-party language may use `RALPH-INAPPLICABLE:` only for one
of two exceptional cases: documented research proves no suitable maintained
checker exists, or a technically non-checkable first-party surface was
misclassified as a checkable language. Preference, inconvenience, legacy
errors, migration cost, and absent setup are invalid. The exact declaration
MUST start with `exceptional case: no suitable maintained checker exists;` or
`exceptional case: technically non-checkable first-party surface;` and include
`evidence:`, `owner:`, `expiry:`, `warning:`, and `review trigger:`. The visible
warning remains until the exception is removed.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new language is added to the project.
* The type checker, version, or strictness level changes.
* A new dependency is added that ships without types.
* The suppression policy changes.
* The sanctioned dynamic boundaries or type-modeling conventions change.

## Research basis

* publisher: Python Software Foundation
  title: "typing — Support for type hints (PEP 484)"
  http: https://docs.python.org/3/library/typing.html
  review date: 2026-07-11

* publisher: Microsoft TypeScript
  title: "TypeScript: Handbook - Type Checking .js Files"
  http: https://www.typescriptlang.org/docs/handbook/type-checking-javascript-files.html
  review date: 2026-07-11

* publisher: The Rust Project
  title: "The Rust Reference: Types"
  http: https://doc.rust-lang.org/reference/types.html
  review date: 2026-07-11

* publisher: mypy (Python typing project)
  title: "Using mypy with an existing codebase"
  http: https://mypy.readthedocs.io/en/stable/existing_code.html
  review date: 2026-07-11

* publisher: Alexis King
  title: "Parse, don't validate"
  http: https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/
  review date: 2026-07-12

* publisher: Scott Wlaschin (F# for fun and profit)
  title: "Designing with types: Making illegal states unrepresentable"
  http: https://fsharpforfunandprofit.com/posts/designing-with-types-making-illegal-states-unrepresentable/
  review date: 2026-07-12

## Living document contract

This policy is a living document. It MUST evolve as the project grows:
update the resolved facts, commands, and requirements whenever verified
project reality changes (new frameworks, new commands, new structure).
Two guardrails bound every amendment:

* Conflicts between this policy's generic defaults and the project's
  established practice are resolved in
  favor of the existing project policy — adapt this file to verified
  project reality, never the reverse. A looser project practice is
  NOT such a conflict: keep the stronger requirement unless a
  documented exception narrows it.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: typechecking-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
