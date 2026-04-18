# Agent Visibility Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Ralph reliably show meaningful agent activity for Claude, Codex, OpenCode, Gemini, and future agents without blank dashboards, stale live views, silent parser drops, or runaway workspace traversal.

**Architecture:** Introduce an explicit, typed agent-activity contract between parsers and display code, then make the runner and display stack consume that contract instead of inferring visibility from ad hoc strings, metadata, or Rich side effects. Separate three concerns cleanly: agent stream normalization, UI-agnostic visibility state, and terminal rendering/refresh.

**Tech Stack:** Python 3.14, Rich Live, pytest, mypy, ruff, Ralph parser modules, Ralph pipeline runner, Ralph display layer.

---

## Problem Summary

The current stack is viable but brittle because each layer makes implicit assumptions:
- Parsers emit `AgentOutputLine`, which is too weak to encode visibility semantics consistently across providers.
- `runner._render_agent_activity_line()` decides what users should see using agent-specific heuristics, so visibility policy is split between parser and runner.
- `ParallelDisplay` and `RenderThread` maintain a loosely typed `dict[str, list[str] | str]` state, which makes blank or stale rendering easy to introduce.
- Tests validate local mechanics, but not the user-visible contract: “when meaningful agent activity happens, the user sees fresh output in both lines and dashboard modes.”

This plan fixes the current bugs and hardens the architecture for future agent adapters.

---

## Target Architecture

### Canonical Activity Model

Replace the current weak cross-layer protocol with a typed activity model:
- `AgentActivityEvent` (normalized parser output)
- `AgentVisibilityState` (UI-agnostic aggregated state)
- `RenderableSnapshot` (dashboard or line-ready state)

The key rule: **parsers never decide whether something is worth displaying**. Parsers normalize provider streams into explicit event kinds. The runner and visibility layer decide what becomes visible.

### Visibility Contract

Every agent session must obey these invariants:
1. A newly started agent has a non-empty visible state within one refresh cycle.
2. Meaningful activity increments a visibility version or sequence number.
3. Display refresh is triggered by state changes, not by implicit library behavior.
4. Terminal rendering has a guaranteed non-empty fallback for waiting, running, tool activity, and completion.
5. Performance-sensitive workspace discovery is bounded and never tied to render-loop timing.

### Extension Contract for New Agents

Adding a new agent should require only:
- a parser/adapter that emits canonical `AgentActivityEvent`s
- optional provider-specific summarization rules
- conformance tests that validate the shared visibility contract

New agents must not need direct awareness of Rich, dashboard state internals, or runner rendering heuristics.

---

## Task 1: Define explicit cross-layer activity types

**Files:**
- Create: `ralph/display/activity_model.py`
- Modify: `ralph/agents/parsers/base.py`
- Test: `tests/test_activity_model.py`

**Step 1: Write the failing tests**

Add tests covering:
- canonical event kinds exist for `text`, `status`, `tool_use`, `tool_result`, `error`, `lifecycle`, `heartbeat`, `progress`, and `unknown`
- events can represent “visible but no content yet” states
- events distinguish raw transport noise from meaningful activity without discarding source information

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_activity_model.py -q`
Expected: FAIL because the file and types do not exist.

**Step 3: Write minimal implementation**

Implement typed dataclasses/enums for:
- provider name
- event kind
- content payload
- structured metadata
- visibility hint (`visible`, `hidden`, `fallback_only`)
- sequence or timestamp metadata

Keep `AgentOutputLine` temporarily, but mark it as legacy adapter output.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_activity_model.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add ralph/display/activity_model.py ralph/agents/parsers/base.py tests/test_activity_model.py
git commit -m "refactor: add typed agent activity model"
```

---

## Task 2: Add a parser normalization layer for all providers

**Files:**
- Create: `ralph/agents/parsers/normalize.py`
- Modify: `ralph/agents/parsers/base.py`
- Modify: `ralph/agents/parsers/claude.py`
- Modify: `ralph/agents/parsers/codex.py`
- Modify: `ralph/agents/parsers/opencode.py`
- Modify: `ralph/agents/parsers/gemini.py`
- Modify: `ralph/agents/parsers/generic.py`
- Test: `tests/test_parsers.py`
- Test: `tests/test_parser_normalization.py`

**Step 1: Write the failing tests**

Add tests asserting that each parser can produce normalized activity events for:
- visible startup/progress
- text output
- tool invocation
- tool result
- lifecycle transitions
- currently suppressed Claude lines (`system: status=...`, `message_delta`) mapped into non-breaking normalized events

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsers.py tests/test_parser_normalization.py -q`
Expected: FAIL because normalization API does not exist and Claude suppression behavior is still hard-coded.

**Step 3: Write minimal implementation**

Create a normalization adapter that converts parser output into canonical events. Keep provider-specific parsing logic local, but stop encoding UI visibility policy in parser branches.

For Claude specifically:
- stop treating `message_delta` and `system: status=...` as unrecoverable noise
- map them to normalized lifecycle/progress events, even if the renderer later chooses compact formatting

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsers.py tests/test_parser_normalization.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add ralph/agents/parsers/base.py ralph/agents/parsers/normalize.py ralph/agents/parsers/claude.py ralph/agents/parsers/codex.py ralph/agents/parsers/opencode.py ralph/agents/parsers/gemini.py ralph/agents/parsers/generic.py tests/test_parsers.py tests/test_parser_normalization.py
git commit -m "refactor: normalize parser activity across providers"
```

---

## Task 3: Move visibility policy out of `runner.py`

**Files:**
- Create: `ralph/display/activity_presenter.py`
- Modify: `ralph/pipeline/runner.py`
- Test: `tests/test_pipeline_runner.py`
- Test: `tests/test_activity_presenter.py`

**Step 1: Write the failing tests**

Add tests asserting:
- the presenter decides line-formatting from canonical events
- startup/lifecycle/progress events have compact but non-empty visible output
- no provider-specific parser assumptions remain inside `_render_agent_activity_line`
- unknown events still produce a user-visible fallback summary

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_runner.py tests/test_activity_presenter.py -q`
Expected: FAIL because presenter module does not exist and runner still hardcodes rendering logic.

**Step 3: Write minimal implementation**

Extract the rendering decision tree from `runner.py` into a presenter module that consumes canonical activity events. Reduce `runner.py` to orchestration only.

The presenter should own:
- visible summary rules
- truncation policy
- fallback messages such as “agent starting”, “tool running”, “waiting for output”

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_runner.py tests/test_activity_presenter.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add ralph/display/activity_presenter.py ralph/pipeline/runner.py tests/test_pipeline_runner.py tests/test_activity_presenter.py
git commit -m "refactor: move activity presentation out of runner"
```

---

## Task 4: Replace ad hoc dashboard state with typed visibility state

**Files:**
- Create: `ralph/display/visibility_state.py`
- Modify: `ralph/display/parallel_display.py`
- Modify: `ralph/display/render_thread.py`
- Modify: `ralph/display/renderers/dashboard.py`
- Test: `tests/test_parallel_display.py`
- Test: `tests/test_render_thread.py`
- Test: `tests/test_dashboard_renderer.py`

**Step 1: Write the failing tests**

Add tests asserting:
- dashboard state has typed fields instead of `dict[str, list[str] | str]`
- each state change increments a visibility version
- `RenderThread` only redraws on version changes or explicit heartbeat intervals
- dashboard startup, waiting, tool-running, and no-output-yet states render non-empty snapshots
- live updates force visible redraws without depending on accidental `Console.print` behavior

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parallel_display.py tests/test_render_thread.py tests/test_dashboard_renderer.py -q`
Expected: FAIL because the typed visibility state and versioned refresh policy do not exist.

**Step 3: Write minimal implementation**

Introduce a typed `VisibilityState` object that tracks:
- unit/session id
- provider
- current status
- last visible message
- last tool activity
- dropped-count / buffered-count
- version number
- timestamps for freshness

Update `RenderThread` to consume visibility state snapshots and own refresh policy explicitly.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parallel_display.py tests/test_render_thread.py tests/test_dashboard_renderer.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add ralph/display/visibility_state.py ralph/display/parallel_display.py ralph/display/render_thread.py ralph/display/renderers/dashboard.py tests/test_parallel_display.py tests/test_render_thread.py tests/test_dashboard_renderer.py
git commit -m "refactor: add typed visibility state for dashboard rendering"
```

---

## Task 5: Fix the current brittle test around Rich live redraws

**Files:**
- Modify: `tests/test_runner_output_routing.py`
- Create: `tests/test_live_contract.py`

**Step 1: Write the failing tests**

Add tests asserting:
- Ralph application code does not call bare `Console.print` for user lines during live sessions
- Rich internal control redraw frames are allowed and do not count as Ralph regressions
- the real contract is “visible updates occur” rather than “Rich never prints control frames”

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner_output_routing.py tests/test_live_contract.py -q`
Expected: FAIL because current tests forbid all console prints during live mode.

**Step 3: Write minimal implementation**

Replace the over-broad guard with a scoped test double that distinguishes:
- Ralph-emitted user messages
- Rich internal `Control` redraw operations

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner_output_routing.py tests/test_live_contract.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_runner_output_routing.py tests/test_live_contract.py
git commit -m "test: align live output assertions with Rich redraw contract"
```

---

## Task 6: Make workspace/discovery activity bounded and visible

**Files:**
- Modify: `ralph/mcp/tool_workspace.py`
- Modify: `ralph/display/activity_presenter.py`
- Test: `tests/test_tool_workspace.py`
- Test: `tests/test_mcp_server.py`
- Test: `tests/test_prompts.py`

**Step 1: Write the failing tests**

Add tests asserting:
- recursive listing and search skip heavyweight/cache/nested-repo paths
- long-running workspace calls emit progress-friendly normalized activity where applicable
- `directory_tree` remains available as the compatibility alias

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_workspace.py tests/test_mcp_server.py tests/test_prompts.py -q`
Expected: FAIL if progress activity or bounded traversal expectations are not yet expressed.

**Step 3: Write minimal implementation**

Keep the existing traversal pruning, but make long-running discovery visible via the new activity model rather than silent waiting.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_workspace.py tests/test_mcp_server.py tests/test_prompts.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add ralph/mcp/tool_workspace.py ralph/display/activity_presenter.py tests/test_tool_workspace.py tests/test_mcp_server.py tests/test_prompts.py
git commit -m "feat: surface bounded workspace discovery activity"
```

---

## Task 7: Add end-to-end visibility contract tests

**Files:**
- Create: `tests/integration/test_agent_visibility_contract.py`
- Create: `tests/testing/fake_live_sink.py`
- Modify: `tests/test_runner_output_routing.py`

**Step 1: Write the failing tests**

Create integration tests that simulate each provider stream and assert:
- a visible startup state appears quickly
- text/tool/progress transitions become visible
- dashboard mode and lines mode preserve the same semantic events
- unknown providers degrade gracefully through generic normalization
- new agent adapters can pass by implementing only the canonical activity contract

**Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_agent_visibility_contract.py -q`
Expected: FAIL because fake live sink and canonical activity contract do not exist yet.

**Step 3: Write minimal implementation**

Use fake parser streams and a fake live sink instead of real Rich terminal assertions. Test visibility invariants, not ANSI/control-sequence details.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_agent_visibility_contract.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/integration/test_agent_visibility_contract.py tests/testing/fake_live_sink.py tests/test_runner_output_routing.py
git commit -m "test: add end-to-end agent visibility contract coverage"
```

---

## Task 8: Document the contract for future agent integrations

**Files:**
- Create: `docs/agent-visibility-contract.md`
- Modify: `ralph/agents/parsers/__init__.py`
- Modify: `ralph/config/models.py`
- Test: `tests/test_parsers.py`

**Step 1: Write the failing tests**

Add tests asserting:
- parser registry errors clearly describe the required contract for new parser types
- new parser docs/reference examples stay aligned with the canonical model

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsers.py -q`
Expected: FAIL if contract messaging or examples are missing.

**Step 3: Write minimal implementation**

Document:
- required event kinds
- visibility hints
- fallback behavior
- tool/progress semantics
- test checklist for new agent providers

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsers.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add docs/agent-visibility-contract.md ralph/agents/parsers/__init__.py ralph/config/models.py tests/test_parsers.py
git commit -m "docs: define agent visibility contract for future providers"
```

---

## Final Verification

Run the full repository verification after all tasks are complete:

```bash
make verify
```

Then run the focused visibility regressions:

```bash
pytest tests/test_parsers.py tests/test_pipeline_runner.py tests/test_parallel_display.py tests/test_render_thread.py tests/test_runner_output_routing.py tests/test_tool_workspace.py tests/test_mcp_server.py tests/test_prompts.py tests/integration/test_agent_visibility_contract.py -q
```

Expected:
- no lint/type/test failures
- dashboard mode shows visible activity quickly
- lines mode preserves the same semantic progress
- parser-specific quirks no longer create silent invisibility
- future agents can integrate through one explicit contract

---

## Notes for Execution

- Do not bundle this into one giant patch. Execute task-by-task with TDD.
- Keep backward compatibility where possible while introducing the canonical model.
- If a task reveals a mismatch between Rich behavior and Ralph’s tests, fix the test to validate Ralph’s contract rather than Rich implementation details.
- Prefer deleting brittle assumptions over adding more ad hoc guards.

Plan complete and saved to `docs/plans/2026-04-18-agent-visibility-hardening.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
