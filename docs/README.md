# Documentation Map

> **Codeberg is the primary repo for Ralph Workflow.**
> Inspect, star, watch, and open issues there first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> The GitHub mirror stays in sync here: <https://github.com/Ralph-Workflow/Ralph-Workflow>

This repository contains both **current Python documentation** and **legacy Rust-era reference material**.

If you are evaluating Ralph Workflow rather than maintaining it, start with these four questions first:

- **What is it?** A free and open-source tool that orchestrates coding agents you already run on your own machine.
- **Who is it for?** Developers and technical teams handing off engineering work that is too big to babysit and too risky to trust blindly.
- **Why is it different?** Ralph is built to return a reviewable result in the repo — not just a transcript and a "done" claim.
- **Why use it now?** You can try it for free on one real backlog task and judge it with one question: would you merge this?

Important expectation for evaluators: Ralph Workflow is free and open source, but it orchestrates coding agents you already have on your own machine. For the cleanest first impression, have at least one supported agent CLI already installed and already authenticated before you start the first-run docs.

## The four docs most evaluators actually need

If you are here to decide whether Ralph Workflow is worth a real try, start with these:

1. `../START_HERE.md` — shortest honest first-run path
2. `example-review-bundle.md` — public proof of the morning-after handoff shape
3. `review-ai-coding-output-before-merge.md` — the five-minute merge test for judging whether the result really holds up
4. `after-your-first-run.md` — the shortest path from a private first run to a Codeberg star/watch or a useful issue

Everything else in this docs map is secondary until after that first Codeberg-first evaluation.

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
- `example-review-bundle.md` — public sample of a morning-after handoff: prompt, development result, review/fix notes, and machine-readable artifacts
- `after-your-first-run.md` — quick post-run scorecard and Codeberg-first next-step guide
- `first-task-guide.md` — practical first-task chooser for the first real unattended run
- `which-agent-should-i-start-with.md` — practical first-run agent-choice guide for Claude Code, Codex, and OpenCode users
- `claude-code-automation.md` — practical guide for developers who already like Claude Code but need a trustworthy automation / overnight finish path
- `run-claude-code-overnight-without-babysitting.md` — practical plain-language page for the exact "Claude Code overnight" / "without babysitting" search intent
- `claude-code-approval-mode.md` — practical guide for developers whose approval or plan mode still leaves them doing approval babysitting instead of morning-after review
- `ralph-workflow-vs-opencode.md` — practical comparison for developers deciding between staying interactive in OpenCode and handing off a reviewable unattended run
- `first-task-prompt-templates.md` — copy-paste starter prompt shapes for feature work, validation, refactors, tests, and docs
- `free-open-source-proof.md` — how to evaluate a first real free and open-source Ralph Workflow run and what reviewable output should look like
- `review-ai-coding-output-before-merge.md` — practical merge-review checklist for deciding whether the handoff is trustworthy
- `bounded-autonomy-for-unattended-coding.md` — practical guide for keeping unattended runs bounded, fail-closed, and reviewable
- `remote-supervision-of-coding-agents.md` — practical guide for converting live-supervision pain into a cleaner morning-after review path
- `open-source-ai-coding-orchestrator.md` — Codeberg-first explanation of what Ralph Workflow adds if you are comparing open-source AI coding orchestrators
- `ai-agent-orchestration-cli.md` — practical comparison page for developers evaluating orchestration CLIs by the quality of the reviewable handoff
- `unattended-coding-agent.md` — practical page for developers searching for an unattended coding agent they can trust to produce a morning-after handoff instead of a transcript
- `spec-driven-ai-agent.md` — explanation of why Ralph Workflow is built around a spec-first finish line instead of a prompt-first loop
- `what-a-good-ai-coding-finish-receipt-looks-like.md` — what a short trustworthy morning-after handoff should contain so review does not start with transcript archaeology
- `when-unattended-coding-fits.md` — quick good-fit vs bad-fit guide for choosing a first real unattended task
- `why-worktrees-are-not-enough.md` — practical comparison for teams already using worktrees who still need a reviewable unattended handoff
- `ralph-workflow-vs-claude-code.md` — practical comparison for developers deciding between staying interactive in Claude Code and handing off a reviewable unattended run
- `ralph-workflow-vs-codex-cli.md` — practical comparison for developers deciding between staying interactive in Codex CLI and handing off a reviewable unattended run
- `claude-code-codex-workflow.md` — practical guide for splitting Claude Code and Codex across plan/build/review without manual glue chaos
- `what-breaks-first-with-multiple-coding-agents.md` — practical guide for teams already running parallel agents who need a cleaner merged-state and morning-after handoff
- `ralph-workflow-vs-aider.md` — comparison page for developers deciding between interactive pair programming and unattended reviewable handoff

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
