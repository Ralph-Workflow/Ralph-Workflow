# Agent Invoke Architecture

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
   `opencode/<provider>`, `agy/<model>`) are expanded by the config layer into
   fully-qualified `AgentSpec` objects before registration.

## Registration

The canonical way to add, update, or remove an agent is through the public
`AgentCatalog` API.  See the step-by-step recipe in
[adding-a-new-agent.md](adding-a-new-agent.md).

## Parser and execution strategy

Every parser (`ClaudeParser`, `CodexParser`, `OpenCodeParser`, `GeminiParser`,
`GenericParser`) is registered in `_PARSER_REGISTRY`, a `types.MappingProxyType`
view over `AgentCatalog`.  The registry is read-only at runtime — writes go
through `AgentCatalog.add` / `AgentCatalog.remove`, which keep the view
synchronised.

Every execution strategy (`GenericExecutionStrategy`,
`CompletionEnforcingStrategy`, etc.) is registered in `_STRATEGY_DISPATCH`,
likewise a `MappingProxyType` view over `AgentCatalog`.  Adding a custom
strategy for a built-in transport is done by registering it in the catalog; the
dispatch view reflects the change automatically.

## CommandBuilder and RuntimeResolver

Every `AgentTransport` enum value has a `CommandBuilder` class in
`ralph/agents/invoke/_command_builders/` and a `RuntimeResolver` class in
`ralph/agents/invoke/_runtime_resolvers/`. The two dispatch dictionaries are:

- `COMMAND_BUILDERS` in `ralph/agents/invoke/_command_builders/__init__.py` —
  maps `AgentTransport` to a `CommandBuilder` class
- `RUNTIME_RESOLVERS` in `ralph/agents/invoke/_runtime_resolvers/__init__.py` —
  maps `AgentTransport` to a `RuntimeResolver` class

Both dicts are populated at module import time and key every `AgentTransport`
value (CLAUDE, CLAUDE_INTERACTIVE, CODEX, OPENCODE, NANOCODER, GENERIC, AGY).
No transport silently falls through to a default; `DefaultRuntimeResolver` handles
only the explicit GENERIC entry and raises `UnsupportedMcpTransportError` for
other transports with an MCP endpoint.

The guard test at
`tests/agents/invoke/test_dispatch_table_covers_every_transport.py` iterates
every `AgentTransport` value and asserts both `COMMAND_BUILDERS[transport]` and
`RUNTIME_RESOLVERS[transport]` are non-None.  Adding a new `AgentTransport`
value without registering both classes fails this test before reaching production.

## Config and chains

Agents are configured in `ralph-workflow.toml` under three top-level sections:

- `[agents.<name>]` — defines an agent's transport, parser, strategy factory,
  flags, and display name.
- `[ccs_aliases.<name>]` — creates a short alias that expands to an agent name,
  used in `agent_chains` and `agent_drains`.
- `[agent_chains]` — chains the output of one agent into the input of another,
  with optional transformation filters.
- `[agent_drains]` — fans a single prompt out to multiple agents in parallel and
  collects all responses.

The shorthand forms for built-in transports are also accepted as agent names:
`claude/<model>` (e.g. `claude/sonnet`), `opencode/<provider>` (e.g.
`opencode/anthropic`), and `agy/<model>` (e.g. `agy/gemini-2.5`).  The config
loader expands these into fully-qualified `AgentSpec` objects before registering
the agent in the catalog.

## Adding a new agent

To learn how to register a new agent, perform updates, or remove an agent, see
the canonical recipe in [adding-a-new-agent.md](adding-a-new-agent.md).
