# Nanocoder Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Nanocoder as a first-class built-in Ralph Workflow agent with shared transport handling, Ralph-managed MCP wiring, and black-box regression coverage.

**Architecture:** Extend the existing agent transport, registry, command-builder, and runtime-MCP seams instead of creating a Nanocoder-specific invocation path. Keep retries and completion transport-agnostic, and use env-based Nanocoder MCP injection so the integration remains black-box testable.

**Tech Stack:** Python 3.12, pytest, Pydantic models, Ralph agent registry/invoke runtime, MCP transport helpers, Markdown docs.

---

### Task 1: Add failing transport and registry tests

**Files:**
- Modify: `ralph-workflow/tests/test_agents_invoke_2.py`
- Modify: `ralph-workflow/tests/test_session_resume_single_source_of_truth.py`
- Modify: `ralph-workflow/tests/test_config_welcome.py`
- Modify: `ralph-workflow/tests/test_agents_registry.py` if present, otherwise add targeted assertions to an existing registry-focused test file

- [ ] **Step 1: Write failing tests for built-in Nanocoder registration and command shape**

Add assertions for:

```python
registry = AgentRegistry.from_config(config)
agent = registry.get("nanocoder")
assert agent is not None
assert agent.transport == AgentTransport.NANOCODER
assert build_command(
    agent,
    str(prompt_file),
    options=BuildCommandOptions(workspace_path=tmp_path),
)[:4] == ["nanocoder", "--mode", "auto-accept", "run"]
```

- [ ] **Step 2: Write failing tests proving Nanocoder has no built-in resume flag**

Add assertions for:

```python
args, new_sid = resolve_session_resume_flag(
    AgentTransport.NANOCODER,
    has_prior_session=True,
    prior_session_id="sid-nano",
    recovery_action="resume",
)
assert args == ["--session-id", "sid-nano"]
assert new_sid == "sid-nano"
```

and a second behavior-level assertion that the built-in `nanocoder` agent itself has `session_flag is None`.

- [ ] **Step 3: Run focused tests to verify they fail**

Run: `uv run pytest -q tests/test_agents_invoke_2.py tests/test_session_resume_single_source_of_truth.py tests/test_config_welcome.py`

Expected: FAIL because Nanocoder transport/agent support does not exist yet.

### Task 2: Add failing runtime MCP-wiring tests

**Files:**
- Modify: `ralph-workflow/tests/test_agents_invoke_2.py`
- Add or modify: `ralph-workflow/tests/test_mcp_transport_nanocoder.py`

- [ ] **Step 1: Write failing tests for Nanocoder runtime env synthesis**

Add assertions for:

```python
runtime = resolve_invocation_runtime(
    nanocoder_config,
    {MCP_ENDPOINT_ENV: "http://127.0.0.1:8123/mcp"},
    tmp_path,
)
assert runtime.agent_env is not None
payload = json.loads(runtime.agent_env["NANOCODER_MCPSERVERS"])
assert payload[0]["name"] == "ralph"
assert payload[0]["transport"] == "http"
assert payload[0]["url"] == "http://127.0.0.1:8123/mcp"
```

and a merge helper test that pre-existing Nanocoder MCP server config remains present alongside the injected Ralph entry.

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `uv run pytest -q tests/test_agents_invoke_2.py tests/test_mcp_transport_nanocoder.py`

Expected: FAIL because no Nanocoder transport helper exists yet.

### Task 3: Implement Nanocoder shared transport support

**Files:**
- Modify: `ralph-workflow/ralph/config/agent_transport.py`
- Modify: `ralph-workflow/ralph/config/agent_config.py`
- Modify: `ralph-workflow/ralph/agents/registry.py`
- Modify: `ralph-workflow/ralph/agents/invoke/_commands.py`
- Modify: `ralph-workflow/ralph/agents/invoke/__init__.py`
- Add: `ralph-workflow/ralph/mcp/transport/nanocoder.py`
- Modify: `ralph-workflow/ralph/mcp/transport/__init__.py`

- [ ] **Step 1: Add the transport enum and inference support**

Implement the minimal enum and inference changes so `cmd="nanocoder"` resolves to `AgentTransport.NANOCODER`.

- [ ] **Step 2: Add the built-in agent definition**

Implement a built-in `nanocoder` agent with:

```python
AgentConfig(
    cmd="nanocoder",
    can_commit=False,
    json_parser=JsonParserType.GENERIC,
    transport=AgentTransport.NANOCODER,
)
```

Leave `session_flag=None`.

- [ ] **Step 3: Implement the shared command-builder branch**

Build the argv through `_build_command()` using Nanocoder's documented run mode:

```python
["nanocoder", "--mode", "auto-accept", "run", prompt_text]
```

Append `--provider` / `--model` only if Ralph's existing `model_flag` surface can pass them through without inventing new config semantics; otherwise preserve the existing raw `model_flag.split()` behavior.

- [ ] **Step 4: Implement Nanocoder MCP env synthesis**

Add a transport helper that emits documented Nanocoder MCP config entries using env-driven injection. The helper should return JSON for `NANOCODER_MCPSERVERS` with a Ralph HTTP server entry and merged existing entries when present.

- [ ] **Step 5: Wire the new transport into `resolve_invocation_runtime()`**

Use the new helper in the shared runtime dispatcher so Nanocoder follows the same runtime-resolution path as other built-ins.

- [ ] **Step 6: Run focused tests to verify green**

Run: `uv run pytest -q tests/test_agents_invoke_2.py tests/test_session_resume_single_source_of_truth.py tests/test_mcp_transport_nanocoder.py tests/test_config_welcome.py`

Expected: PASS.

### Task 4: Add docs and config coverage

**Files:**
- Modify: `ralph-workflow/README.md`
- Modify: `ralph-workflow/docs/sphinx/agents.md`
- Modify: `ralph-workflow/docs/sphinx/configuration.md`
- Modify: `ralph-workflow/docs/sphinx/agent-compatibility.md`
- Modify: `ralph-workflow/docs/sphinx/troubleshooting.md`
- Modify: `ralph-workflow/ralph/config/welcome.py`
- Modify: `ralph-workflow/ralph/policy/defaults/ralph-workflow.toml`
- Modify: `ralph-workflow/ralph/policy/defaults/ralph-workflow-local.toml`
- Add or modify docs-sync tests as needed

- [ ] **Step 1: Add failing docs assertions first**

Add or extend tests so supported-agent enumerations and config examples mention Nanocoder where appropriate without changing default chains.

- [ ] **Step 2: Run docs-focused tests to verify they fail**

Run: `uv run pytest -q tests/test_config_welcome.py`

Expected: FAIL until docs/help text is updated.

- [ ] **Step 3: Update docs surfaces with Nanocoder as a supported opt-in built-in**

Document:

- supported agents now include Nanocoder
- Nanocoder is opt-in and not added to the default chain
- users can define `[agents.nanocoder]` or use the built-in name directly
- Ralph manages MCP wiring for the built-in transport during a run

- [ ] **Step 4: Run docs-focused tests to verify green**

Run: `uv run pytest -q tests/test_config_welcome.py`

Expected: PASS.

### Task 5: Final verification

**Files:**
- No additional code files expected

- [ ] **Step 1: Run the focused Nanocoder regression set**

Run: `uv run pytest -q tests/test_agents_invoke_2.py tests/test_session_resume_single_source_of_truth.py tests/test_mcp_transport_nanocoder.py tests/test_config_welcome.py`

Expected: PASS.

- [ ] **Step 2: Run canonical verification**

Run: `make verify`

Expected: all checks pass with no ERROR/WARNING diagnostics.

- [ ] **Step 3: Review changed docs against the documentation rubric**

Confirm the changed public-doc surfaces still have clear roles, accurate supported-agent framing, and no default-chain drift.
