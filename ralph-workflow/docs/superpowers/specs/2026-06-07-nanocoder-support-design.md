# Nanocoder Support Design

## Goal

Add Nanocoder as a first-class built-in Ralph Workflow agent with Ralph-managed MCP wiring, shared transport/runtime handling, and black-box-testable integration behavior.

## Scope

- Add a built-in `nanocoder` agent and transport.
- Use Nanocoder's documented `run` mode for unattended execution.
- Inject Ralph's run-scoped MCP endpoint through Nanocoder's documented configuration surface rather than requiring manual user setup.
- Preserve the existing Ralph completion contract: artifact proof or `declare_complete`, not transcript confidence.
- Keep bundled default chains unchanged; Nanocoder becomes opt-in.

## Documentation Basis

Nanocoder documentation reviewed for this design:

- README quick-start and CLI flags via upstream README.
- Configuration docs for `agents.config.json`, `NANOCODER_CONFIG_DIR`, `NANOCODER_MCPSERVERS`, and precedence rules.
- MCP configuration docs for `.mcp.json`, transport shapes, `alwaysAllow`, and precedence.
- Development modes docs for `--mode` and `run` defaulting to `auto-accept`.
- Session management docs for saved sessions and `/resume` behavior.

## Architecture

### Built-in Agent and Transport

Nanocoder should be modeled like the existing built-in agents instead of adding a one-off execution path. The implementation should extend Ralph's current transport-driven seams:

- `AgentTransport` gains `NANOCODER`.
- `builtin_agents()` gains `nanocoder`.
- command construction flows through the existing shared `_build_command()` dispatcher.
- runtime MCP wiring flows through `resolve_invocation_runtime()`.

This keeps Nanocoder inside the same registry, recovery, and diagnostics surfaces as the other built-ins and avoids agent drift.

### Command Shape

Nanocoder's documented unattended entrypoint is:

`nanocoder --mode auto-accept run "<prompt>"`

Ralph should build that command through the shared command-builder module, not inline in invoke logic. The prompt should be passed as full prompt text, consistent with other managed headless transports.

### MCP Wiring

Nanocoder documents two related MCP surfaces:

- `agents.config.json` under `nanocoder.mcpServers`
- `.mcp.json`
- environment-variable overrides such as `NANOCODER_MCPSERVERS`

For Ralph-managed runs, the cleanest shared-runtime fit is documented env-based injection. Ralph should synthesize a run-scoped `NANOCODER_MCPSERVERS` payload that exposes Ralph's managed MCP endpoint as a remote HTTP MCP server for the active run.

Ralph should continue to proxy user/project upstream MCP servers through its own upstream merge flow rather than letting the agent bypass Ralph's managed boundary.

### Session and Retry Policy

Nanocoder docs describe persistent sessions and `/resume` inside Nanocoder itself, but no documented unattended `run`-mode resume flag was found during documentation review. Ralph therefore must not invent session-resume semantics.

Design consequence:

- no built-in `session_flag` for `nanocoder`
- no special-case resume mapping for `AgentTransport.NANOCODER`
- retries for Nanocoder remain fresh-session retries unless future upstream docs add a documented CLI resume contract

### Parser and Completion

Nanocoder docs clearly document plain run-mode terminal behavior but do not establish compatibility with Ralph's existing Claude, Codex, or OpenCode structured parser contracts. The initial integration therefore should use the generic parser/runtime path unless direct test evidence from the CLI proves a stronger built-in parser match.

Completion must stay black-box and transport-agnostic:

- successful phase artifact created during the run, or
- explicit `declare_complete`

### Black-Box Testability

This integration must remain testable without live Nanocoder execution.

Required test seams:

- registry behavior observable through built-in agent lookup and validation
- command-building behavior observable from built argv
- runtime MCP wiring observable from synthesized environment/config payloads
- docs/config behavior observable from rendered documentation and defaults text

Tests must not assert private helper internals when public observable seams exist. The design intentionally routes Nanocoder through the existing shared seams so tests can cover the behavior the operator actually depends on.

## Files Expected To Change

- `ralph/config/agent_transport.py`
- `ralph/config/agent_config.py`
- `ralph/agents/registry.py`
- `ralph/agents/invoke/_commands.py`
- `ralph/agents/invoke/__init__.py`
- `ralph/mcp/transport/__init__.py`
- `ralph/mcp/transport/nanocoder.py`
- selected docs/config surfaces that enumerate supported agents
- focused tests for registry, command-building, runtime wiring, and docs text

## Non-Goals

- Do not change Ralph's default bundled chains to include Nanocoder.
- Do not add undocumented Nanocoder resume behavior.
- Do not add a Nanocoder-only execution path outside Ralph's shared transport/runtime machinery.
