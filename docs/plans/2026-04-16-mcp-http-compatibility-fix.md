# MCP HTTP Compatibility Fix Implementation Plan

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Ralph's localhost MCP server reliably register with Claude-family HTTP MCP clients so deferred Ralph tools become available on the first turn.

**Architecture:** Replace the opaque FastMCP runtime dependency for Ralph's standalone localhost server with a Ralph-owned, deterministic HTTP MCP server implementation. Keep transport behavior explicit and dependency-injected so handshake semantics, headers, methods, and session state are directly testable.

**Tech Stack:** Python 3.12+, http.server-based standalone server, existing Ralph MCP bridge/tool registry, pytest, httpx.

---

### Task 1: Lock down the failing compatibility behavior with tests

**Files:**
- Modify: `ralph-workflow/tests/test_mcp_server.py`
- Modify: `ralph-workflow/tests/test_mcp_startup.py`

**Steps:**
1. Add a regression test asserting `GET /mcp` does not return a JSON-RPC `Missing session ID` error for the standalone server.
2. Add a regression test asserting initialize returns the exact session/header semantics Ralph expects to expose.
3. Add a regression test for `notifications/initialized` returning 202/no body in the standalone HTTP flow.
4. Run only the new MCP server tests and confirm they fail before implementation.

### Task 2: Promote the Ralph-owned HTTP MCP server path

**Files:**
- Modify: `ralph-workflow/ralph/mcp/server/runtime.py`
- Modify: `ralph-workflow/ralph/mcp/server/lifecycle.py` (only if startup wiring needs adjustment)

**Steps:**
1. Refactor the fallback HTTP server into the primary standalone runtime path.
2. Keep request parsing, method dispatch, and response serialization in small injectable functions/classes.
3. Implement explicit handlers for:
   - `initialize`
   - `notifications/initialized`
   - `tools/list`
   - `tools/call`
   - `prompts/list`
   - `resources/list`
   - `resources/templates/list`
4. Make GET behavior explicit and Claude-compatible instead of leaking `Missing session ID` JSON-RPC errors.
5. Preserve current tool registry behavior and session capability filtering.

### Task 3: Keep the server transport testable

**Files:**
- Modify: `ralph-workflow/ralph/mcp/server/runtime.py`
- Modify: `ralph-workflow/tests/test_mcp_server.py`

**Steps:**
1. Extract pure response builders for each MCP method.
2. Inject session/state dependencies rather than hard-coding transport behavior.
3. Add tests covering method dispatch and response payloads without needing a subprocess.
4. Add one end-to-end local HTTP test covering the full Claude-style handshake sequence.

### Task 4: Verify startup and preflight still work

**Files:**
- Modify: `ralph-workflow/tests/test_mcp_startup.py`
- Modify: `ralph-workflow/tests/test_mcp_server.py`

**Steps:**
1. Verify `start_mcp_server()` preflight still succeeds against the new standalone runtime.
2. Verify tool discovery still includes `ralph_submit_artifact` for commit-capable sessions.
3. Verify restricted sessions still filter write/exec tools correctly.

### Task 5: Run verification

**Files:**
- No new files

**Steps:**
1. Run targeted tests for MCP runtime/startup.
2. Run commit-generation tests that depend on MCP availability.
3. Run `make verify` from `ralph-workflow`.
4. Collect final Oracle review on the transport fix.
