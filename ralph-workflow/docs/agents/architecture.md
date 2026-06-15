# Agent Invoke Architecture

## Per-Transport Dispatch Model

Every `AgentTransport` enum value has a `CommandBuilder` class in `ralph/agents/invoke/_command_builders/` and a `RuntimeResolver` class in `ralph/agents/invoke/_runtime_resolvers/`. The two dispatch dictionaries are:

- `COMMAND_BUILDERS` in `ralph/agents/invoke/_command_builders/__init__.py` — maps `AgentTransport` to a `CommandBuilder` class
- `RUNTIME_RESOLVERS` in `ralph/agents/invoke/_runtime_resolvers/__init__.py` — maps `AgentTransport` to a `RuntimeResolver` class

Both dicts are populated at module import time and key every `AgentTransport` value (CLAUDE, CLAUDE_INTERACTIVE, CODEX, OPENCODE, NANOCODER, GENERIC, AGY). No transport silently falls through to a default; `DefaultRuntimeResolver` handles only the explicit GENERIC entry and raises `UnsupportedMcpTransportError` for other transports with an MCP endpoint.

## Swapping a Custom Handler

To swap a custom `CommandBuilder` or `RuntimeResolver` for an existing transport:

1. Write a `CommandBuilder` subclass with a `build(config, prompt_file, *, options)` method
2. Write a `RuntimeResolver` subclass with a `resolve(config, extra_env, workspace_path, *, base_env, system_prompt_file, unsafe_mode)` method
3. Register each in the corresponding dispatch dict via a one-line `dict` update:

```python
COMMAND_BUILDERS[AgentTransport.GENERIC] = MyCustomCommandBuilder
RUNTIME_RESOLVERS[AgentTransport.GENERIC] = MyCustomRuntimeResolver
```

No `AgentTransport` enum change, no private helper, no production-code edit beyond the dict update. The public `ralph.agents.invoke.build_command` and `ralph.agents.invoke.resolve_invocation_runtime` use the swapped classes automatically.

## Guard Test

The guard test at `tests/agents/invoke/test_dispatch_table_covers_every_transport.py` iterates every `AgentTransport` value and asserts both `COMMAND_BUILDERS[transport]` and `RUNTIME_RESOLVERS[transport]` are non-None. Adding a new `AgentTransport` value without registering both classes fails this test before reaching production.
