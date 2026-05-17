# Documentation Map

This repository contains both **current Python documentation** and **legacy Rust-era reference material**.

## Current Python docs

Start here for the maintained package:

- `../START_HERE.md` — fastest path to using Ralph Workflow for free on a real task
- `../ralph-workflow/README.md` — installation, development, and API overview
- `../ralph-workflow/CONTRIBUTING.md` — contributor workflow for the Python package
- `agents/verification.md` — required verification commands
- `agents/type-ignore-policy.md` — suppression policy for `# type: ignore[...]` usage
- `agents/parallelization.md` — parallel development mode guide
- `agents/testing-guide.md` — testing patterns for the Python package
- `agents/workspace-trait.md` — workspace trait documentation
- package docstrings under `../ralph-workflow/ralph/` — authoritative API-level pydoc

## Compatibility redirect stubs (agents/)

These files are retained for backwards compatibility but redirect to canonical guides:

- `agents/python-verification.md` — redirects to `agents/verification.md`
- `agents/integration-tests.md` — redirects to `agents/testing-guide.md`

## Current architecture entry points

The most reliable architecture references today are the Python modules themselves:

- `../ralph-workflow/ralph/cli/main.py`
- `../ralph-workflow/ralph/config/loader.py`
- `../ralph-workflow/ralph/pipeline/orchestrator.py`
- `../ralph-workflow/ralph/pipeline/reducer.py`
- `../ralph-workflow/ralph/phases/__init__.py`
- `../ralph-workflow/ralph/mcp/server/runtime.py`

## Tooling family (current Python)

The `tooling/` family covers Python development tooling:

- `tooling/python-tooling.md` — Python-specific development toolchain

Note: `tooling/remote-build.md` and `tooling/dylint.md` are historical Rust-era references retained for archival purposes.

## Performance family (historical Rust-era reference)

The `performance/` family contains historical Rust-era performance documentation
retained for reference. These documents describe the retired Rust implementation's
performance characteristics and are not current Python guidance:

- `performance/README.md` — historical performance overview
- `performance/memory-budget.md` — historical memory management (Rust)
- `performance/monitoring-guide.md` — historical performance monitoring (Rust)
- `performance/optimization-guide.md` — historical optimization patterns (Rust)

For Python performance guidance, refer to the package docstrings and Sphinx documentation.

## Operational guides (current Python)

- `agent-compatibility.md` — provider capability matrix
- `quick-reference.md` — command quick reference
- `template-guide.md` — template usage
- `git-workflow.md` — Git workflow with Ralph
- `free-open-source-proof.md` — how to evaluate a first real free and open-source Ralph Workflow run and what reviewable output should look like
- `when-unattended-coding-fits.md` — quick good-fit vs bad-fit guide for choosing a first real unattended task

## Architecture family (mixed state — see docs/architecture/README.md for explicit classification)

`docs/architecture/` contains mixed-state architecture documentation. Not all pages describe current Python behavior:

**Maintained current Python-facing architecture docs:**

- `architecture/pipeline-lifecycle.md` — end-to-end pipeline lifecycle (planning → development → commit → review/fix), policy-driven orchestration
- `architecture/event-loop-and-reducers.md` — event loop, reducer architecture, policy-based routing
- `architecture/parallel-fan-out.md` — same-workspace v1 parallel fan-out: `allowed_directories` path isolation, `.agent/workers/<unit_id>/` namespaces, artifact-based worker completion, no per-worker branches/worktrees/merge-back

**Historical Rust-era reference (not current Python behavior):**

- `architecture/checkpoint-and-resume.md` — references retired Rust `src/checkpoint/` paths
- `architecture/agents-and-prompts.md` — references retired Rust `src/agents/` paths
- `architecture/git-and-rebase.md` — references retired Rust libgit2 paths
- `architecture/analysis-agent.md` — historical Rust reference
- `architecture/effect-system.md` — historical Rust reference
- `architecture/streaming-and-parsers.md` — historical Rust reference
- `architecture/mcp-upstream-proxy.md` — historical Rust reference
- `architecture/codebase-tour.md` — historical Rust reference
- `architecture/logging-and-observability.md` — historical Rust reference
- `architecture/memory-budget.md` — historical Rust reference
- `architecture/memory-safety.md` — historical Rust reference

For current checkpoint/resume behavior, see the Python source under `ralph-workflow/ralph/checkpoint/`. For current agent behavior, see `ralph-workflow/ralph/agents/`. For current git operations, see `ralph-workflow/ralph/git/`.

## Migration family (mixed state — see individual files for status)

**Maintained current public migration guides:**

- `migration/policy-v2.md` — policy-driven orchestration migration guide (current Python behavior)
- `migration/parallel-mode.md` — parallel v1 migration guide: same-workspace only, `allowed_directories` isolation, `.agent/workers/<unit_id>/` namespace, artifact-based completion, no per-worker branches/worktrees/merge-back

**Current specialized reference (still accurate):**

- `migration/error-response-format.md`
- `migration/plan-xsd-equivalence-notes.md`
- `migration/weak-model-json-schema-conventions.md`
- `migration/xml-deprecation-timeline.md`
- `migration/xsd-to-json-schema-mapping.md`

## RFC and plans families (historical/reference-only)

`docs/RFC/` and `docs/plans/` are historical/reference-only surfaces. They contain design history and implemented plans from the Rust era. They are not maintained operator guidance:

- `RFC/` — request-for-comment design docs from the Rust implementation era
- `plans/` — implemented plan documents; retained for historical reference
  - Exception: `docs/tooling/remote-build.md` links to `docs/plans/2026-04-08-remote-build-server.md` for one-time remote machine setup steps (labeled historical/reference in both the source and target)

## Code-style and performance families (historical Rust-era reference)

`code-style/` and `performance/` contain historical Rust-era reference material. The Python package uses standard Python tooling (ruff, mypy, pytest) documented in `tooling/python-tooling.md` and verified via `cd ralph-workflow && make verify`.
