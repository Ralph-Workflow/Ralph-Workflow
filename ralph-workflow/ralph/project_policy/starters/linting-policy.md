<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: linting-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Linting Policy

## Purpose and scope

This policy governs the selected linters, their exact commands, the
source categories covered, the rule set, the strictness baseline, the
formatting responsibilities, and the rules around inline suppressions and
configuration overrides.

## Default requirements

A maintained first-party language includes interpreted or compiled production
and test source owned by the project. Generated/vendored code, embedded
snippets, and template/IDL/SQL/shell sources are classified explicitly in
`first_party_languages` and `excluded_language_evidence` according to whether
the project maintains and can lint them.

* When a maintained first-party language supports a suitable maintained linter or
  equivalent static quality tool, the project MUST select and run one.
  Missing configuration, legacy findings, or team preference do not make
  linting inapplicable.
* Selection MUST consider active maintenance, compatibility, useful
  diagnostics, configurability, and CI suitability. No product is
  universally preferred by this policy.
* Formatting MUST be enforced by a maintained formatter or by the linter
  when it provides an equivalent deterministic formatting check. Competing
  tools MUST NOT own the same formatting rules.
* Per-language `RALPH-LANG:` declarations are required for every maintained
  first-party code language, each followed by a `RALPH-COMMAND:` line or an
  explicit `RALPH-INAPPLICABLE:` line.
* Inline suppressions MUST carry the narrowest tool-supported diagnostic
  identifier and a documented rationale. Blanket suppressions are forbidden.
* The authoritative configuration or suppression inventory MUST identify
  file-level disables and broad exclusions with their rationale; duplicating
  every entry in this policy is not required.

## Dead code — zero tolerance

Dead code is prohibited. It is better to delete obsolete code and implement it
again if demonstrated need returns than to retain unowned paths indefinitely.

* Unused, unreachable, obsolete, superseded, commented-out, and speculative
  code MUST be removed in the same change that makes it unnecessary.
* “Might be needed later,” compatibility without a verified consumer, and
  incomplete future plans are never exceptions; version control preserves
  history.
* Dead-code findings MUST be fixed at the source, never hidden with ignores,
  exported-name tricks, fake references, coverage exclusions, or disabled rules.
  A proven live path reached through reflection, a framework/plugin registry,
  FFI callback, dynamic dispatch, conditional platform build, or external
  consumer MAY use the narrowest tool-supported live-entry annotation. This is
  not a dead-code exception: it requires observable consumer/entry-point
  evidence, rationale, owner, and review trigger; speculative consumers do not qualify.
* Generated or vendored code is governed at its source boundary. A retained
  first-party compatibility path requires a verified consumer, owner, removal
  condition, and expiry/review date.
* Every ecosystem-supported unused/dead-code check MUST run in the declared
  lint or verification gate. When no suitable maintained automated check
  exists, code review MUST still enforce deletion.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If the project is too young for a fact to be settled, record the best
current answer plus the condition that will settle it, e.g.
"none yet (assumed <date>; revisit when <trigger>)" — a future agent must
be able to tell a settled fact from a provisional one at a glance. Then
delete this comment. -->

RALPH-FACT: linter_per_language: PROJECT-FACT-UNRESOLVED
RALPH-FACT: rule_set_baseline: PROJECT-FACT-UNRESOLVED
RALPH-FACT: formatter_responsibility: PROJECT-FACT-UNRESOLVED
RALPH-FACT: excluded_paths: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suppression_rationale_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_gate_integration: PROJECT-FACT-UNRESOLVED
RALPH-FACT: maintenance_evidence: PROJECT-FACT-UNRESOLVED
RALPH-FACT: first_party_languages: PROJECT-FACT-UNRESOLVED
RALPH-FACT: excluded_language_evidence: PROJECT-FACT-UNRESOLVED
RALPH-FACT: dead_code_detection_per_language: PROJECT-FACT-UNRESOLVED
RALPH-FACT: retained_compatibility_inventory: PROJECT-FACT-UNRESOLVED
RALPH-FACT: live_entry_point_annotation_inventory: PROJECT-FACT-UNRESOLVED

For each language, `maintenance_evidence` records tool and version range,
official maintenance source, compatibility and first-party coverage evidence,
selection date, and recheck trigger. Recheck on major language/tool changes,
incompatibility, abandonment, or a relevant security signal.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* DECLARE one `RALPH-LANG:` block per language with the exact linter
  command.
* PREFER existing tooling. Adding a new linter requires a documented
  rationale.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the linter, rule set, formatter, or
  suppression policy.

An agent MUST NOT:

* Add `per-file-ignores`, `extend-per-file-ignores`, or any other
  global rule silencing without a per-file rationale.
* Use an inline suppression without the narrowest tool-supported identifier,
  when supported, and an adjacent rationale. Blanket or file-wide suppression
  requires a documented exception.
* Lower the lint strictness level to obtain a passing result.
* Retain verified dead code, suppress it, or add a fake use solely to make a
  linter pass. A verified live-entry annotation follows the evidence contract above.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: per-language template. Keep one block per maintained first-party language
with the real linter command (first token must be an approved gate tool;
wrap others in `make`, `uv run`, or `npx`), add blocks for detected
languages missing below, drop blocks for languages the project does not
use, and record genuinely unlinted languages as inapplicable with a
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

The expected successful result is a clean lint run (exit 0). Lint
failures MUST be addressed at the source, not by adding suppressions.

## Exceptions

A maintained first-party language may use `RALPH-INAPPLICABLE:` only when
documented research proves no suitable maintained linter/formatter exists or
the surface is technically non-lintable. Preference, inconvenience, legacy
findings, migration cost, and absent setup are invalid. The declaration MUST
start with `exceptional case: no suitable maintained linter exists;` or
`exceptional case: technically non-lintable first-party surface;` and include
`evidence:`, `owner:`, `expiry:`, `warning:`, and `review trigger:`. The visible
warning remains until the exception is removed.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new language is added to the project.
* The linter, version, or rule set changes.
* A new lint dependency is added.
* The suppression policy changes.
* The dead-code detector, compatibility inventory, or generated-code boundary changes.

## Research basis

* publisher: Astral (ruff)
  title: "ruff documentation: Rules"
  http: https://docs.astral.sh/ruff/rules/
  review date: 2026-07-11

* publisher: ESLint Project
  title: "Configuring ESLint"
  http: https://eslint.org/docs/latest/use/configure/
  review date: 2026-07-11

* publisher: The Rust Project
  title: "Clippy documentation"
  http: https://doc.rust-lang.org/clippy/
  review date: 2026-07-11

* publisher: Google Engineering Practices
  title: "The CL Author's Guide to Getting Through Code Review"
  http: https://google.github.io/eng-practices/review/developer/
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

* Policy id: `<!-- ralph-policy-id: linting-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
