<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: typechecking-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, deletes this banner, and adds the completion
marker. Readiness stays blocked while this banner or any placeholder token
remains. -->

# Type-Checking Policy

## Purpose and scope

This policy governs every project language for which static type checking
applies. It defines the selected type checker, the exact commands, the
source categories included, the strictness level, and the rules around
suppressions, ignores, casts, generated code, and untyped dependencies.

## Default requirements

* The agent MUST declare a `RALPH-LANG:` block for EVERY language
  detected in the project stack, followed by a `RALPH-COMMAND:` or an
  explicit `RALPH-INAPPLICABLE:` line with a reason.
* The selected type checker MUST match the language ecosystem best
  practice (mypy for Python, tsc for TypeScript, cargo check for Rust,
  go build for Go, etc.). A non-standard checker is permitted only
  with a documented rationale.
* Suppressions (`# type: ignore`, `@ts-ignore`, `#[allow(...)]`) MUST
  carry a specific error code AND a documented rationale. Blanket
  suppressions are forbidden.
* Generated code, vendored code, and migration code MUST be excluded
  from type checking via configuration (not by inline suppression). The
  exclusion pattern MUST be listed in this policy.
* Untyped third-party dependencies MUST be stubbed at the project's
  type boundary, NOT silenced globally. A blanket `ignore_missing_imports`
  is forbidden.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

RALPH-FACT: typechecker_per_language: PROJECT-FACT-UNRESOLVED
RALPH-FACT: strictness_level: PROJECT-FACT-UNRESOLVED
RALPH-FACT: excluded_paths: PROJECT-FACT-UNRESOLVED
RALPH-FACT: stubbed_dependencies: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suppression_rationale_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_gate_integration: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* DECLARE one `RALPH-LANG:` block per language with the exact checker
  command. Do not invent languages; do not omit detected languages.
* PREFER existing tooling and configuration. Adding a new type checker
  requires a documented rationale and a benchmarked benefit.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the type checker, strictness level, or
  exclusion patterns.

An agent MUST NOT:

* Add `ignore_missing_imports`, `follow_imports = silent`, or similar
  global silencers without a per-dependency rationale.
* Use `# type: ignore` without a specific error code.
* Weaken the strictness level to obtain a passing result.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: per-language template. Keep one block per project language
with the real checker command (first token must be an approved gate tool;
wrap others in `make`, `uv run`, or `npx`), add blocks for detected
languages missing below, drop blocks for languages the project does not
use, and record genuinely unchecked languages as inapplicable with a
reason. Then delete this comment. -->

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

A project that genuinely does not apply type checking to a detected
language MUST mark that language with `RALPH-INAPPLICABLE:` and a
documented reason. The reason is reviewed at the next policy update.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new language is added to the project.
* The type checker, version, or strictness level changes.
* A new dependency is added that ships without types.
* The suppression policy changes.

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
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` comment; its presence
  certifies this file passed validation when it was last amended.