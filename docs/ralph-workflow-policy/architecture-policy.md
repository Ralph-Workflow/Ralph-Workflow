<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: architecture-policy.md -->

# Architecture Policy

## Purpose and scope

This policy governs component boundaries, dependency direction, state
ownership, external I/O, and durable contracts for the Ralph Workflow
runtime. It records this project's architecture without imposing a named
pattern or directory layout, and it is the single source of truth that
every quality gate (lint, typecheck, audit chain) consults when checking
cross-component imports, module state, and seam placement.

## Default requirements

* Dependencies MUST follow a documented direction; cycles across declared
  architectural components and forbidden cross-boundary imports are prohibited.
  The audit (`ralph/testing/audit_di_seam.py`,
  `ralph/testing/audit_agent_module_state.py`,
  `ralph/testing/audit_agent_internal_paths.py`) enforces the direction
  by walking the import graph and flagging forbidden edges.
* Domain decisions MUST remain separable from delivery frameworks and external
  I/O where the project has such distinctions. Production code reaches
  external I/O through the ralph/files/ seam (FsWorkspace / MemoryWorkspace)
  and the ralph/mcp/ transport, not through direct subprocess or filesystem
  calls.
* Mutable state, transactions, concurrency, caches, and background work MUST
  have explicit owners and lifecycles. Global mutable singletons are
  forbidden; long-lived mutable collections require FIFO/size caps
  (`deque(maxlen=...)`, `OrderedDict` with a count cap) or a documented
  `# bounded-accumulator-ok: <reason>` marker.
* Public APIs, CLI behavior, protocols, schemas, and persisted formats MUST be
  treated as compatibility boundaries when consumers depend on them. The
  pydantic-validated models under ralph-workflow/ralph/pydantic_validation_errors.py
  and ralph-workflow/ralph/logging_models.py are the durable contract
  surface for cross-process payloads.
* New abstractions MUST solve a demonstrated boundary, volatility, testing, or
  reuse need; implementation count alone neither requires nor forbids an
  abstraction. The di_seam and audit_agent_module_state audits
  re-evaluate every new dependency on a per-PR basis.
* Material, hard-to-reverse architectural decisions MUST be recorded durably
  as MADR-format ADRs under ralph-workflow/docs/architecture/ (filename
  pattern: `adr-NNNN-slug.md`); the index lives at
  ralph-workflow/docs/architecture/index.md.
* Architecture MUST be evaluated against explicit quality-attribute scenarios,
  stakeholder needs, operational context, and known tradeoffs rather than a
  preferred pattern in isolation. The `quality_attribute_scenarios` and
  `known_risks_and_tradeoffs` facts below are the maintained list.
* Relevant static, runtime, deployment, interface, and data-flow views MUST be
  documented when those views affect design or operation. The architecture
  ADRs and the operator manual at ralph-workflow/docs/sphinx/ are the
  maintained fabric for these views.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: architecture_style: modular monolithic Python package under ralph-workflow/ralph/ with a thin CommonJS distribution wrapper at ralph-workflow/skills-package/. The Python package owns behaviour (CLI, MCP transport, agent invocation, recovery, watchdog); the JS wrapper is a single-file distribution layer that re-exposes the skills content for downstream installers. There is no microservice boundary, no message bus, and no remote RPC; subprocess and MCP-JSON over stdio are the only cross-process surfaces.
RALPH-FACT: component_map: ralph-workflow/ralph/ is partitioned by concept into the subpackages agents, api, checkpoint, cli, config, contrib, diagnostics, display, executor, exit_pause, files, git, guidelines, interrupt, language_detector, logging, mcp, phases, pipeline, platform, policy, pro_support, process, project_policy, prompts, recovery, runtime, skills, telemetry, testing, timeout, workspace. Each subpackage owns exactly one concept and is the canonical seam for dependency injection into that concern (see ralph-workflow/ralph/testing/conftest.py for the per-component fixtures).
RALPH-FACT: dependency_direction: top-down by subpackage depth. Higher-level subpackages (cli, mcp, agents, pipeline) may import from any lower-level subpackage. Lower-level subpackages (files, workspace, language_detector, logging, timeout) MUST NOT import from higher-level ones. Cross-cutting subpackages (testing, project_policy) live at the leaves and are dev-only — production code MUST NOT import ralph.testing. Circular-import avoidance is delegated to lazy PLC0415 imports at the documented sites in ralph/cli, ralph/config, ralph/display, ralph/mcp/explore, ralph/mcp/tools/workspace, ralph/pipeline (each with a documented rationale in the audit_lint_bypass allowlist).
RALPH-FACT: forbidden_dependencies: (1) ralph.testing MUST NOT be imported from ralph/<production>; the audit_agent_module_state check walks the import graph and flags any production module that reaches into ralph.testing. (2) ralph.* MUST NOT import from the project virtualenv directly (no os.environ / site.getsitepackages) — environment access goes through ralph/_env_loader.py. (3) Cross-component state MUST NOT be passed through module-level mutable globals; all cross-component state flows through the Workspace seam or through explicit constructor injection. (4) ralph.pro_support MUST NOT be required by any open-source path; it is loaded only by the `rdev` development launcher.
RALPH-FACT: state_ownership: each ralph/<subpackage> owns its state and exposes it through its public __init__.py exports. Cross-package state passes through the ralph/files/ seam (FsWorkspace for filesystem, MemoryWorkspace for tests) and through ralph/agents/idle_watchdog/Clock for time. Process-level subprocess state passes through ralph/executor/ (MockProcessExecutor for tests, real executor for subprocess_e2e). No module-level mutable state is shared across subpackage boundaries.
RALPH-FACT: external_io_boundaries: ralph/files/ (FsWorkspace / MemoryWorkspace) owns filesystem access; ralph/mcp/ owns MCP transport over stdio and the local-only HTTP fallback; ralph/agents/ owns subprocess invocation to AI agent CLIs; ralph/executor/ owns the subprocess executor abstraction; ralph/git/ owns git CLI; ralph/pro_support/ owns the Pro services HTTP client. Every external I/O call is funnelled through a typed adapter, and the audit_mcp_timeout + audit_resource_lifecycle checks enforce the bounded-timeout and bounded-accumulator contracts on these adapters.
RALPH-FACT: durable_contracts: (1) cross-process payloads pass through pydantic-validated models in ralph-workflow/ralph/pydantic_validation_errors.py (re-exported by ralph-workflow/ralph/__init__.py). (2) loguru records cross subpackage boundaries through the structured types in ralph-workflow/ralph/logging_models.py. (3) the recovery classifier owns the FailureCategory enum in ralph-workflow/ralph/recovery/classifier.py. (4) the watchdog contract (ralph-workflow/ralph/agents/idle_watchdog/) owns in-stream IdleWatchdog and post-exit PostExitWatchdog with Clock injection. A new durable contract requires a new typed model in pydantic_validation_errors.py or logging_models.py and an entry in the recovery classifier; ad-hoc dict[str, Any] across a boundary is a defect.
RALPH-FACT: decision_record_location: ralph-workflow/docs/architecture/adr-NNNN-slug.md, MADR format. Index at ralph-workflow/docs/architecture/index.md. Existing examples: ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md. A material architectural decision made without an ADR is a process defect; the agent-policy obligates the agent to record the decision before the change merges.
RALPH-FACT: quality_attribute_scenarios: (1) The 60 s combined test budget is the dominant performance scenario — verified by ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS + the _BUDGET_TRACKED_STEPS set; a regression that pushes the suite past 60 s is a fail-closed defect. (2) Determinism is mandatory for the default suite — every test must inject clock / filesystem / subprocess fakes; real I/O lives behind the subprocess_e2e marker. (3) Async I/O is supported via pytest-asyncio (asyncio_mode=auto) for subpackage code that needs it. (4) Memory growth is bounded by the resource-lifecycle audit; long-lived accumulators must declare a cap or get flagged.
RALPH-FACT: runtime_and_deployment_views: (1) install view: `make dev` (uv sync --extra dev) for the development env, `make stable` for the pinned stable env, `make install` for the system-wide install. (2) runtime view: `python -m ralph` CLI; `rdev` launcher for the dev build; `ralph` launcher for the stable build. (3) CI view: Codeberg (Woodpecker) and GitHub Actions both invoke `make verify` on every PR. (4) distribution view: Homebrew formula at ralph-workflow/Formula/ralph-workflow.rb (built via `make dist-homebrew`); PyPI wheel + sdist (built via `make dist-pypi`). (5) skill distribution view: the skills-package under ralph-workflow/skills-package/ is a CommonJS npm-style distribution, packed via `npm run prepack`.
RALPH-FACT: major_data_flows: (1) CLI invocation: `python -m ralph` -> typer CLI -> ralph/agents/invoke.py -> subprocess to the configured agent CLI (Claude Code / OpenCode / Codex / Cursor / Pi) -> NDJSON stream on stdout -> ralph/parsers/* -> pydantic model -> recovery classifier -> output. (2) MCP transport: ralph/mcp/server/__main__.py -> stdio JSON-RPC -> ralph/mcp/server/_fallback_http_handler.py (local-only HTTP) -> ralph/mcp/tools/* -> workspace write/read. (3) Filesystem: any path -> FsWorkspace.write_file / read_file / append_file / edit_file -> root-normalized, path-traversal-checked, audit-tool-exec-checked. (4) Watchdog: agent subprocess -> PostExitWatchdog (Clock-injected) -> recovery classifier -> retry or final output.
RALPH-FACT: known_risks_and_tradeoffs: (1) The 60 s combined test budget is a hard upper bound; pushing it requires a maintenance-trigger review and a recorded decision. (2) mypy --strict + ruff with the documented rule set is a maintenance burden on first-party authors; the cost is paid deliberately to keep cross-package contracts checkable. (3) The Homebrew formula is the only Ruby file; it is syntax-checked by `make formula-check` only — there is no Ruby type checker wired. (4) The legacy Rust implementation under docs/legacy-rust/ is a read-only historical pointer; reintroducing active Rust code requires a recorded decision AND updating the dependency, typecheck, and lint policies in the same workflow. (5) AI agent CLIs are subprocesses and the dominant source of flakiness; the live_agy and subprocess_e2e markers quarantine that flakiness.
RALPH-FACT: conformance_method: `make -C ralph-workflow verify` is the authoritative architecture-conformance gate. It runs the 22-step _VERIFY_STEPS chain in ralph-workflow/ralph/verify.py, including the import-graph checks (audit_di_seam, audit_agent_module_state, audit_agent_internal_paths), the typecheck (`make typecheck` -> `uv run python -m mypy ralph/`), the lint (`make lint` -> `uv run ruff check ralph/ tests/`), the test suite (`make test`, 60 s combined budget), the 17 audit steps, and the social-proof sweep. A red flag from any of these is a cross-component boundary violation, a forbidden import, or a state-ownership leak; the agent MUST fix the cause at the source, never by adding ignore annotations.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* INSPECT the component map before moving responsibilities; the per-subpackage
  seam is the documented injection point in ralph-workflow/ralph/testing/conftest.py.
* PRESERVE the documented dependency direction; lazy imports (PLC0415) at the
  named sites are the only sanctioned escape hatch, each with a documented
  rationale in the audit_lint_bypass allowlist.
* RECORD every material architectural decision in a new MADR ADR under
  ralph-workflow/docs/architecture/ BEFORE the change merges; an
  architecture change without an ADR is a process defect.
* UPDATE the affected `RALPH-FACT:` lines (component_map,
  dependency_direction, forbidden_dependencies, state_ownership,
  external_io_boundaries, durable_contracts, runtime_and_deployment_views,
  major_data_flows) in the same workflow that changes the architecture.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.

An agent MUST NOT:

* Introduce a new subpackage without updating `component_map` and recording
  an ADR.
* Cross a documented component boundary with a direct import; the seam
  is the integration surface.
* Introduce module-level mutable state shared across subpackage
  boundaries; the audit_agent_module_state check rejects this on every PR.
* Weaken the conformance gate to obtain a passing result; the verify chain
  is fail-closed.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow verify

The expected successful result is exit 0 from the 22-step verify chain
in ralph-workflow/ralph/verify.py. On failure, report the failing audit
or step and the failure category (import-graph violation, typecheck error,
lint finding, test failure, audit bypass detection, social-proof
verification). The di_seam / agent_module_state / agent_internal_paths
audits are the architecture-specific subset; their findings are
non-negotiable and MUST be repaired at the source, never by adding an
ignore.

## Exceptions

A documented exception to the dependency direction or the forbidden-import
list requires a MADR ADR under ralph-workflow/docs/architecture/, scope,
rationale, owner, and a removal or review date. Exceptions expire at the
next policy review; an expired exception without an updated rationale is
treated as a violation.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new subpackage is added under ralph-workflow/ralph/.
* A new external I/O dependency is introduced.
* A new ADR is recorded under ralph-workflow/docs/architecture/.
* A cross-component boundary is crossed by a new import.
* A new durable contract (pydantic model, loguru record, classifier
  category) is added.
* The audit_di_seam, audit_agent_module_state, or
  audit_agent_internal_paths audit's allowlist changes.

## Research basis

* publisher: Carnegie Mellon Software Engineering Institute
  title: "Documenting Software Architectures: Views and Beyond"
  http: https://www.sei.cmu.edu/library/documenting-software-architectures/
  review date: 2026-07-12

* publisher: Martin Fowler
  title: "Patterns of Enterprise Application Architecture"
  http: https://martinfowler.com/books/eaa.html
  review date: 2026-07-12

* publisher: ThoughtWorks Technology Radar
  title: "Modular Monoliths over Microservices"
  http: https://martinfowler.com/bliki/MonolithFirst.html
  review date: 2026-07-12

* publisher: Open Agile Architecture group
  title: "Modular and Layered Architectures"
  http: https://www.opengroup.org/architecture/togaf
  review date: 2026-07-12

## Living document contract

This is a living document. Verified project facts determine implementation
details; mandatory outcomes remain unless narrowed by a scoped, owner-approved,
expiring exception. Stronger legal, contractual, security, or safety
obligations win. Two guardrails bound every amendment:

* Conflicts between this policy's generic defaults and the project's
  established practice are resolved in favor of the existing project
  policy — adapt this file to verified project reality, never the reverse.
  A looser project practice is NOT such a conflict: the stronger rule
  wins unless a documented exception narrows it.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: architecture-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`