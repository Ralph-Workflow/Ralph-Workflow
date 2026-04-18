# Ralph MCP Upstream Proxy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Ralph the only MCP server visible to provider CLIs by proxying user-configured upstream MCP servers behind Ralph, so Ralph remains the single policy authority for tool access.

**Architecture:** Ralph already owns a run-scoped MCP server and server-side capability checks. Extend that server with an upstream MCP client/proxy layer that discovers configured user MCP servers, aggregates and namespaces their tool catalogs, enforces Ralph policy before dispatch, and forwards approved tool calls to upstream backends. Apply the same provider-facing contract to every supported transport in the main implementation: tools-only proxying, fail-closed startup, explicit namespacing, and Ralph-only MCP visibility for Claude, OpenCode, and Codex.

**Tech Stack:** Python 3.12+, Ralph MCP server runtime, JSON-RPC/MCP over HTTP and stdio, pytest, mypy, ruff.

---

## Design Constraints

- **Ralph is the only provider-visible MCP endpoint in strict mode.** Claude/OpenCode/Codex must not receive user MCP servers directly when strict Ralph authority is enabled.
- **Proxy only `tools` in v1.** Do not proxy `resources` or `prompts` yet.
- **Explicit namespacing is mandatory.** Upstream tools must be exposed under Ralph-owned names to avoid collisions.
- **Fail closed.** If an upstream server cannot be initialized at startup, Ralph must not advertise its tools.
- **Backend auth is not replaced.** Ralph may deny more; it cannot erase upstream auth requirements.
- **All supported agent transports are in scope for the main rollout.** Build transport-neutral proxy machinery first, then update Claude, OpenCode, and Codex in the same implementation wave.

Recommended public naming rule for proxied tools in v1:

```text
ralph_upstream__<server_name>__<tool_name>
```

Examples:
- `ralph_upstream__filesystem__read_file`
- `ralph_upstream__github__search_repos`

---

## Task 1: Document the contract before code

**Files:**
- Modify: `ralph-python/docs/mcp-tool-restriction.md`
- Modify: `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md`
- Create: `docs/architecture/mcp-upstream-proxy.md`

**Step 1: Write the failing docs expectation as a test note in the plan**

Document the contract to implement:

- strict Ralph mode exposes only Ralph to providers
- user MCP servers are preserved by being proxied behind Ralph, not passed through directly
- proxied tools are namespaced
- only `tools` are proxied in v1

**Step 2: Update the provider restriction docs draft**

Add wording like:

```md
In strict Ralph authority mode, provider CLIs receive only the Ralph MCP endpoint. User-configured upstream MCP servers are loaded by Ralph itself and re-exposed as Ralph-owned proxied tool aliases. Provider-side MCP permissions must not be relied on for these proxied tools. This contract applies consistently to Claude, OpenCode, and Codex integrations.
```

**Step 3: Add architecture doc skeleton**

Include sections for:
- startup/discovery
- upstream client lifecycle
- namespacing and collision handling
- policy enforcement order
- failure behavior
- provider integration per transport

**Step 4: Commit**

```bash
git add ralph-python/docs/mcp-tool-restriction.md docs/RFC/RFC-011-mcp-tool-availability-postmortem.md docs/architecture/mcp-upstream-proxy.md
git commit -m "docs(mcp): define Ralph upstream proxy contract"
```

---

## Task 2: Add a normalized upstream MCP config model

**Files:**
- Create: `ralph-python/ralph/mcp/upstream_config.py`
- Modify: `ralph-python/ralph/agents/invoke.py`
- Test: `ralph-python/tests/test_agents_invoke.py`
- Test: `ralph-python/tests/test_mcp_startup.py`

**Step 1: Write the failing tests**

Add tests for extracting supported upstream server definitions from existing config sources without exposing them directly to provider CLIs:

```python
def test_claude_mode_extracts_upstream_servers_without_passing_them_through(tmp_path: Path) -> None:
    ...


def test_opencode_mode_extracts_upstream_servers_without_passing_them_through(tmp_path: Path) -> None:
    ...


def test_codex_mode_extracts_upstream_servers_without_passing_them_through(tmp_path: Path) -> None:
    ...


def test_upstream_config_normalizes_url_only_http_servers() -> None:
    ...


def test_upstream_config_rejects_duplicate_ralph_server_name() -> None:
    ...
```

**Step 2: Run the targeted tests to verify failure**

Run:

```bash
cd ralph-python
pytest tests/test_agents_invoke.py -q -k "claude or opencode or codex or upstream"
pytest tests/test_mcp_startup.py -q -k "upstream"
```

Expected: FAIL because no upstream config extraction model exists yet.

**Step 3: Write the minimal config model**

Create dataclasses like:

```python
@dataclass(frozen=True)
class UpstreamMcpServer:
    name: str
    transport: Literal["http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
```

And a loader that:
- reads supported Claude/OpenCode/Codex config sources deterministically
- keeps only non-Ralph upstream servers
- normalizes URL-only HTTP entries
- returns a transport-neutral list for Ralph runtime use

**Step 4: Keep every provider config strict**

Refactor `invoke.py` so each supported transport builds a provider-visible config containing only Ralph while passing upstream config to Ralph runtime separately:
- Claude: `--mcp-config` containing only Ralph
- OpenCode: provider-visible MCP/config content containing only Ralph
- Codex: provider-visible MCP/config content containing only Ralph
- sidecar/env/session payload carrying upstream config for Ralph runtime

**Step 5: Re-run targeted tests**

Run the same pytest commands and expect PASS.

**Step 6: Commit**

```bash
git add ralph/agents/invoke.py ralph/mcp/upstream_config.py tests/test_agents_invoke.py tests/test_mcp_startup.py
git commit -m "feat(mcp): extract upstream server config for Ralph proxying"
```

---

## Task 3: Add upstream MCP client abstractions (tools-only)

**Files:**
- Create: `ralph-python/ralph/mcp/upstream_client.py`
- Create: `ralph-python/ralph/mcp/upstream_models.py`
- Test: `ralph-python/tests/test_mcp_transport.py`
- Test: `ralph-python/tests/test_mcp_server.py`

**Step 1: Write the failing tests**

Add tests for two core behaviors:

```python
def test_http_upstream_client_lists_tools() -> None:
    ...


def test_http_upstream_client_calls_tool() -> None:
    ...


def test_stdio_upstream_client_lists_tools() -> None:
    ...
```

**Step 2: Verify red**

Run:

```bash
pytest tests/test_mcp_transport.py -q -k "upstream"
pytest tests/test_mcp_server.py -q -k "upstream"
```

Expected: FAIL because no upstream client exists.

**Step 3: Implement a narrow tools-only client API**

Create a small interface:

```python
class UpstreamMcpClient(Protocol):
    def list_tools(self) -> list[UpstreamTool]: ...
    def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...
```

Support only:
- HTTP MCP
- stdio MCP

Do not implement prompts/resources in v1.

**Step 4: Preserve raw upstream errors**

Forward backend tool-call errors with server name context, e.g.:

```python
raise UpstreamCallError(f"upstream server 'filesystem' tool 'read_file' failed: {message}")
```

**Step 5: Re-run targeted tests**

Run the two pytest commands and expect PASS.

**Step 6: Commit**

```bash
git add ralph/mcp/upstream_client.py ralph/mcp/upstream_models.py tests/test_mcp_transport.py tests/test_mcp_server.py
git commit -m "feat(mcp): add upstream MCP tool clients"
```

---

## Task 4: Build an upstream registry with namespacing and collision detection

**Files:**
- Create: `ralph-python/ralph/mcp/upstream_registry.py`
- Modify: `ralph-python/ralph/mcp/tool_names.py`
- Test: `ralph-python/tests/test_mcp_bridge.py`
- Test: `ralph-python/tests/test_mcp_server.py`

**Step 1: Write the failing tests**

Add tests for:

```python
def test_upstream_registry_namespaces_tools_by_server() -> None:
    ...


def test_upstream_registry_rejects_colliding_proxy_aliases() -> None:
    ...


def test_upstream_registry_skips_unhealthy_server() -> None:
    ...
```

**Step 2: Verify red**

Run:

```bash
pytest tests/test_mcp_bridge.py -q -k "upstream"
pytest tests/test_mcp_server.py -q -k "namespace or upstream"
```

Expected: FAIL.

**Step 3: Implement registry model**

Create a registry that:
- accepts configured upstream servers
- initializes each client
- fetches its tools at startup
- maps each tool to a Ralph-owned alias
- rejects collisions deterministically
- excludes unhealthy upstreams from the advertised catalog

Add helper(s) in `tool_names.py`, e.g.:

```python
def upstream_proxy_tool_name(server_name: str, tool_name: str) -> str:
    return f"ralph_upstream__{server_name}__{tool_name}"
```

**Step 4: Re-run targeted tests**

Expect PASS.

**Step 5: Commit**

```bash
git add ralph/mcp/upstream_registry.py ralph/mcp/tool_names.py tests/test_mcp_bridge.py tests/test_mcp_server.py
git commit -m "feat(mcp): namespace proxied upstream tools"
```

---

## Task 5: Extend Ralph MCP server runtime to expose proxied upstream tools

**Files:**
- Modify: `ralph-python/ralph/mcp/tool_bridge.py`
- Modify: `ralph-python/ralph/mcp/server/runtime.py`
- Modify: `ralph-python/ralph/mcp/server/lifecycle.py`
- Modify: `ralph-python/ralph/mcp/startup.py`
- Test: `ralph-python/tests/test_mcp_server.py`
- Test: `ralph-python/tests/test_mcp_lifecycle.py`

**Step 1: Write the failing tests**

Add tests proving the runtime advertises both Ralph-native and proxied upstream tools when upstreams are healthy:

```python
def test_build_fastmcp_server_lists_proxied_upstream_tools(tmp_path: Path) -> None:
    ...


def test_proxied_upstream_tool_call_is_forwarded_after_policy_check(tmp_path: Path) -> None:
    ...
```

**Step 2: Verify red**

Run:

```bash
pytest tests/test_mcp_server.py -q -k "proxied or upstream"
pytest tests/test_mcp_lifecycle.py -q -k "upstream"
```

Expected: FAIL.

**Step 3: Implement minimal runtime composition**

Extend the runtime build path so `build_ralph_tool_registry(...)` can accept optional upstream registry input and register lazy proxied handlers that:
1. map Ralph alias → upstream server/tool
2. run the normal Ralph capability check first
3. call the upstream client only if allowed

Pseudo-shape:

```python
bridge = build_ralph_tool_registry(session, workspace)
for proxy_tool in upstream_registry.tool_definitions():
    bridge.register(proxy_tool.metadata, proxy_tool.handler)
```

**Step 4: Keep fail-closed startup**

If an upstream server cannot initialize or list tools, log it and omit it from the visible tool list. Do not advertise a broken alias.

**Step 5: Re-run targeted tests**

Expect PASS.

**Step 6: Commit**

```bash
git add ralph/mcp/tool_bridge.py ralph/mcp/server/runtime.py ralph/mcp/server/lifecycle.py ralph/mcp/startup.py tests/test_mcp_server.py tests/test_mcp_lifecycle.py
git commit -m "feat(mcp): expose upstream tools through Ralph server"
```

---

## Task 6: Enforce Ralph policy over proxied tools

**Files:**
- Modify: `ralph-python/ralph/mcp/capability_mapping.py`
- Modify: `ralph-python/ralph/mcp/session.py`
- Modify: `ralph-python/ralph/mcp/tool_bridge.py`
- Test: `ralph-python/tests/test_mcp_policy_outcomes.py`
- Test: `ralph-python/tests/test_mcp_server.py`

**Step 1: Write the failing tests**

Define the v1 policy rule clearly:
- proxied upstream tools require an explicit capability such as `UpstreamToolUse`
- optional future refinement may allow per-server or per-tool policy, but not in v1

Add tests like:

```python
def test_session_without_upstream_capability_cannot_use_proxied_tool() -> None:
    ...


def test_session_with_upstream_capability_can_use_proxied_tool() -> None:
    ...
```

**Step 2: Verify red**

Run:

```bash
pytest tests/test_mcp_policy_outcomes.py -q -k "upstream"
pytest tests/test_mcp_server.py -q -k "policy and upstream"
```

Expected: FAIL.

**Step 3: Implement minimal capability rule**

Add one narrow capability first:

```python
Capability.UPSTREAM_TOOL_USE
```

Use that in proxied tool metadata so Ralph remains the enforcement point.

**Step 4: Re-run targeted tests**

Expect PASS.

**Step 5: Commit**

```bash
git add ralph/mcp/capability_mapping.py ralph/mcp/session.py ralph/mcp/tool_bridge.py tests/test_mcp_policy_outcomes.py tests/test_mcp_server.py
git commit -m "feat(mcp): gate proxied tools with Ralph capability policy"
```

---

## Task 7: Apply Ralph-only MCP visibility across all provider transports

**Files:**
- Modify: `ralph-python/ralph/agents/invoke.py`
- Modify: `ralph-python/ralph/agents/registry.py`
- Modify: `ralph-python/tests/test_agents_invoke.py`
- Modify: `ralph-python/docs/mcp-tool-restriction.md`

**Step 1: Write the failing tests**

Add tests proving every supported transport receives only Ralph as the provider-visible MCP server while upstream server definitions are passed to Ralph separately:

```python
def test_claude_strict_mode_only_exposes_ralph_server() -> None:
    ...


def test_opencode_strict_mode_only_exposes_ralph_server() -> None:
    ...


def test_codex_strict_mode_only_exposes_ralph_server() -> None:
    ...


def test_provider_strict_mode_passes_upstream_proxy_payload_to_ralph() -> None:
    ...
```

**Step 2: Verify red**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "claude or opencode or codex or upstream"
```

Expected: FAIL.

**Step 3: Implement transport-specific strict provider policy in one wave**

For every supported transport:
- keep the provider-visible MCP/server config Ralph-only in strict mode
- stop preserving user MCP servers directly in provider config in strict mode
- preserve unrelated non-MCP user settings where possible
- pass serialized upstream config to Ralph runtime via env, sidecar, or session file using the normalized model from Task 2

Do not leave Claude updated while OpenCode/Codex still expose user MCP servers directly.

**Step 4: Re-run targeted tests**

Expect PASS.

**Step 5: Commit**

```bash
git add ralph/agents/invoke.py ralph/agents/registry.py tests/test_agents_invoke.py ralph-python/docs/mcp-tool-restriction.md
git commit -m "fix(agents): route user MCP through Ralph across transports"
```

---

## Task 8: Fix the surfaced commit prompt path bug before rollout

**Files:**
- Modify: `ralph-python/ralph/cli/commands/commit.py`
- Modify: `ralph-python/ralph/prompts/system_prompt.py` (only if needed)
- Test: `ralph-python/tests/test_cli_commands.py`

**Step 1: Write the failing test**

Add a regression proving commit-mode invocation materializes the file referenced by the appended system prompt:

```python
def test_commit_agent_attempt_persists_current_prompt_before_append_system_prompt(tmp_path: Path) -> None:
    ...
```

**Step 2: Verify red**

Run:

```bash
pytest tests/test_cli_commands.py -q -k "current_prompt or commit"
```

Expected: FAIL.

**Step 3: Implement the minimal fix**

Before commit agent invocation, ensure `.agent/CURRENT_PROMPT.md` exists with the canonical prompt content or a safe fallback, matching the phase prompt materialization contract.

**Step 4: Re-run targeted test**

Expect PASS.

**Step 5: Commit**

```bash
git add ralph/cli/commands/commit.py tests/test_cli_commands.py
git commit -m "fix(commit): persist current prompt for Claude commit runs"
```

---

## Task 9: End-to-end verification and rollout safety

**Files:**
- Modify as needed from previous tasks only
- Verification only

**Step 1: Run focused MCP and invoke tests**

```bash
cd ralph-python
pytest tests/test_agents_invoke.py -q
pytest tests/test_mcp_server.py -q
pytest tests/test_mcp_lifecycle.py -q
pytest tests/test_mcp_policy_outcomes.py -q
pytest tests/test_mcp_startup.py -q
pytest tests/test_cli_commands.py -q -k "commit or current_prompt"
```

**Step 2: Run static verification**

```bash
ruff check ralph/ tests/
mypy ralph/ --strict
```

**Step 3: Run full package verification**

```bash
make verify
```

**Step 4: Manual smoke check against a real provider**

Verify one real run per supported provider integration with:
- provider-visible MCP server list containing only Ralph
- proxied upstream tools appearing under Ralph-owned aliases
- proxied tool call succeeding through Ralph
- blocked proxied tool call being denied by Ralph, not provider prompt flow

Minimum matrix:
- Claude
- OpenCode
- Codex

**Step 5: Commit any final follow-up fixes**

```bash
git add -A
git commit -m "test(mcp): verify Ralph upstream proxy contract"
```

---

## Rollout Notes

- **Recommended v1 scope:** all supported providers in one rollout, tools-only proxy, HTTP + stdio upstreams, fail-closed startup.
- **Do not include in v1:** resource/prompt proxying, live tool catalog refresh, automatic upstream process restart, backend auth delegation.
- **Success condition:** all supported provider CLIs only know about Ralph; proxied upstream tools remain usable through Ralph-owned aliases; Ralph policy is the first permission gate for every proxied tool call.

---

Plan complete and saved to `docs/plans/2026-04-17-ralph-mcp-upstream-proxy.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
