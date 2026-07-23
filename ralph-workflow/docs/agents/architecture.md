# Agent Invoke Architecture

See [Agent Subsystem README](README.md) for the unified entry point.

## Stack overview

Ralph's agent invoke stack has six layers that cooperate to route a user prompt
to the right built-in or custom agent, configure its transport and parser, and
produce an executable command:

```
registration  →  parser  →  strategy  →  CommandBuilder
                       ↓
               RuntimeResolver  →  config / chain
```

1. **Registration** — `register_agent_support()` accepts agent metadata and registers
   an `AgentSupport` entry in the `AgentCatalog`.
2. **Parser** — `AgentCatalog.get_parser(name)` resolves a parser factory from
   `_PARSER_REGISTRY` (a read-only view over `AgentCatalog`) and yields
   structured `AgentOutputLine` events from raw agent output.
3. **Strategy** — `AgentCatalog.get_strategy(transport, command=name)` resolves an
   execution strategy from `_STRATEGY_DISPATCH` (also a read-only view over
   `AgentCatalog`). Each built-in transport maps to a strategy class.
4. **CommandBuilder** — `COMMAND_BUILDERS[transport]` dispatches to a
   transport-specific `CommandBuilder` class that assembles the CLI invocation
   from `AgentConfig`.
5. **RuntimeResolver** — `RUNTIME_RESOLVERS[transport]` dispatches to a
   transport-specific `RuntimeResolver` that materialises the runtime environment
   (environment variables, MCP upstreams, workspace paths) before the process
   starts.
6. **Config and chains** — `[agents.<name>]`, `[ccs_aliases.<name>]`,
   `[agent_chains]`, and `[agent_drains]` in `ralph-workflow.toml` configure every
   aspect of an agent's behaviour. The shorthand forms (`claude/<model>`,
   `opencode/<provider>`, `agy/<model>`, `pi/<model>`) are expanded by the
   config layer into fully-qualified `AgentSpec` objects before registration.
   The `pi/<model>` shorthand preserves the full suffix (e.g.
   `pi/anthropic/claude-sonnet-4-20250514` becomes
   `--model anthropic/claude-sonnet-4-20250514`) using
   `name.removeprefix('pi/')` so multi-segment `provider/id` patterns
   round-trip intact.

## Registration

The single canonical entry point for adding, updating, or removing an agent is
`register_agent_support()` (defined in `ralph/agents/registration.py`).  See the
step-by-step recipe in [adding-a-new-agent.md](adding-a-new-agent.md).

## Single source of truth: builtin_supports()

Built-in agents are declared in a single module `ralph/agents/builtin.py` which exposes the private `_BUILTIN_AGENT_SUPPORTS` tuple. Callers query the built-in catalog supports through the public `builtin_supports()` function.

To keep the catalog and agent registries in lockstep, `AgentRegistry.from_config` and `AgentRegistry(catalog=...)` both invoke `_seed_catalog_with_builtins()`. The seed is idempotent, ensuring that duplicate invocations do not cause double-registration errors.

For details on how to register and manage custom agents, or to modify built-in ones, see the [Agent Subsystem README](README.md) and [adding-a-new-agent.md](adding-a-new-agent.md).

## Parser and execution strategy

Every parser (`ClaudeParser`, `CodexParser`, `OpenCodeParser`, `GeminiParser`,
`GenericParser`, `PiParser`) is registered in `_PARSER_REGISTRY`, a
`types.MappingProxyType` view over `AgentCatalog`.  The registry is
read-only at runtime — writes go through `AgentCatalog.add` /
`AgentCatalog.remove`, which keep the view synchronised.  `PiParser`
covers the documented `AgentSessionEvent` NDJSON vocabulary from
`pi --mode json` (https://pi.dev/docs/latest/json).

Every execution strategy (`GenericExecutionStrategy`,
`CompletionEnforcingStrategy`, etc.) is registered in `_STRATEGY_DISPATCH`,
likewise a `MappingProxyType` view over `AgentCatalog`.  Adding a custom
strategy for a built-in transport is done by registering it in the catalog; the
dispatch view reflects the change automatically.

## NDJSON parser layer (`NdjsonParserBase`)

The 6 wire-format NDJSON parsers (`claude`, `opencode`, `codex`, `gemini`,
`generic`, `pi`) share a common base class `NdjsonParserBase` in
`ralph/agents/parsers/_ndjson_base.py`. The base owns 6 behaviors that
used to be duplicated across every parser's `classify_line`:

1. strip the SSE `data:` prefix
2. short-circuit on `[DONE]` (yields `AgentOutputLine(type='stop')`)
3. non-JSON lines -> `AgentOutputLine(type='raw', content=stripped)`
4. non-dict JSON  -> `AgentOutputLine(type='raw', content=stripped)`
5. lifecycle event types are suppressed via the canonical
   `is_lifecycle_event` helper (no lines yielded, subclass hook never called)
6. `{"error": ...}` shapes produce `AgentOutputLine(type='error', ...)` via
   the canonical `extract_error_message` helper

Subclasses override a single `_dispatch_json_object(obj, raw)` hook to handle
per-agent event types. `NdjsonParserBase` itself inherits from
`ParserTemplateBase`, which remains the public base for any custom
(non-NDJSON) parser. See `tests/agents/parsers/test_ndjson_base.py` for the
per-behavior coverage of the base and the per-parser migration tests under
`tests/agents/parsers/test_<parser>_uses_ndjson_base.py` for behavior
preservation.

## Built-in declaration (`BuiltinAgentSpec`)

The 7 built-in agents (`claude`, `claude-headless`, `codex`, `opencode`,
`nanocoder`, `agy`, `pi`) are declared in `ralph/agents/builtin.py` as
`BuiltinAgentSpec` dataclass rows. Each row is materialized into an
`AgentSupport` via `BuiltinAgentSpec.to_support(name)`, collapsing the
14-kwarg `AgentSupport.from_registration_kwargs` call into a single
declarative line per agent. The frozen golden test
`tests/agents/test_builtin_spec_consolidation.py` pins the
(name, transport, parser_factory, strategy_factory, config.cmd,
session_flag, json_parser) tuple for every built-in, so the BuiltinAgentSpec
refactor is provably no-op.

## CommandBuilder and RuntimeResolver

Every `AgentTransport` enum value has a `CommandBuilder` class in
`ralph/agents/invoke/_command_builders/` and a `RuntimeResolver` class in
`ralph/agents/invoke/_runtime_resolvers/`. The two dispatch dictionaries are:

- `COMMAND_BUILDERS` in `ralph/agents/invoke/_command_builders/__init__.py` —
  maps `AgentTransport` to a `CommandBuilder` class
- `RUNTIME_RESOLVERS` in `ralph/agents/invoke/_runtime_resolvers/__init__.py` —
  maps `AgentTransport` to a `RuntimeResolver` class

Both dicts are populated at module import time and key every `AgentTransport`
value (CLAUDE, CLAUDE_INTERACTIVE, CODEX, OPENCODE, NANOCODER, GENERIC, AGY, PI).
No transport silently falls through to a default; `DefaultRuntimeResolver` handles
only the explicit GENERIC entry and raises `UnsupportedMcpTransportError` for
other transports with an MCP endpoint.

The guard test at
`tests/agents/invoke/test_dispatch_table_covers_every_transport.py` iterates
every `AgentTransport` value and asserts both `COMMAND_BUILDERS[transport]` and
`RUNTIME_RESOLVERS[transport]` are non-None.  Adding a new `AgentTransport`
value without registering both classes fails this test before reaching production.

## Config and chains

Agents are configured in `ralph-workflow.toml` under four top-level sections:

- `[agents.<name>]` — defines an agent's transport, parser, strategy factory,
  flags, and display name.
- `[ccs_aliases.<name>]` — creates a short alias that expands to an agent name,
  used in `agent_chains` and `agent_drains`.
- `[agent_chains]` — an ORDERED FALLBACK LIST of agent names for one role.
  Ralph tries the first agent; if it fails or exhausts its retries, Ralph
  moves to the next one instead of stopping immediately. This is the
  canonical definition; do NOT describe it as inter-agent piping.
- `[agent_drains]` — a ROUTING LABEL that binds a pipeline phase to a chain.
  The drain name is the pipeline key (`planning`, `development`, `analysis`,
  `commit`, …) and its value is the chain name that handles it. The drain
  is NOT parallel fan-out — that is a separate effect handled by
  `[phases.<name>].parallelization`, not by `[agent_drains]`.

The shorthand forms for built-in transports are also accepted as agent names:
`claude/<model>` (e.g. `claude/sonnet`), `opencode/<provider>` (e.g.
`opencode/anthropic`), `agy/<model>` (e.g. `agy/gemini-2.5`), and
`pi/<model>` (e.g. `pi/anthropic/claude-sonnet-4-20250514`).  The config
loader expands these into fully-qualified `AgentSpec` objects before registering
the agent in the catalog.

## Adding a new agent

To learn how to register a new agent, perform updates, or remove an agent, see
the canonical recipe in [adding-a-new-agent.md](adding-a-new-agent.md).

## AGY and Pi end-to-end smoke walkthroughs

> **Canonical home for the per-transport smoke details.** The
> troubleshooting index points here for the parity table column
> meanings, mock-vs-live diagnostic paths, and how the upstream
> `agy` binary or the local `pi` binary is exercised.

### AGY transport end-to-end smoke

To verify that the AGY transport is wired correctly from Ralph Workflow
through the live `agy` binary, run the canonical AGY smoke test on
Linux or macOS:

```bash
python -m ralph smoke-interactive-agy
```

The parity table reports five acceptance signals:

| Column | Green means |
|--------|-------------|
| File | `tmp/interactive-agy-smoke/todo-list.js` was created |
| Session | A session ID was observed in the transcript |
| Parser events | The transcript produced parseable events (Claude parity only) |
| Tool activity | Tool-use/tool-result signals or the artifact's `headless_guide_checks` were observed |
| Artifact | The `smoke_test_result` artifact was submitted |

A red column in File, Tool activity, or Artifact indicates a Ralph
Workflow regression. The Session and Parser events columns may show
`missing`/`0` on AGY headless `--print` runs: AGY does not emit a
session ID or parser-friendly stdout stream in `--print` mode. Because
AGY's headless `--print` mode does not reliably call Ralph Workflow's
streamable-HTTP MCP tools, the smoke prompt instructs AGY to write the
`smoke_test_result` artifact directly to `.agent/artifacts/smoke_test_result.json`;
tool activity is then inferred from that artifact.

If AGY exits 0 but the parity table reports no file, no artifact, and
the `Breaks` column contains `AGY --print returned empty stdout: ...`,
the upstream `agy` binary itself produced no stdout. The smoke
detector reads `~/.gemini/antigravity-cli/cli.log` and reports the
measured root cause in the `Breaks` column. The most common upstream
conditions are an individual API quota exhausted error
(`429 RESOURCE_EXHAUSTED`), whose diagnostic names the reset window,
or an unrecognized model ID. Lowercased or slashed slugs such as
`agy/gemini-3.5-flash-low` are not accepted by AGY v1.0.8; use the
exact display names from `agy models` (the eight canonical names include
`Gemini 3.5 Flash (Medium/High/Low)`, `Gemini 3.1 Pro (Low/High)`,
`Claude Sonnet 4.6 (Thinking)`, `Claude Opus 4.6 (Thinking)`, and
`GPT-OSS 120B (Medium)`). These are upstream AGY conditions, not
Ralph Workflow regressions; wait for the quota reset or pin a recognized
alias via `--agent agy/<model>`.

#### Distinguishing live-quota failure from mock-quota output

The live `agy` v1.0.8 binary is re-measured periodically; the
re-measurement notes (upstream source URLs re-fetched, local binary
probed, full AGY smoke parity table) live in `tmp/agy-source-of-truth.txt`
and `tmp/agy-smoke-run.log` to keep the troubleshooting index lean.

When running with `RALPH_AGY_BINARY` set (for example to the
deterministic mock at `tests/_support/mock_agy.sh` for CI), an empty
stdout with `MOCK_AGY_BEHAVIOR=quota_exhausted` is expected and
reported as an informational break, not as the live upstream quota
diagnostic. The mock entrypoint is `tests/_support/mock_agy.py` (run
as `python -m tests._support.mock_agy`); `mock_agy.sh` is a thin
wrapper suitable for `RALPH_AGY_BINARY`. To verify the harness itself,
re-run the canonical AGY smoke above with `RALPH_AGY_BINARY=tests/_support/mock_agy.sh`
set. It should report file=yes, artifact=yes, and no upstream-quota break.

### Pi.dev transport end-to-end smoke

To verify that the Pi (pi.dev) transport is wired correctly, that the
documented `AgentSessionEvent` NDJSON format parses without error, and
that `pi --mode json <prompt>` produces the expected argv, run the two
pytest suites that cover the public surface end-to-end without
touching the network or a real `pi` binary:

```bash
# Drive the public surface (AgentRegistry -> catalog.get('pi') -> build_command)
uv run pytest tests/agents/test_pi_dev_blackbox.py -q

# Pin the documented AgentSessionEvent vocabulary against the committed fixture
uv run pytest tests/agents/parsers/test_pi_dev_wire_format_spec.py -q
```

Both tests are pure-Python (no `time.sleep`, no real subprocess, no
network), so they pass deterministically under the 60 s combined test
budget enforced by `make verify`. The wire-format spec test loads the
committed fixture at `tests/agents/parsers/fixtures/pi_dev_documented_events.json`
(NOT the transient `tmp/pi-dev-docs/inventory.md`), so a clean-checkout
run does not depend on transient state.

The argv assertion in the black-box test ends with the actual prompt
TEXT loaded from a `tmp_path` fixture (e.g. `hello world`) per the
public contract in `ralph-workflow/ralph/agents/invoke/_command_builders/__init__.py:_load_prompt_text`
with `positional_prompt=True`. Do NOT assert the literal `'PROMPT.md'`
— that is the prompt file PATH, not the file CONTENT that the
positional argv element carries.

For the live `pi` binary end-to-end path, see <https://pi.dev/docs/latest/usage> for the documented `--mode json` invocation and the documented `--approve` (`-a`) project-trust override. Pi has no native CLI MCP config file, so Ralph Workflow removes `RALPH_MCP_ENDPOINT` from the Pi subprocess environment, writes a generated Pi extension, and passes it with `--no-builtin-tools --extension` so Pi receives Ralph Workflow MCP tools through its custom-tool API.
