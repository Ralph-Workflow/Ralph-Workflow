<!-- ralph-policy-schema: v6 -->
<!-- ralph-policy-id: clean-code-policy.md -->

# Clean-Code Policy

## Purpose and scope

This policy governs project-specific naming, structure, module-boundary,
and readability rules. It defines the expectations for function, class,
module, and file scope and responsibility; the limits on dead code and
premature extensibility; the error-handling and logging conventions; the
rules for refactoring code that cannot be tested or understood cleanly;
and the treatment of generated, vendored, migration, and compatibility
code.

The maintained implementation is the Python package under
`ralph-workflow/ralph/`; the conventions below describe Python-first
practice and adapt to other detected languages (see Verification).

## Default requirements

* The agent MUST follow project-specific naming conventions verified
  against existing source. Generic style guides do NOT override the
  project's actual convention.
* The agent MUST prefer simple, maintainable solutions and existing
  project patterns over speculative abstractions or duplicated helpers.
  An abstraction with one implementation and one caller is premature.
* Dead code, commented-out code, unused compatibility layers, and
  unused imports MUST be removed. The "we might need it later"
  rationale does not override the no-dead-code rule.
* Function, class, module, and file scope MUST follow single
  responsibility: a function does one thing, a module owns one
  concept. Functions over `pylint.max-args = 40` (the CLI typer
  callback floor) MUST be justified inline; the project has narrowed
  the ruff floor beyond its default for the same reason.
* Error handling MUST surface actionable context. Bare `except: pass`
  is forbidden. Errors MUST be logged with enough context to diagnose
  (`loguru`-based logging in production code, with the structured
  fields from `ralph/logging_models.py`).
* Code that cannot be tested cleanly MUST be refactored at the
  boundary, not patched with white-box tests. Black-box testability is
  the contract (see testing-policy.md).
* Generated code, vendored code, migration code, and compatibility
  code MUST be marked as such (via filename, header, or directory
  location) and excluded from generic lint/typecheck rules.
* Mutable collection literals at module level or assigned to `self.X`
  in `__init__` MUST carry a FIFO/size cap (`deque(maxlen=...)`,
  `OrderedDict` with a count cap) or a documented
  `# bounded-accumulator-ok: <reason>` marker. Unbounded accumulators
  retain heavyweight objects across a long unattended run and are the
  bug class the resource-lifecycle audit
  (`ralph.testing.audit_resource_lifecycle`) detects — see
  `ralph-workflow/docs/agents/memory-lifecycle.md` for the contract.

## Commenting policy

A comment records ONLY what the code cannot say: intent, a non-obvious
constraint, an invariant, a workaround with its removal trigger, or an
external contract. A comment that restates the code is noise, and a
comment that contradicts the code misleads the next maintainer.

Banned comment content:

* Restating the code the comment sits on.
* Change narration ("added for X", "fixed", "now", "previously").
* Commented-out code.
* Section banners.
* Notes addressed to the reviewer instead of to the next maintainer.
* A deferred-work marker (a `todo`, `fixme`, or `hack` prefix) that
  carries neither an owner nor a removal trigger.

Editing code means re-reading the adjacent comments: they may have gone
stale. Public-API docstrings stay mandatory
(`ralph.testing.audit_public_docstrings`); this rule does not weaken
that requirement.

Evidence tags differ by claim. The comment/code-inconsistency link to
defects is tagged moderate (the source is listed under Research basis)
and is the basis for the blocking check. The over-commenting claim —
that agents comment too much, and that removing such comments changes
outcomes — is tagged anecdotal and its effect size is unconfirmed, so a
comment that restates the code is reported for review and does not fail
the gate; it is reviewed whenever the comment audit reports it. No
percentage, multiplier, or benchmark figure from either claim belongs in
this policy until it is confirmed against the primary source.

The inline bypass form is `# comment-ok: <reason>`; the reason is
mandatory, and an empty reason is not a bypass. The marker waives its
own line and the line immediately below it.

The comment audit (`ralph.testing.audit_comments`) runs in BASELINE MODE:
it fails only on comments on new or changed lines relative to the
committed baseline, and it honours the bypass above. It scans Python
source (`.py` and `.pyi`); comments in other languages are covered by
the review gate. Burn-down of the existing baseline is a separate,
optional follow-up outside this gate; the remaining count comes from a
one-off scan of the committed tree, not from the gate.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: naming_convention: PEP 8 snake_case for functions, variables, and module names; PascalCase for classes; SCREAMING_SNAKE_CASE for module-level constants. CLI entry points live under `ralph/cli/` and use Typer / rich-click conventions. Tests live under `ralph-workflow/tests/` and mirror the package layout they exercise. Type aliases use the `T = ...` PEP 613 form only when reused across modules.
RALPH-FACT: module_boundary_rule: each subpackage under `ralph/` (agents, api, checkpoint, cli, config, contrib, diagnostics, display, executor, exit_pause, files, git, guidelines, interrupt, language_detector, mcp, phases, pipeline, platform, policy, pro_support, process, project_policy, prompts, recovery, runtime, skills, telemetry, testing, update_check, visual, workspace) owns one concept and is the seam for dependency injection via the shared fixtures in `ralph-workflow/tests/conftest.py` and the per-component fixture modules under `ralph-workflow/tests/_support/`. (`logging` and `timeout` are flat top-level modules — `ralph/logging.py` and `ralph/timeout_defaults.py` — not subpackages, per architecture-policy.md:63.) New public subpackages require an `.. automodule::` entry in `docs/sphinx/modules.rst` (enforced by `tests/test_sphinx_modules_coverage.py`). Circular-import avoidance is delegated to lazy `PLC0415` ignores with rationale, not to a global silencer.
RALPH-FACT: error_handling_convention: raise typed exceptions (custom pydantic-validated models in `ralph/pydantic_validation_errors.py` for canonical subtypes); never `except: pass`. Errors MUST carry actionable context (the affected subject, the failing input, the operator's next step). Subprocess / network failures carry a `timeout=` per the MCP timeout contract. Recovery classifies exceptions via `ralph/recovery/classifier.py:FailureClassifier`; do not classify at the call site.
RALPH-FACT: logging_convention: `loguru` is the production logger (per `[project].dependencies` in `ralph-workflow/pyproject.toml`). Structured fields follow `ralph/logging_models.py`. Logging configuration is centralized in `ralph/_logging_config.py` and `ralph/_logging_paths.py`; per-module `logging.basicConfig` is forbidden. Verbosity toggle lives at `python -m ralph --verbose`.
RALPH-FACT: generated_code_marker: autogenerated audit / verifiers under `ralph/testing/audit_*.py` are marked via module-level docstrings; vendored stubs live under `ralph-workflow/stubs/` (registered via `mypy_path` in `ralph-workflow/mypy.ini`). Migration / compatibility shims live under `ralph/pro_support/` and carry the same `# reason: autogenerated code has no type support` marker that satisfies `audit_typecheck_bypass`. The skills mirror output (`ralph-workflow/skills-package/content/`) is regenerated by `npm run prepack` per `ralph/skills/content/` and is gitignored / repacked, not hand-edited.
RALPH-FACT: vendored_code_marker: vendored first-party type stubs live under `ralph-workflow/stubs/` with a per-stub module docstring declaring the upstream package, the stub scope, and the upstream-version pin. The legacy Rust implementation under `docs/legacy-rust/` is quarantined and carries its own README marking it retired; treat as read-only.
RALPH-FACT: dead_code_audit_command: `make -C ralph-workflow dead-code` runs `uv run --with vulture python -m vulture --config pyproject.toml`. Vulture's minimum-confidence floor is the single project-wide `min_confidence = 100` set in `ralph-workflow/pyproject.toml` `[tool.vulture]`; there is no separate quick-run / release-only override. Removal rather than annotation is the project default; a `# noqa`-style annotated dead symbol is a defect, not a workaround.

## Guard inventory

| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |
|---|---|---|---|---|---|---|
| Syntax-proxy ban | Clean-code owner | private-call ratchets and blanket functional rules do not reward making a method public or adding a wrapper | no private method is renamed to public solely to satisfy a private-call ratchet; no wrapper exists only to satisfy a blanket functional rule | no private method is renamed to public solely to satisfy a private-call ratchet | advisory: code review verifies visibility; review: when private-call ratchet is reviewed | when every private-call ratchet is retired |
| Bounded premature abstraction | Clean-code owner | new abstractions are justified by demonstrated boundary, volatility, testing, or reuse value rather than implementation count | abstraction is justified by demonstrated boundary, volatility, testing, or reuse value; implementation count alone neither requires nor forbids abstraction | abstraction is justified by demonstrated boundary, volatility, testing, or reuse value | advisory: code review verifies boundary value; review: on new abstraction introduction | when the project stops growing the abstraction target |
| Comment content | Clean-code owner | the comments on a new or changed line carry what the code cannot say | a comment on a new or changed line matches a blocking banned comment shape listed under Commenting policy | a comment on a new or changed line matches a blocking banned comment shape | deterministic: comment policy audit (audit_comments) | when the comment gate is retired |

## AI execution instructions

Shared execution rules (truthful evidence, run every declared gate and report actual outcome, do not weaken gates, update facts in the same workflow, honest failure reporting) live in `docs/ralph-workflow-policy/execution-kernel.md` (v6). This file adds only its domain outcomes, facts, triggers, and exceptions. When a rule in this file conflicts with the kernel, the kernel wins and this file is amended.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow dead-code

A second dead-code-relevant gate is the Ruby formula check
(`make -C ralph-workflow formula-check`); its command line is declared
once in linting-policy.md under Verification, so it is not duplicated
here.

The expected successful result is a clean Vulture run (exit 0). Any
finding is reported with file:line and the confidence score; the
remediation is to remove the symbol, not to annotate it.

RALPH-COMMAND: uv run python -m ralph.testing.audit_comments

The comment audit is the binding check for the commenting rule above. It
runs from the Python project root (`ralph-workflow/`), the same
invocation the verify gate uses. The expected successful result is a
clean audit over the new and changed lines (exit 0), honouring the
inline `# comment-ok: <reason>` bypass. A blocking finding is reported
with file:line and rule id; the remediation is to delete the comment or
rewrite it, never to weaken the gate. An advisory finding is reported
for review and does not fail the gate.

The drift audit
(`ralph/testing/audit_resource_lifecycle`) is part of `make verify`
and is the binding check for the resource-accumulator rule above.

## Exceptions

A documented exception (e.g. legacy compatibility shim with a removal
date) requires a documented rationale, scope, owner, and removal or
review date. The exception lives in this section; the symbol itself
carries an inline marker cross-referencing the entry.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new naming convention is introduced.
* A new module boundary rule is introduced.
* The error-handling or logging convention changes.
* A new generated/vendored code category is added.
* The commenting rule changes: a new banned comment shape, a change to
  the bypass form, or a change to the baseline-mode scope.

## Research basis

* publisher: Robert C. Martin ("Uncle Bob")
  title: "The Clean Code Blog"
  http: https://blog.cleancoder.com/
  review date: 2026-08-09

* publisher: Martin Fowler
  title: "Refactoring: Improving the Design of Existing Code"
  http: https://martinfowler.com/books/refactoring.html
  review date: 2026-08-09

* publisher: Google Engineering Practices
  title: "Code Health: Reduce Nesting, Reduce Complexity"
  http: https://google.github.io/eng-practices/review/developer/
  review date: 2026-08-09

* publisher: Sandi Metz
  title: "Practical Object-Oriented Design in Ruby"
  http: https://www.poodr.com/
  review date: 2026-08-09

* publisher: arXiv (Radmanesh, Imani, Ahmed, Moshirpour)
  title: "Investigating the Impact of Code Comment Inconsistency on Bug Introducing"
  http: https://arxiv.org/abs/2409.10781
  review date: 2026-11-09

* publisher: Google Style Guides
  title: "Documentation Best Practices"
  http: https://google.github.io/styleguide/docguide/best_practices.html
  review date: 2026-11-09

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

* Policy id: `<!-- ralph-policy-id: clean-code-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v6 -->`
