# Shared Run-Scoped MCP Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace per-attempt MCP bridge churn with a stable run-scoped MCP server that stays alive for the entire Ralph run and applies per-session/per-agent policy inside the server.

**Architecture:** Ralph should own one long-lived MCP transport per run, not one transport per agent attempt. Agent sessions remain real and capability-scoped, but session identity and policy move into request routing / server-side enforcement instead of being encoded by creating and destroying sockets on every retry or fallback. The rewrite target must be a transport abstraction (stable endpoint), not a hardcoded `unix://` design. The default cross-platform shape should be a localhost server bound to a random port at run start, with that endpoint passed dynamically to agents; Unix sockets can remain an implementation detail on Unix only if they sit behind the same stable-endpoint abstraction.

**Tech Stack:** Rust, `ralph-workflow`, `mcp-server`, Unix socket / localhost transport, existing `AgentSession`, existing capability/tool-filter infrastructure.

---

## Problem Statement

The current architecture couples transport lifetime to agent-attempt lifetime:

- commit plumbing starts a fresh `SessionBridge` for each fallback attempt
- reducer invocation starts a fresh `SessionBridge` for each agent invocation
- provider startup and MCP connection happen on provider-controlled timing
- retries/fallbacks destroy and recreate the endpoint while providers are still starting up

This creates split-brain and timing races across:

1. prompt-visible tool names
2. harness/provider config
3. live MCP transport readiness
4. provider-visible tool catalog
5. artifact ingestion / fallback behavior

The runtime evidence now proves:

- prompt text can be correct
- socket path can exist
- provider can still mark `ralph` MCP as failed
- `ralph --mcp-proxy` can fail with `Connection refused`
- fallback churn hides the real defect

So the actual architecture target is:

- one stable MCP server per Ralph run
- per-session/per-agent capability enforcement inside the server
- no transport teardown until the run really ends

---

### Task 1: Rewrite the postmortem to describe the real failure

**Files:**
- Modify: `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md`

**Step 1: Replace the false resolution claim**
- Mark the old “implemented/fixed” framing as incorrect.
- State clearly that the real root cause was not just manifest drift.

**Step 2: Document the true architecture failure**
- Explain that transport lifetime was wrongly coupled to attempt lifetime.
- Explain that provider-truth validation never existed at the actual boundary.

**Step 3: Add live evidence**
- Claude init showed `ralph` MCP server status `failed`.
- Live probe showed socket path could exist while Claude still exposed zero Ralph tools.
- `ralph --mcp-proxy` showed `Connection refused`.

**Step 4: Add the corrected target architecture**
- Stable run-scoped MCP server.
- Session-aware routing/policy inside the server.
- Same-agent resubmission before fallback.

---

### Task 2: Add a run-scoped MCP server manager abstraction

**Files:**
- Create: `ralph-workflow/src/mcp_server/run_scoped.rs`
- Modify: `ralph-workflow/src/mcp_server/mod.rs`
- Test: `ralph-workflow/src/mcp_server/run_scoped.rs`

**Step 1: Write the failing test**
- Add a unit test proving a run-scoped manager can create one stable endpoint and reuse it across multiple session registrations.

**Step 2: Run test to verify it fails**
- Run targeted test with `cargo xtask test -p ralph-workflow --lib -- <test_name>`.

**Step 3: Implement minimal manager**
- Add a run-scoped manager that owns one stable transport/server lifecycle.
- Add registration API for `AgentSession` metadata.
- Make the endpoint abstraction transport-neutral: Unix socket on Unix, TCP/named-pipe strategy on Windows.
- Keep implementation narrow at first: stable server + session registry.

**Step 4: Re-run targeted tests**
- Ensure the new manager works without touching provider integration yet.

---

### Task 3: Move session enforcement from transport creation to request/session routing

**Files:**
- Modify: `ralph-workflow/src/mcp_server/session_bridge.rs`
- Modify: `ralph-workflow/src/mcp_server/tool_bridge.rs`
- Modify: `mcp-server/src/io/session_bridge.rs`
- Modify: `mcp-server/src/io/mod.rs`
- Modify: `mcp-server/src/dispatch/access.rs`

**Step 1: Write the failing test**
- Add a test proving two sessions with different capabilities can share one server while still seeing different effective tool permissions.

**Step 2: Verify red**
- Run the targeted test and confirm current code cannot satisfy it.

**Step 3: Implement request-scoped session lookup**
- Route incoming requests through a session identity instead of assuming server instance == session.
- Preserve audit correlation per session.

**Step 4: Verify green**
- Run session-isolation tests and MCP behavioral tests.

---

### Task 4: Refactor commit plumbing to reuse stable MCP transport

**Files:**
- Modify: `ralph-workflow/src/phases/commit/runner/chain.rs`
- Modify: `ralph-workflow/src/phases/commit/runner/io.rs`
- Test: `ralph-workflow/src/phases/commit/runner/chain.rs`

**Step 1: Write the failing test**
- Add a regression proving commit fallback/retry uses one shared MCP server lifetime for the whole chain, not a fresh bridge per attempt.

**Step 2: Verify red**
- Run targeted test and confirm current code starts separate bridges.

**Step 3: Implement shared commit-run MCP context**
- Start one stable MCP server for the commit run.
- Reuse it across same-agent retry and fallback agents.

**Step 4: Verify green**
- Run commit runner targeted tests.

---

### Task 5: Refactor reducer agent invocation to use run-scoped MCP transport

**Files:**
- Modify: `ralph-workflow/src/reducer/boundary/agent.rs`
- Modify: `ralph-workflow/src/phases/context.rs`
- Modify: any state/context owner that must hold the run-scoped server lifetime
- Test: `ralph-workflow/src/reducer/boundary/tests/...`

**Step 1: Write the failing test**
- Add a test proving two sequential reducer agent invocations in one run reuse stable MCP transport while keeping session-scoped policy distinct.

**Step 2: Verify red**
- Run targeted test and confirm per-invocation bridge churn still exists.

**Step 3: Implement minimal lifetime ownership**
- Store run-scoped MCP server in run/phase/execution context.
- Register agent sessions against it instead of spinning up a new bridge.

**Step 4: Verify green**
- Run reducer boundary tests.

---

### Task 6: Make fallback behavior submission-aware before agent switching

**Files:**
- Modify: `ralph-workflow/src/phases/commit/runner/chain.rs`
- Modify: any shared retry prompt utility if extracted
- Test: `ralph-workflow/src/phases/commit/tests.rs`

**Step 1: Write the failing test**
- Assert that when an agent produces plain-text commit output without artifact submission, Ralph issues a same-agent resubmission prompt before switching models.

**Step 2: Verify red**
- Run targeted test.

**Step 3: Implement minimal retry behavior**
- Reuse existing retry infrastructure/prompt patterns.
- Keep fallback only after resubmission attempt fails.

**Step 4: Verify green**
- Re-run targeted tests.

---

### Task 7: Add provider-truth smoke tests (the missing contract)

**Files:**
- Create/modify: integration tests under `tests/integration_tests/`
- Possibly add harness-focused tests under `ralph-workflow/src/agents/harness/`

**Step 1: Write the failing test**
- Add a smoke test seam for: provider config generated → provider-facing init state observed → expected Ralph MCP tool visible.

**Step 2: Verify red**
- Confirm current architecture fails this test.

**Step 3: Implement support hooks**
- Add deterministic probe/debug hooks if necessary.
- Keep them narrow and test-oriented.

**Step 4: Verify green**
- Run targeted provider-boundary tests.

---

### Task 8: Full verification

**Files:**
- No new files; verification only.

**Step 1: Run targeted tests**
- `cargo xtask test -p ralph-workflow --lib`
- relevant integration tests for MCP + commit

**Step 2: Run full verification**
- `cargo xtask verify`

**Step 3: Fix surfaced failures immediately**
- No deferrals.

**Step 4: Re-run verification**
- Require clean pass before claiming completion.
