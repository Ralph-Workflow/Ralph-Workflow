# Legacy SSE Upstream Support Implementation Plan

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Support legacy MCP HTTP+SSE upstream servers end to end so a docs-mcp style `.agent/mcp.toml` entry using `url = "http://127.0.0.1:6280/sse"` works in Ralph.

**Architecture:** Add an explicit legacy SSE upstream transport path behind the existing `transport = "http"` user-facing config by detecting SSE-style endpoints and routing them through a dedicated upstream client/probe path. Keep the transport boundary black-box testable by exercising public validation and registry behavior against a fake SSE server fixture rather than testing private internals.

**Tech Stack:** Python 3.12, httpx, pytest, Ralph MCP upstream runtime

---

### Task 1: Lock the intended behavior with failing black-box tests

**Files:**
- Create or modify: `ralph-workflow/tests/fixtures/fake_sse_mcp.py`
- Modify: `ralph-workflow/tests/integration/test_validate_custom_mcp_http_e2e.py`
- Modify: `ralph-workflow/tests/mcp/test_custom_mcp_roundtrip.py`
- Modify: `ralph-workflow/tests/mcp/test_custom_mcp_out_of_the_box.py`

**Step 1: Write the failing fixture and integration test**
- Add a fake MCP server that exposes legacy SSE semantics:
  - `GET /sse` returns `text/event-stream`
  - emits an `endpoint` event containing a message POST URL
  - `POST` to the message URL accepts JSON-RPC and answers over SSE `message` events
- Add a runner-level test proving `_validate_custom_mcp_servers(tmp_path)` succeeds when `.agent/mcp.toml` points at `/sse`.

**Step 2: Run the targeted test to verify it fails for the right reason**
Run: `uv run pytest ralph-workflow/tests/integration/test_validate_custom_mcp_http_e2e.py -q`
Expected: failure mentioning POST/validation against the SSE URL.

**Step 3: Add round-trip coverage**
- Add black-box tests proving `UpstreamRegistry.build(...)` and `probe_agent_transports(...)` work with an upstream defined by `/sse`.
- Keep assertions on observable behavior only: successful registry tool exposure, successful transport probe, successful validation.

**Step 4: Run the targeted MCP tests to verify they fail**
Run: `uv run pytest ralph-workflow/tests/mcp/test_custom_mcp_roundtrip.py ralph-workflow/tests/mcp/test_custom_mcp_out_of_the_box.py -q`
Expected: failures limited to the new SSE cases.

### Task 2: Implement an architecturally sound legacy SSE transport path

**Files:**
- Modify: `ralph-workflow/ralph/mcp/upstream/client.py`
- Modify: `ralph-workflow/ralph/mcp/protocol/startup.py`
- Modify: `ralph-workflow/ralph/mcp/upstream/validation.py`
- Modify: `ralph-workflow/ralph/mcp/upstream/agent_probe.py`
- Modify if needed: `ralph-workflow/ralph/mcp/upstream/config.py`, `ralph-workflow/ralph/config/mcp_models.py`

**Step 1: Introduce a legacy SSE request/response helper**
- Build a small helper that:
  - opens the SSE endpoint with `GET`
  - reads the `endpoint` event
  - POSTs JSON-RPC requests to the advertised message URL
  - reads matching JSON-RPC responses from SSE `message` events
- Keep it transport-focused and reusable by both validation and upstream client code.

**Step 2: Route SSE URLs through that helper**
- Preserve existing streamable HTTP behavior for normal POST-capable MCP endpoints.
- Add a deterministic rule for legacy SSE handling, preferably based on endpoint shape and/or explicit handshake fallback.
- Avoid rewriting unrelated config semantics.

**Step 3: Reuse the same transport path in validation and runtime calls**
- Ensure startup validation and `HttpUpstreamClient` use the same protocol decision so validation success implies runtime success.
- Ensure agent probe uses the same handshake expectations.

**Step 4: Run targeted tests until green**
Run the focused integration/MCP test commands from Task 1 after each change.
Expected: all new SSE tests pass, existing HTTP `/mcp` tests remain green.

### Task 3: Update docs and examples

**Files:**
- Modify: `ralph-workflow/README.md`
- Modify: `ralph-workflow/ralph/policy/defaults/mcp.toml`
- Modify any current MCP troubleshooting docs if touched by behavior

**Step 1: Document the supported docs-mcp URL**
- Make it explicit that docs-mcp `/sse` endpoints are supported.
- Clarify the distinction between streamable HTTP endpoints and legacy SSE endpoints in plain language.

**Step 2: Keep examples aligned with real behavior**
- Update the bundled `mcp.toml` example so users do not copy a stale URL shape.
- Mention that Ralph now handles docs-mcp-style `/sse` endpoints end to end.

**Step 3: Run doc-sensitive checks if needed**
- If README/default examples are changed only, rely on `make verify` for final confirmation.

### Task 4: Full verification

**Files:**
- No new files beyond above

**Step 1: Run focused tests**
Run: `uv run pytest ralph-workflow/tests/integration/test_validate_custom_mcp_http_e2e.py ralph-workflow/tests/mcp/test_custom_mcp_roundtrip.py ralph-workflow/tests/mcp/test_custom_mcp_out_of_the_box.py -q`
Expected: PASS

**Step 2: Run diagnostics on touched Python files**
Run LSP diagnostics on the touched MCP files and fix any errors before broader verification.

**Step 3: Run repository-required verification**
Run from `ralph-workflow/`: `make verify`
Expected: all checks pass with no errors or warnings.

**Step 4: Summarize outcome**
- State which endpoints are now supported.
- State which tests prove the behavior end to end.
- Note any assumptions kept for backward compatibility.
