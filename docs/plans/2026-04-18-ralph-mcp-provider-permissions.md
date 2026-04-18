# Ralph MCP Provider Permissions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure supported provider agents get blanket approval for Ralph MCP tools only, while Ralph remains the single enforcement point for actual tool authorization.

**Architecture:** Keep provider-visible MCP configuration Ralph-only, but stop relying on provider prompt/tool heuristics for normal operation. For transports that support provider-side MCP allowlists or equivalent approval controls, explicitly allow the exact Ralph-exposed MCP tool names for the current runtime/session. Preserve Ralph’s internal `required_capability` checks in `ToolBridge` and handler-level `require_capability(...)` calls as the real policy boundary. Cover Claude directly and verify OpenCode, Codex, and config-inherited aliases continue to behave correctly under the same transport policy.

**Tech Stack:** Python 3.12+, Ralph agent invocation layer, Ralph MCP runtime/tool registry, pytest, mypy, ruff.

---

## Design Constraints

- **Ralph remains the only provider-visible MCP endpoint in strict mode.** Do not re-expose upstream servers directly.
- **Provider blanket approval must be Ralph-only.** Never grant provider-side approval beyond Ralph-owned MCP tool names.
- **Ralph runtime remains the true policy authority.** Do not weaken `required_capability` checks or session capability enforcement.
- **Transport behavior must stay explicit.** Claude, OpenCode, Codex, and aliases inheriting those transports must all be covered.
- **Prompt-path fixes stay in scope if still needed.** Provider permissions should reduce prompts, but prompt materialization/inlining bugs must still be fixed if they remain reproducible.
- **No commits during implementation.** Leave all work in the working tree.

---

## Task 1: Document the provider permission contract

**Files:**
- Modify: `ralph-workflow/docs/mcp-tool-restriction.md`
- Modify: `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md`
- Modify: `docs/architecture/mcp-upstream-proxy.md`

**Step 1: Write the failing docs note into the plan**

Capture the intended contract:
- provider-visible MCP stays Ralph-only
- provider-side blanket approval, where supported, is granted only to Ralph MCP tool names
- Ralph runtime capability checks remain authoritative
- this contract applies to Claude, OpenCode, Codex, and transport-inheriting aliases

**Step 2: Update docs**

Add wording like:

```md
Provider CLIs must see only the Ralph MCP server in strict mode. Where a provider exposes MCP tool approval controls, Ralph should auto-approve only Ralph-owned MCP tool names for that session/runtime. This does not bypass Ralph policy: ToolBridge metadata and session capabilities still gate execution after the provider forwards the call.
```

**Step 3: Verify docs stay consistent with current runtime**

Check wording against:
- `ralph-workflow/ralph/agents/invoke.py`
- `ralph-workflow/ralph/mcp/tool_bridge.py`
- `ralph-workflow/ralph/mcp/server/runtime.py`

---

## Task 2: Add transport-neutral helpers for Ralph tool names

**Files:**
- Modify: `ralph-workflow/ralph/mcp/tool_names.py`
- Modify: `ralph-workflow/ralph/mcp/tool_bridge.py`
- Test: `ralph-workflow/tests/test_mcp_bridge.py`
- Test: `ralph-workflow/tests/test_mcp_server.py`

**Step 1: Write failing tests**

Add tests proving Ralph can derive the exact provider-facing tool names for its runtime tool registry, including Claude namespaced names:

```python
def test_tool_bridge_reports_bare_ralph_tool_names() -> None:
    ...


def test_tool_bridge_reports_claude_ralph_tool_names() -> None:
    ...
```

**Step 2: Verify RED**

Run:

```bash
cd ralph-workflow
pytest tests/test_mcp_bridge.py -q -k "tool names or claude"
pytest tests/test_mcp_server.py -q -k "tool names or claude"
```

**Step 3: Implement minimal helper surface**

Expose helpers that can produce:
- bare Ralph tool names for transports that use bare names
- Claude `mcp__ralph__<tool>` names for Claude transport
- the exact current registry-derived tool set, not a stale handwritten list

Recommended shapes:

```python
def ralph_tool_names() -> tuple[str, ...]:
    ...


def provider_visible_ralph_tool_names(transport: AgentTransport | None) -> tuple[str, ...]:
    ...
```

**Step 4: Re-run tests**

Run the same pytest commands and expect PASS.

---

## Task 3: Teach provider command builders to auto-approve Ralph-only MCP tools

**Files:**
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Modify: `ralph-workflow/ralph/agents/registry.py` only if transport defaults need tightening
- Test: `ralph-workflow/tests/test_agents_invoke.py`
- Test: `ralph-workflow/tests/test_agent_registry.py` if defaults change

**Step 1: Write failing tests first**

Cover all supported transports/aliases:

```python
def test_claude_mcp_command_auto_approves_exact_ralph_tool_names(tmp_path: Path) -> None:
    ...


def test_claude_transport_alias_inherits_ralph_tool_allowlist(tmp_path: Path) -> None:
    ...


def test_opencode_transport_does_not_regress_when_ralph_only_runtime_is_wired(tmp_path: Path) -> None:
    ...


def test_codex_transport_does_not_regress_when_ralph_only_runtime_is_wired(tmp_path: Path) -> None:
    ...
```

If OpenCode or Codex have transport-specific approval knobs, assert the equivalent behavior there. If they do not, assert that no provider-side allowlist is needed and that prompt/command construction still passes inline content rather than file-path dependencies.

**Step 2: Verify RED**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "allowlist or allowedTools or claude or opencode or codex"
```

**Step 3: Implement the minimal provider-side fix**

For Claude:
- keep `--mcp-config` Ralph-only
- keep `--strict-mcp-config`
- keep built-in tools disabled if that is still required
- add provider-side approval for the exact Ralph MCP tool names currently exposed by runtime

Preferred direction:
- construct `--allowedTools` from the runtime-derived Ralph tool-name helper for Claude transport
- keep upstream provider tools excluded entirely

For OpenCode/Codex:
- if transport has an explicit approval surface, wire the exact Ralph tool set there too
- otherwise, leave provider config Ralph-only and verify no permission prompt regression in current prompt/command flow

For aliases:
- ensure behavior is based on `transport`, not command-name string matching

**Step 4: Re-run tests**

Run the same pytest selector plus any new registry tests.

---

## Task 4: Finish the commit-mode prompt-path fix under the new permission model

**Files:**
- Modify: `ralph-workflow/ralph/cli/commands/commit.py`
- Modify: `ralph-workflow/ralph/prompts/commit/__init__.py` if Claude-specific prompt generation must avoid oversized diff path references
- Modify: `ralph-workflow/ralph/agents/invoke.py` if inline prompt handling still needs transport-specific fixes
- Test: `ralph-workflow/tests/test_cli_commit_command.py`
- Test: `ralph-workflow/tests/test_system_prompt.py`
- Test: `ralph-workflow/tests/test_prompts_commit.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`

**Step 1: Add failing tests for both prompt-path failure modes**

Cover:
- `.agent/CURRENT_PROMPT.md` materialization
- commit prompt body being passed inline instead of as a path where the provider interprets it as content
- large commit diff prompts avoiding provider-required file reads for Claude commit mode, if still reproducible

**Step 2: Verify RED**

Run targeted pytest selectors for commit/system prompt/invoke tests.

**Step 3: Implement only the needed fixes**

Potential acceptable fixes:
- ensure system prompt file exists before provider invocation
- inline prompt content instead of passing prompt file paths for Claude strict MCP mode
- if large diff payload references still trigger provider-side file reads, make Claude commit-mode prompt generation inline-safe or provide an explicit provider-approved read path only for Ralph-owned tools

**Step 4: Re-run targeted tests**

Run:

```bash
pytest tests/test_cli_commit_command.py -q
pytest tests/test_system_prompt.py -q
pytest tests/test_prompts_commit.py -q
pytest tests/test_agents_invoke.py -q -k "commit or claude"
```

---

## Task 5: End-to-end verification and rollout safety across supported agents

**Files:**
- Modify: `.sisyphus/plans/2026-04-17-ralph-mcp-upstream-proxy.md`
- Optionally modify docs if verification reveals contract drift

**Step 1: Focused MCP/invoke verification**

Run:

```bash
pytest tests/test_agents_invoke.py -q
pytest tests/test_mcp_server.py -q
pytest tests/test_mcp_lifecycle.py -q
pytest tests/test_mcp_policy_outcomes.py -q
pytest tests/test_mcp_bridge.py -q
pytest tests/test_mcp_startup.py -q
pytest tests/test_cli_commit_command.py -q
pytest tests/test_prompts_commit.py -q
```

**Step 2: Static verification**

Run:

```bash
ruff check ralph/ tests/
mypy ralph/ --strict
```

**Step 3: Broad package verification**

Run:

```bash
make verify
```

**Step 4: Manual provider smoke matrix**

Run real smoke flows for:
- Claude commit-message generation with Ralph-only MCP config
- Claude normal MCP session with tool use that should be auto-approved provider-side but still policy-gated by Ralph
- OpenCode MCP flow
- Codex MCP flow

For each, verify:
- no direct upstream provider MCP exposure
- no unexpected provider permission prompts for Ralph-owned tools
- Ralph capability denials still occur when expected
- commit flow does not ask the user to paste prompt or diff files

**Step 5: Update the tracker**

Mark completed items in:
- `.sisyphus/plans/2026-04-17-ralph-mcp-upstream-proxy.md`

Leave Task 9 open only if the manual provider smoke matrix still has unresolved failures.
