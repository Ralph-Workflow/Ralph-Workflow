# MCP Config Hardening Implementation Plan

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make MCP/config behavior correct and explicit across Claude, OpenCode, and Codex so Ralph preserves intended custom MCP configuration where possible, enforces native-tool restrictions consistently, and documents the exact guarantees directly in code and docs.

**Architecture:** Keep transport-specific enforcement in `ralph/agents/invoke.py`, but make the policy explicit instead of implicit: shared helpers should define what gets preserved, what gets overridden, and why. Claude remains strict-mode driven, OpenCode remains config-payload driven, and Codex remains best-effort; the implementation must make those differences obvious in code comments, tests, and docs.

**Tech Stack:** Python 3.14, pytest, mypy, ruff, TOML/JSON config generation, Claude/OpenCode/Codex CLI integration.

---

### Task 1: Define the canonical MCP/config policy surface

**Files:**
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Modify: `ralph-workflow/docs/mcp-tool-restriction.md`
- Reference: `ralph-workflow/ralph/mcp/tool_names.py`

**Step 1: Write the failing documentation-focused test or assertion target**

Add or identify tests that make the intended transport contract concrete:

- Claude preserves supported user `mcpServers` entries.
- Claude intentionally overrides any user-defined `ralph` server entry.
- Claude does **not** silently imply that preserved custom servers are usable unless allowlist policy permits them.
- OpenCode preserves unrelated `mcp` and `tools` sections while disabling native tools.
- Codex preserves existing `config.toml` sections and appends Ralph-specific overrides.

At minimum, add test names covering these invariants in `tests/test_agents_invoke.py` before touching implementation.

**Step 2: Run test to verify current failures**

Run targeted tests as each invariant is added, for example:

```bash
pytest tests/test_agents_invoke.py -q -k "claude or opencode or codex"
```

Expected: at least the Claude allowlist/usability invariants fail before implementation changes.

**Step 3: Write minimal implementation comments and structure**

In `ralph/agents/invoke.py`, add top-level comments above the transport-specific helpers explaining:

- why Claude uses strict config synthesis,
- why OpenCode uses merge-by-override for `tools`,
- why Codex is best-effort only,
- which parts of user config are preserved vs intentionally ignored.

Comments must explain **intent**, not restate syntax.

**Step 4: Run tests and docs checks**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "claude or opencode or codex"
ruff check ralph/ tests/
```

Expected: tests pass and comments/doc changes remain lint-clean.

**Step 5: Commit**

```bash
git add ralph/agents/invoke.py docs/mcp-tool-restriction.md tests/test_agents_invoke.py
git commit -m "docs: define MCP transport policy invariants"
```

---

### Task 2: Finish Claude config preservation and resolve the allowlist gap

**Files:**
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Modify: `ralph-workflow/ralph/mcp/tool_names.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`
- Reference: `ralph-workflow/docs/mcp-tool-restriction.md`

**Step 1: Write the failing tests**

Add explicit tests for:

```python
def test_claude_mcp_config_preserves_home_claude_json_servers(): ...

def test_claude_mcp_config_preserves_workspace_claude_json_servers(): ...

def test_claude_mcp_config_workspace_overrides_home_for_same_server_name(): ...

def test_claude_mcp_config_overrides_user_ralph_server_definition(): ...

def test_claude_allowed_tools_includes_preserved_custom_server_tools_when_policy_permits(): ...
```

The last test is the critical one: it should fail under the current Ralph-only `--allowedTools` policy if preserved custom MCP tools are supposed to remain usable.

**Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "claude_mcp_config or allowed_tools"
```

Expected: config-source precedence and/or allowlist-usability tests fail before implementation.

**Step 3: Implement the minimal safe fix**

Refactor Claude-specific helpers so they expose a transport policy explicitly, for example:

```python
@dataclass(frozen=True)
class ClaudeMcpPolicy:
    config_payload: dict[str, object]
    allowed_tools: str
```

Implementation requirements:

1. Load supported user config sources in deterministic order.
2. Merge **only** `mcpServers`.
3. Force Ralph's server definition last so Ralph-owned wiring wins.
4. Decide the permanent allowlist policy explicitly:
   - **Option A (recommended if product intent is true preservation):** include preserved custom MCP tool aliases in `--allowedTools` using a declared/derived registry, then document the rule.
   - **Option B (if strict Ralph-only tooling is the actual product intent):** stop claiming custom MCPs are preserved for use; preserve them only in config if harmless, but document them as intentionally blocked.

Do **not** leave the current half-state where config says “preserved” but allowlist silently blocks them.

**Step 4: Add in-code knowledge comments**

Above `_claude_mcp_config*` helpers and `_claude_allowed_tools()`, add comments explaining:

- why unrelated Claude settings are intentionally excluded,
- why user `mcpServers` are merged,
- why `ralph` is forced last,
- how allowlist policy interacts with preserved custom servers.

**Step 5: Run tests to verify green**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "claude"
ruff check ralph/ tests/
mypy ralph/ --strict
```

Expected: all Claude invoke tests pass and the allowlist behavior matches the documented contract.

**Step 6: Commit**

```bash
git add ralph/agents/invoke.py ralph/mcp/tool_names.py tests/test_agents_invoke.py docs/mcp-tool-restriction.md
git commit -m "fix: make Claude MCP preservation and allowlist policy explicit"
```

---

### Task 3: Harden OpenCode behavior and make preservation rules explicit

**Files:**
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`
- Modify: `ralph-workflow/docs/mcp-tool-restriction.md`

**Step 1: Write the failing tests**

Add tests for:

```python
def test_opencode_config_preserves_existing_mcp_servers(): ...

def test_opencode_config_overrides_user_ralph_server_definition(): ...

def test_opencode_config_preserves_unrelated_permission_entries(): ...

def test_opencode_config_rejects_or_normalizes_non_dict_mcp_sections(): ...
```

**Step 2: Run tests to verify failure if needed**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "opencode"
```

Expected: any missing invariants fail before implementation.

**Step 3: Tighten implementation**

Keep the existing merge shape, but make it explicit in code:

- user `mcp` entries are preserved,
- Ralph-owned `mcp.ralph` always wins,
- user permission entries are preserved except where Ralph must force policy,
- native tool disable entries are enforced last.

If helper extraction improves readability, add a small shared merge helper rather than duplicating dict mutation inline.

**Step 4: Add intent comments**

Document why OpenCode is allowed to preserve arbitrary user MCP servers while still forcing native tools off.

**Step 5: Verify**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "opencode"
ruff check ralph/ tests/
mypy ralph/ --strict
```

Expected: OpenCode behavior is fully covered and documented.

**Step 6: Commit**

```bash
git add ralph/agents/invoke.py tests/test_agents_invoke.py docs/mcp-tool-restriction.md
git commit -m "test: lock down OpenCode MCP merge invariants"
```

---

### Task 4: Fortify Codex behavior and document its permanent limitations

**Files:**
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`
- Modify: `ralph-workflow/docs/mcp-tool-restriction.md`

**Step 1: Write the failing tests**

Add or keep tests for:

```python
def test_codex_config_toml_preserves_existing_mcp_servers(): ...

def test_codex_config_toml_overrides_existing_ralph_server_definition(): ...

def test_codex_config_toml_preserves_unrelated_top_level_sections(): ...
```

**Step 2: Run tests to verify failure if behavior drifts**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "codex"
```

Expected: existing or new tests catch any config overwrite regressions.

**Step 3: Clarify implementation comments**

In `_prepare_codex_home()`, add comments explaining:

- why `config.toml` is rebuilt from `base_config` instead of symlinked,
- which existing sections are preserved,
- why Ralph appends `[mcp_servers.ralph]` and feature overrides,
- why Codex cannot guarantee MCP-only enforcement.

**Step 4: Align docs to reality**

Update `docs/mcp-tool-restriction.md` so Codex’s section explicitly says:

- config preservation exists,
- feature reduction is best-effort,
- core editing primitives remain active,
- custom MCP preservation is not equivalent to safe policy enforcement.

**Step 5: Verify**

Run:

```bash
pytest tests/test_agents_invoke.py -q -k "codex"
ruff check ralph/ tests/
mypy ralph/ --strict
```

Expected: Codex behavior stays stable and its limitations are obvious.

**Step 6: Commit**

```bash
git add ralph/agents/invoke.py tests/test_agents_invoke.py docs/mcp-tool-restriction.md
git commit -m "docs: clarify Codex MCP preservation and limits"
```

---

### Task 5: Make tool-alias generation a first-class, documented policy module

**Files:**
- Modify: `ralph-workflow/ralph/mcp/tool_names.py`
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Test: `ralph-workflow/tests/test_mcp_policy_outcomes.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`

**Step 1: Write the failing tests**

Add tests that force the alias policy to be explicit:

```python
def test_claude_tool_name_supports_non_ralph_server_names(): ...

def test_allowed_tool_builder_documents_or_enforces_server_scope(): ...
```

**Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_mcp_policy_outcomes.py tests/test_agents_invoke.py -q -k "claude_tool or allowed_tool"
```

Expected: failure until the policy is encoded clearly.

**Step 3: Implement helper extraction**

If Claude needs to allow preserved custom server tools, create a small helper that converts an explicit server/tool policy into `--allowedTools`. If Claude remains Ralph-only by design, encode that decision in a named helper instead of implicit default arguments.

The goal is to eliminate “magic Ralph-only” behavior hiding inside `_claude_allowed_tools()`.

**Step 4: Add comments as knowledge anchors**

Document in `tool_names.py`:

- which aliases are stable contracts,
- which are transport-specific,
- whether server names other than `ralph` are first-class or intentionally unsupported.

**Step 5: Verify**

Run:

```bash
pytest tests/test_mcp_policy_outcomes.py tests/test_agents_invoke.py -q
ruff check ralph/ tests/
mypy ralph/ --strict
```

Expected: alias policy is testable and no longer encoded as accidental defaults.

**Step 6: Commit**

```bash
git add ralph/mcp/tool_names.py ralph/agents/invoke.py tests/test_mcp_policy_outcomes.py tests/test_agents_invoke.py
git commit -m "refactor: make MCP tool alias policy explicit"
```

---

### Task 6: Fortify the knowledge base in code comments and supporting docs

**Files:**
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Modify: `ralph-workflow/ralph/mcp/tool_names.py`
- Modify: `ralph-workflow/docs/mcp-tool-restriction.md`
- Modify: `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md`

**Step 1: Write the failing documentation checklist**

Before editing, create a checklist in the plan execution notes that all of these statements must be true after the change:

- docs do not overclaim custom MCP usability,
- Claude/OpenCode/Codex guarantees are separated clearly,
- code comments explain intent at the enforcement seams,
- the postmortem points to the final policy instead of an outdated partial state.

**Step 2: Update code comments**

Add compact but durable comments near:

- `_prepare_codex_home()`
- `_claude_mcp_config()` and `_load_existing_claude_mcp_config()`
- `_claude_allowed_tools()`
- `_merge_opencode_config_content()`
- `claude_allowed_tool_names()` and related helpers in `tool_names.py`

These comments should answer “why is this policy correct?” and “what must not drift?”

**Step 3: Update docs**

Revise:

- `ralph-workflow/docs/mcp-tool-restriction.md` so it precisely states preservation vs usability vs enforcement for each transport.
- `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md` so the postmortem references the final transport policy and test seam once complete.

**Step 4: Verify docs and comments against tests**

Run:

```bash
pytest tests/test_agents_invoke.py -q
pytest tests/test_mcp_policy_outcomes.py -q
ruff check ralph/ tests/
mypy ralph/ --strict
```

Expected: comments/docs match observed behavior and no new drift is introduced.

**Step 5: Commit**

```bash
git add ralph/agents/invoke.py ralph/mcp/tool_names.py docs/mcp-tool-restriction.md docs/RFC/RFC-011-mcp-tool-availability-postmortem.md tests/test_agents_invoke.py tests/test_mcp_policy_outcomes.py
git commit -m "docs: anchor MCP transport intent in code and docs"
```

---

### Task 7: Full verification and regression sweep

**Files:**
- Verify only

**Step 1: Run focused transport tests**

```bash
pytest tests/test_agents_invoke.py -q
pytest tests/test_mcp_policy_outcomes.py -q
```

Expected: all MCP/config transport and alias tests pass.

**Step 2: Run full project verification**

```bash
make verify
```

Expected: `ruff`, `mypy`, full `pytest`, and coverage gate all pass.

**Step 3: Smoke-check documentation claims manually**

Read these files after verification and confirm they agree:

- `ralph/agents/invoke.py`
- `ralph/mcp/tool_names.py`
- `docs/mcp-tool-restriction.md`
- `docs/RFC/RFC-011-mcp-tool-availability-postmortem.md`

Expected: no remaining contradiction like “custom MCPs are preserved” paired with “only Ralph tools are allowlisted.”

**Step 4: Final commit**

```bash
git add .
git commit -m "fix: harden MCP config policy across transports"
```

---

## Notes for the implementer

- Prefer explicit helper names over clever dict mutation if the latter hides transport policy.
- Do not broaden preservation to unrelated provider settings without a specific need.
- Do not leave transport behavior “technically preserved but unusable”; pick a policy and encode it fully.
- Keep TDD strict: every new invariant gets a failing test first.
- If Claude cannot safely support arbitrary preserved custom MCP tool allowlisting, update the docs and comments to say so unambiguously rather than implying support.
