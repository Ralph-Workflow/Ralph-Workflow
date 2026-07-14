# Shared Subagent Smoke Scenario Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared `--subagents` smoke option, with an optional delegated-task prompt file, that verifies native subagent dispatch, result parsing, post-result progress, and normal smoke completion.

**Architecture:** Extend the common smoke plumbing with an opt-in scenario flag and ordered parsed-event analysis. Keep transport wrappers thin, preserve the default scenario exactly, and expose shared results through the existing CLI report and table.

**Tech Stack:** Python 3.12, Typer, Pydantic configuration models, pytest, Ralph's agent parsers and smoke plumbing.

---

### Task 1: Define the shared scenario contract

**Files:**
- Modify: `ralph-workflow/tests/test_cli_smoke.py`
- Modify: `ralph-workflow/ralph/pipeline/plumbing/smoke_plumbing.py`

- [ ] Write failing tests proving the default prompt is unchanged and the opt-in prompt requires one bounded native subagent task followed by main-agent work.
- [ ] Run `uv run pytest -q tests/test_cli_smoke.py -k "prompt and subagent"` and confirm the new expectation fails because the option is absent.
- [ ] Add the smallest shared scenario input to `_build_smoke_prompt` and `SmokeRunParams` without adding transport-specific branches outside the existing prompt builder.
- [ ] Re-run the focused tests and confirm they pass with no warnings.

### Task 2: Support custom delegated-task prompts

**Files:**
- Modify: `ralph-workflow/tests/test_cli_smoke.py`
- Modify: `ralph-workflow/ralph/cli/commands/smoke.py`
- Modify: `ralph-workflow/ralph/pipeline/plumbing/smoke_plumbing.py`

- [ ] Write failing tests for a UTF-8 `--subagent-prompt-file`, an empty file, an unreadable file, and use without `--subagents`.
- [ ] Run the focused CLI tests and confirm failure occurs because the option is not implemented.
- [ ] Read and validate the file at the CLI boundary, then inject its content only into the delegated-task section of the shared prompt.
- [ ] Re-run the tests and confirm invalid input fails before agent invocation while valid content preserves all harness-owned requirements.

### Task 3: Detect ordered subagent evidence

**Files:**
- Modify: `ralph-workflow/tests/test_harness_run_diagnosis.py`
- Modify: `ralph-workflow/ralph/pipeline/plumbing/smoke_plumbing.py`

- [ ] Write failing black-box tests with parsed transcript fixtures for dispatch plus result plus later progress, missing dispatch, missing result, and no post-result progress.
- [ ] Run the focused test nodes and confirm they fail because `SmokeRunResult` has no subagent evidence surface.
- [ ] Parse tool-use and tool-result metadata in order, recognize native subagent tools by their normalized tool names, and add scenario-gated errors for the first missing signal.
- [ ] Re-run the focused tests and confirm all cases pass.

### Task 4: Expose and render the shared option

**Files:**
- Modify: `ralph-workflow/tests/test_cli_smoke.py`
- Modify: `ralph-workflow/ralph/cli/commands/smoke.py`
- Modify: `ralph-workflow/ralph/cli/main.py`

- [ ] Write failing tests proving each interactive smoke command forwards `--subagents` and `--subagent-prompt-file`, and the report/table display the observed subagent status.
- [ ] Run the focused CLI tests and confirm failure occurs at the missing option or result fields.
- [ ] Thread the boolean through `smoke_harness_agent_command`, each command wrapper, `run_smoke_plumbing`, and `_run_smoke_agent`; render a concise subagent column and report line.
- [ ] Re-run the CLI tests and confirm the default calls remain backward compatible.

### Task 5: Document the diagnostic option

**Files:**
- Modify: `ralph-workflow/docs/sphinx/cli.md`
- Modify: `ralph-workflow/docs/agents/adding-a-new-agent.md`

- [ ] Run `./scripts/fabrication_guard.py --level 1` on both files before editing.
- [ ] Document the exact `--subagents` and `--subagent-prompt-file` commands, ordered pass criteria, supported shared harness behavior, token cost, and intended use for parser/activity/watchdog debugging.
- [ ] Re-run the level-1 fabrication guard on both files and build the docs with warnings treated as errors.

### Task 6: Verify, commit, and diagnose

**Files:**
- Verify all modified files.

- [ ] Run focused interactive parser and PTY tests from `docs/agents/verification.md`.
- [ ] Run `make verify` and fix every surfaced error or warning.
- [ ] Generate the commit only through `ralph --generate-commit`, as required by `AGENTS.md`.
- [ ] Run `python -m ralph smoke-interactive-claude --subagents` and use the ordered transcript and watchdog evidence to diagnose the reported hang.
