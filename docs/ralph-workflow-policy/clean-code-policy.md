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
  reason does not override the no-dead-code rule.
* Function, class, module, and file scope MUST follow single
  responsibility: a function does one thing, a module owns one
  concept. Functions over `pylint.max-args = 40` (the CLI typer
  callback floor) are refactored or configured in tool configuration;
  the project has narrowed the ruff floor beyond its default for the
  same reason.
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
  `OrderedDict` with a count cap). Unbounded accumulators
  retain heavyweight objects across a long unattended run and are the
  bug class the resource-lifecycle audit
  (`ralph.testing.audit_resource_lifecycle`) detects — see
  `ralph-workflow/docs/agents/memory-lifecycle.md` for the contract.

## Verifiable constructs and banned escape hatches

Code uses only constructs the type checker and linter can fully verify. The
default is ban. The burden of proof is on allowing a construct, never on
banning it: a construct remains allowed only when its common use is safe and
fully verifiable by both tools. Occasional need, careful use, or restriction to
one module does not justify allowance. There are no scoped exemptions, inline
suppressions, per-file ignores of a banned rule, allowlists, or baselines. The
only remedy for a finding is restructuring the code. When a banned construct
appears unavoidable, the agent reports the finding and stops instead of
suppressing it. This rule has evidence tag `none` and ships as
`deterministic` because the project's ban gate enforces it.

The catalog names the enforcing linter rule IDs. Uppercase hyphenated
identifiers without a linter prefix are project-gate rule IDs; the remediation
agent maps them to the rule IDs enforced by the project's own type checker and
linter configuration.

| Language | Banned constructs | Enforcing rule IDs | Enforcement |
|---|---|---|---|
| Python | `eval`; `exec`; `compile`; `__import__`; dynamic `importlib` loading; `globals`/`locals`/`vars`; non-literal `getattr`/`setattr`/`delattr`; `Any`; `typing.cast`; `# type: ignore`; `ctypes`/`cffi`; `pickle`/`marshal`; metaclasses; `__getattr__`/`__getattribute__`/`__setattr__` overrides; three-argument `type()`; `inspect` reflection in production; runtime monkeypatching; `global`/`nonlocal`; mutable default arguments; `assert` for runtime validation; bare and broad `except`; named `lambda`; pass-through `*args`/`**kwargs`; `isinstance`/`type` dispatch chains; dynamic attributes without `__slots__`; star imports; `os.environ` outside one config module; `subprocess` with `shell=True`; shared mutable state through `threading`; `sys.path` edits; `__del__`; `__all__` mutation; `print` in library code; `time.sleep` in production; inheritance across modules. | `S307`; `S102`; `BANNED-CALL`; `BANNED-CALL`; `DYNAMIC-IMPORT`; `BANNED-NAMESPACE`; `B009`, `B010`, `DYNAMIC-ATTRIBUTE`; `ANN401`; `TC006`, `TYPING-CAST`; `PGH003`, `TYPE-IGNORE`; `BANNED-IMPORT`; `S403`, `S301`, `S302`, `BANNED-IMPORT`; `METACLASS`; `DYNAMIC-METHOD`; `TYPE-FACTORY`; `INSPECT-REFLECTION`; `MONKEYPATCH`; `PLW0603`, `PLW0602`, `GLOBAL-STATE`; `B006`, `MUTABLE-DEFAULT`; `S101`, `RUNTIME-ASSERT`; `E722`, `BLE001`, `BROAD-EXCEPT`; `E731`, `ASSIGNED-LAMBDA`; `ANN002`, `ANN003`, `VARIADIC-PASSTHROUGH`; `PLR1701`, `TYPE-DISPATCH`; `DYNAMIC-INSTANCE-ATTRIBUTE`; `F403`, `F405`, `STAR-IMPORT`; `OS-ENVIRON`; `S602`, `S604`, `SHELL-TRUE`; `THREAD-STATE`; `SYS-PATH`; `FINALIZER`; `ALL-MUTATION`; `T201`, `PRINT`; `SLEEP`; `CROSS-MODULE-INHERITANCE`. | deterministic: banned constructs audit (audit_banned_constructs) |
| Rust | `unsafe`; `transmute`; `static mut`; `Any`/`downcast`; `#[allow(...)]` on a banned lint; `unwrap`/`expect`/`panic!` outside tests; procedural macros generating untyped code. | `unsafe_code`; `unnecessary_transmutes`, `clippy::useless_transmute`, `BANNED-TRANSMUTE`; `static_mut_refs`; `clippy::downcast_ref`, `BANNED-DOWNCAST`; `BANNED-ALLOW`; `clippy::unwrap_used`, `clippy::expect_used`, `clippy::panic`; `BANNED-UNTYPED-MACRO`. | advisory: no Rust source is present; review: when the first Rust source file lands |
| TypeScript/JavaScript | `any`; assertions to unrelated types; non-null `!`; `@ts-ignore`/`@ts-expect-error`/`@ts-nocheck`; `eval`; `new Function`; `Proxy`/`Reflect`; `with`; `useEffect`; `dangerouslySetInnerHTML`; every `eslint-disable` form. | `@typescript-eslint/no-explicit-any`; `@typescript-eslint/consistent-type-assertions`; `@typescript-eslint/no-non-null-assertion`; `@typescript-eslint/ban-ts-comment`; `no-eval`; `no-new-func`; `BANNED-REFLECTION`; `no-with`; `BANNED-EFFECT`; `react/no-danger`; `eslint-comments/no-use`. | advisory: no TypeScript source and no application JavaScript source is present; review: when the first TypeScript or JavaScript source file lands |
| C/C++ | `void *` data; C-style casts; `reinterpret_cast`; `const_cast`; type-punning unions; variadics; `setjmp`/`longjmp`; raw `new`/`delete`; function-like `#define` macros. | `BANNED-VOID-POINTER`, `cppcoreguidelines-pro-type-reinterpret-cast`; `google-readability-casting`, `cppcoreguidelines-pro-type-cstyle-cast`; `cppcoreguidelines-pro-type-reinterpret-cast`; `cppcoreguidelines-pro-type-const-cast`; `cppcoreguidelines-pro-type-union-access`; `cppcoreguidelines-pro-type-vararg`; `cert-err52-cpp`; `cppcoreguidelines-owning-memory`; `cppcoreguidelines-macro-usage`. | advisory: no C or C++ source is present; review: when the first C or C++ source file lands |
| Go | `unsafe`; `reflect`; `interface{}`/`any` outside a closed generic constraint; `//go:linkname`; `//nolint`. | `govet:unsafeptr`; `BANNED-REFLECT`; `BANNED-ANY`; `BANNED-LINKNAME`; `nolintlint`. | advisory: no Go source is present; review: when the first Go source file lands |
| Java/Kotlin/C# | Reflection APIs; `Unsafe`; raw types; unchecked casts; `dynamic`; `Object`/`Any` parameter types; `@SuppressWarnings`/`#pragma warning disable`. | `BANNED-REFLECTION`; `BANNED-UNSAFE`; `rawtypes`; `unchecked`; `BANNED-DYNAMIC`; `BANNED-OBJECT-PARAMETER`; `BANNED-SUPPRESSION`. | advisory: no Java, Kotlin, or C# source is present; review: when the first Java, Kotlin, or C# source file lands |
| Any other language | Equivalent bans derived by the remediation agent from the project's own type checker and linter documentation. | The remediation agent cites the enforcing rule IDs and leaves no cataloged feature able to disable checking silently. | advisory: no other maintained language is present; review: when the first other-language source file lands |

## Commenting policy

Source code contains no comments. Intent, constraints, and invariants are
expressed through names, types, structure, tests, and assertions. The only
`#` line tolerated is a shebang on line 1 of an executable script, because
the kernel reads it.

Banned without exception:

* Restating the code the comment sits on.
* Change narration ("added for X", "fixed", "now", "previously").
* Commented-out code.
* Section banners.
* Notes addressed to the reviewer instead of to the next maintainer.
* A deferred-work marker: a `todo`, `fixme`, or `hack` prefix.
* Encoding declarations, `# type: ignore`, `# noqa`, `# pragma`,
  `# pylint:` and `# fmt:` suppressions. The underlying warning is
  fixed in code. Inline suppression is always rejected. A
  `[tool.ruff.lint.per-file-ignores]` entry is accepted only when its
  code and pattern match `_PYPROJECT_IGNORE_ALLOWLIST`
  (`ralph/testing/audit_lint_bypass.py`); `audit_lint_bypass` rejects
  any other addition.

Editing code means deleting any comment found in the touched region.
Docstrings are banned the same way as comments: no module, class,
function, or method docstring, and no string-literal statement used as
documentation. Contracts live in names, type annotations, and tests.

Evidence tags differ by claim. The comment/code-inconsistency link to
defects is tagged moderate (the source is listed under Research basis)
and is the basis for the blocking check. The over-commenting claim —
that agents comment too much, and that removing such comments changes
outcomes — is tagged anecdotal and its effect size is unconfirmed. The
ban itself is a project judgment tagged `none`: no recorded source
establishes that the ban lowers the defect rate, and the ban does not
rest on one. The evidence tag and the enforcement status are separate
properties: the tag names the kind of source behind the rule, the
enforcement status names who decides it. The ban ships as
`deterministic` because the comment audit enforces it; it is one of
two exceptions (along with the banned-construct ban) to the
execution-kernel rule that a rule tagged `anecdotal` or `none` ships
advisory.
No percentage, multiplier, or benchmark figure from either claim
belongs in this policy until it is confirmed against the primary
source.

There is no inline bypass marker and no baseline. A comment is a finding
even when it carries a reason, and the gate holds no list of exempted
lines.

The comment audit (`ralph.testing.audit_comments`) enforces this
absolutely: it fails on every comment token and every docstring in
tracked Python, and on every comment in shell, TOML, YAML, Makefile and
Jinja sources, except a line-1 shebang. It reads comments with the
`tokenize` module, so a `#` inside a string is never misread, and it
finds docstrings through the `ast` module. A finding carries
`path:line`, the comment text, and the fix hint "delete the comment or
encode it in code".

## Documentation restriction

A change MAY edit documentation only when it changes user-visible
behaviour, a public interface, an install or run command, or a policy
contract, and then only the single owning document and only the affected
statement.

Documentation states current facts and procedures only. It does not
record why a choice was made, which alternatives were weighed, how the
choice evolved, research notes, exclusion lists, migration narration, or
a log of reasoning. A choice lives in the code, the tests, or the gate
that enforces it, and nowhere else.

No new documentation file, plan, design note, ADR, log, or summary is
created unless the task names the file. Documentation that contradicts
this rule is deleted or reduced to current facts.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: naming_convention: PEP 8 snake_case for functions, variables, and module names; PascalCase for classes; SCREAMING_SNAKE_CASE for module-level constants. CLI entry points live under `ralph/cli/` and use Typer / rich-click conventions. Tests live under `ralph-workflow/tests/` and mirror the package layout they exercise. Type aliases use the `T = ...` PEP 613 form only when reused across modules.
RALPH-FACT: module_boundary_rule: each subpackage under `ralph/` (agents, api, checkpoint, cli, config, contrib, diagnostics, display, executor, exit_pause, files, git, guidelines, interrupt, language_detector, mcp, phases, pipeline, platform, policy, pro_support, process, project_policy, prompts, recovery, runtime, skills, telemetry, testing, update_check, visual, workspace) owns one concept and is the seam for dependency injection via the shared fixtures in `ralph-workflow/tests/conftest.py` and the per-component fixture modules under `ralph-workflow/tests/_support/`. (`logging` and `timeout` are flat top-level modules — `ralph/logging.py` and `ralph/timeout_defaults.py` — not subpackages, per architecture-policy.md:63.) New public subpackages require an `.. automodule::` entry in `docs/sphinx/modules.rst` (enforced by `tests/test_sphinx_modules_coverage.py`). Circular-import avoidance is delegated to a documented lazy import boundary, not to a global silencer.
RALPH-FACT: error_handling_convention: raise typed exceptions (custom pydantic-validated models in `ralph/pydantic_validation_errors.py` for canonical subtypes); never `except: pass`. Errors MUST carry actionable context (the affected subject, the failing input, the operator's next step). Subprocess / network failures carry a `timeout=` per the MCP timeout contract. Recovery classifies exceptions via `ralph/recovery/classifier.py:FailureClassifier`; do not classify at the call site.
RALPH-FACT: logging_convention: `loguru` is the production logger (per `[project].dependencies` in `ralph-workflow/pyproject.toml`). Structured fields follow `ralph/logging_models.py`. Logging configuration is centralized in `ralph/_logging_config.py` and `ralph/_logging_paths.py`; per-module `logging.basicConfig` is forbidden. Verbosity toggle lives at `python -m ralph --verbose`.
RALPH-FACT: generated_code_marker: autogenerated audit / verifiers under `ralph/testing/audit_*.py` are marked by their `audit_` filename prefix; vendored stubs live under `ralph-workflow/stubs/` (registered via `mypy_path` in `ralph-workflow/mypy.ini`). Migration / compatibility shims live under `ralph/pro_support/` and are marked by that directory location.
RALPH-FACT: vendored_code_marker: vendored first-party type stubs live under `ralph-workflow/stubs/` and are marked by that directory location plus the `mypy_path` entry in `ralph-workflow/mypy.ini`; the upstream package and its version pin are recorded in the stub file name. The legacy Rust implementation under `docs/legacy-rust/` is quarantined and carries its own README marking it retired; treat as read-only.
RALPH-FACT: dead_code_audit_command: `make -C ralph-workflow dead-code` runs `uv run --with vulture python -m vulture --config pyproject.toml`. Vulture's minimum-confidence floor is the single project-wide `min_confidence = 100` set in `ralph-workflow/pyproject.toml` `[tool.vulture]`; there is no separate quick-run / release-only override. Removal rather than annotation is the project default; a `# noqa`-style annotated dead symbol is a defect, not a workaround.

## Guard inventory

| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |
|---|---|---|---|---|---|---|
| Syntax-proxy ban | Clean-code owner | private-call ratchets and blanket functional rules do not reward making a method public or adding a wrapper | no private method is renamed to public solely to satisfy a private-call ratchet; no wrapper exists only to satisfy a blanket functional rule | no private method is renamed to public solely to satisfy a private-call ratchet | advisory: code review verifies visibility; review: when private-call ratchet is reviewed | when every private-call ratchet is retired |
| Bounded premature abstraction | Clean-code owner | new abstractions are justified by demonstrated boundary, volatility, testing, or reuse value rather than implementation count | abstraction is justified by demonstrated boundary, volatility, testing, or reuse value; implementation count alone neither requires nor forbids abstraction | abstraction is justified by demonstrated boundary, volatility, testing, or reuse value | advisory: code review verifies boundary value; review: on new abstraction introduction | when the project stops growing the abstraction target |
| Comment and docstring absence | Clean-code owner | source carries no comments and no docstrings | the absolute comment audit returns zero findings over the tracked tree | the absolute comment audit returns zero findings over the tracked tree | deterministic: absolute comment and docstring policy audit (audit_comments) | when the comment gate is retired |
| Python type-system escape-hatch ban | Clean-code owner | the Python ban catalog has zero findings | the Python ban gate reports every cataloged construct | the Python findings reported by the ban gate | deterministic: banned constructs audit (audit_banned_constructs) | when the cataloged constructs become structurally impossible |
| Rust type-system escape-hatch ban | Clean-code owner | the Rust ban catalog has zero findings | the Rust language detector and ban gate report every cataloged construct | the Rust findings reported by the language detector and ban gate | advisory: no Rust source is present; review: when the first Rust source file lands | when the cataloged constructs become structurally impossible |
| TypeScript/JavaScript type-system escape-hatch ban | Clean-code owner | the TypeScript/JavaScript ban catalog has zero findings | the TypeScript/JavaScript language detector and ban gate report every cataloged construct | the TypeScript/JavaScript findings reported by the language detector and ban gate | advisory: no TypeScript or JavaScript source is present; review: when the first TypeScript or JavaScript source file lands | when the cataloged constructs become structurally impossible |
| C/C++ type-system escape-hatch ban | Clean-code owner | the C/C++ ban catalog has zero findings | the C/C++ language detector and ban gate report every cataloged construct | the C/C++ findings reported by the language detector and ban gate | advisory: no C or C++ source is present; review: when the first C or C++ source file lands | when the cataloged constructs become structurally impossible |
| Go type-system escape-hatch ban | Clean-code owner | the Go ban catalog has zero findings | the Go language detector and ban gate report every cataloged construct | the Go findings reported by the language detector and ban gate | advisory: no Go source is present; review: when the first Go source file lands | when the cataloged constructs become structurally impossible |
| Java/Kotlin/C# type-system escape-hatch ban | Clean-code owner | the Java/Kotlin/C# ban catalog has zero findings | the Java/Kotlin/C# language detector and ban gate report every cataloged construct | the Java/Kotlin/C# findings reported by the language detector and ban gate | advisory: no Java, Kotlin, or C# source is present; review: when the first Java, Kotlin, or C# source file lands | when the cataloged constructs become structurally impossible |
| Other-language type-system escape-hatch ban | Clean-code owner | the derived language ban catalog has zero findings | the language detector requires a derived catalog and gate for every other maintained language | the missing or failing derived language catalog reported by the gate | advisory: no other maintained language is present; review: when the first other-language source file lands | when the cataloged constructs become structurally impossible |

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
clean audit over the whole tracked tree (exit 0). A finding is reported
with file:line, the comment text, and the fix hint; the remediation is
to delete the comment or encode it in code, never to weaken the gate.

The drift audit
(`ralph/testing/audit_resource_lifecycle`) is part of `make verify`
and is the binding check for the resource-accumulator rule above.

## Exceptions

A documented exception (e.g. legacy compatibility shim with a removal
date) requires a documented reason, scope, owner, and removal or
review date. The exception lives in this section.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new naming convention is introduced.
* A new module boundary rule is introduced.
* The error-handling or logging convention changes.
* A new generated/vendored code category is added.
* The commenting rule changes: a new banned comment shape, a change to
  the scanned file set, or a change to the enforcement mode.

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
