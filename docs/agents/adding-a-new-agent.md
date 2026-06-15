# Adding a new agent

Adding a new agent to Ralph Workflow is built around a single registration
call, `register_agent_support()`. The infrastructure is built on three shared
seams:

- `BaseExecutionStrategy` — a template-method base class with sensible defaults
  for activity classification, idle classification, and exit classification.
- `CompletionEnforcingStrategy` — a mixin for agents that require explicit
  completion evidence before a clean exit is considered terminal.
- `register_agent_support()` — a one-call API that writes the parser into the
  parser-type-keyed registry, the strategy into the transport-keyed registry
  used by `strategy_for_transport()`, and the agent configuration in the
  caller's `AgentRegistry`.
- `strategy_for_command()` — runtime strategy resolution that selects the
  strategy registered for an agent's full command string before falling back to
  the transport-keyed slot used by `strategy_for_transport()`.

Command-builder wiring and role-classifier wiring remain transport-keyed
because they are transport concerns: transport-specific flags (`--mcp-config`,
`--print`, `--session`) and the official-docs-based role classifier vary by
transport, not by agent name. The headless vs interactive distinction is fully
covered by the `AgentTransport` enum plus the strategy's
`supports_session_continuation()` contract.

The API keeps all additional state caller-owned: you pass the target
`AgentRegistry`, and the only module-level lookup tables used are the existing
`_PARSER_REGISTRY` parser registry, `_STRATEGY_DISPATCH` transport-keyed
strategy registry, and `_CUSTOM_COMMAND_REGISTRY` collision-free custom-command
registry.  A registered agent is keyed by both its `name` and its full
executable command string, so runtime paths such as `invoke_agent()` and the
smoke-test harness can resolve the correct parser and strategy even when the
command string differs from the registered name.  Custom commands are stored in
a separate collision-free registry keyed by the full command; a custom command
like `claude wrapper` does not replace the built-in `claude` parser or strategy.

## Import path

The API is opt-in. It is intentionally **not** re-exported from
`ralph.agents` so the public surface stays small.

```python
from ralph.agents.registration import register_agent_support, get_registered_agent_support
```

## Headless agent example

```python
from collections.abc import Iterator

from ralph.agents.execution_state import (
    BaseExecutionStrategy,
    strategy_for_command,
    strategy_for_transport,
)
from ralph.agents.parsers import AgentOutputLine, get_parser
from ralph.agents.parsers.base import AgentParser
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport


class MyAgentParser:
    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="output", content=line, raw=line)


class MyAgentStrategy(BaseExecutionStrategy):
    """Inherit defaults, override only what the agent needs."""


registry = AgentRegistry()
register_agent_support(
    "my-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=MyAgentParser,
    strategy_factory=MyAgentStrategy,
    agent_registry=registry,
)

assert isinstance(get_parser("my-agent"), MyAgentParser)
assert isinstance(
    strategy_for_transport(AgentTransport.GENERIC), MyAgentStrategy
)
assert isinstance(
    strategy_for_command("my-agent", AgentTransport.GENERIC), MyAgentStrategy
)
assert "my-agent" in registry.agents
```

## Interactive agent example

Interactive agents use a transport that supports session continuation. Set
`interactive=True` to wire a session-resume flag template into the agent
configuration.

```python
register_agent_support(
    "my-interactive-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser_factory=MyAgentParser,
    strategy_factory=MyAgentStrategy,
    agent_registry=registry,
    interactive=True,
)

config = registry.agents["my-interactive-agent"]
assert config.transport == AgentTransport.CLAUDE_INTERACTIVE
assert config.session_flag is not None
assert isinstance(
    strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE), MyAgentStrategy
)
```

## Custom command and flags

By default the agent's executable command (`AgentConfig.cmd`) equals the
registered `name`. For real agents the command name usually differs from the
registered name or includes flags.  Pass the command and other flags as keyword
arguments; the parser and strategy are also registered under the full
executable command string so runtime resolution picks them up:

```python
register_agent_support(
    "my-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=MyAgentParser,
    strategy_factory=MyAgentStrategy,
    agent_registry=registry,
    cmd="my-agent-cli --json",
    output_flag="--json",
    print_flag="--print",
    session_flag="--continue {}",
    can_commit=True,
)
```

Supported overrides mirror `AgentConfig`: `cmd`, `output_flag`, `yolo_flag`,
`verbose_flag`, `can_commit`, `model_flag`, `print_flag`, `streaming_flag`,
`session_flag`, `display_name`, and `subagent_capability`.

When `cmd` is overridden, both `get_parser("my-agent")` and
`get_parser("my-agent-cli --json")` return the registered parser, and
`strategy_for_command("my-agent-cli --json", AgentTransport.GENERIC)` returns
the registered strategy.  Registering a reserved built-in parser name such as
`claude` or `opencode` raises `ValueError`, so a custom `claude wrapper`
command cannot overwrite the built-in `claude` parser or strategy.

## Multiple agents on the same transport

Multiple custom agents may share a transport.  Each keeps its own parser entry
(keyed by `name` and by full command string) and its own strategy.  The last
registration for a given transport wins the transport-keyed slot used by
`strategy_for_transport()`, while `strategy_for_command(cmd, transport)` still
resolves each agent by its full command string.  You can also retrieve a
specific agent's registered pieces with `get_registered_agent_support(name)`:

```python
register_agent_support(
    "agent-a", transport=AgentTransport.GENERIC, ...
)
register_agent_support(
    "agent-b", transport=AgentTransport.GENERIC, ...
)

strategy_a = strategy_for_command("agent-a", AgentTransport.GENERIC)
strategy_b = strategy_for_command("agent-b", AgentTransport.GENERIC)

pair_a = get_registered_agent_support("agent-a")
pair_b = get_registered_agent_support("agent-b")
```

Duplicate full-command registrations are rejected with `ValueError` so two
agents cannot silently clobber each other.

## Files to touch

```
your_project/
└── my_agent_integration.py          # one file: parser + strategy + registration
```

No edits to `ralph/agents/parsers/__init__.py`,
`ralph/agents/execution_state/_factory.py`, or `ralph/agents/registry.py` are
required for a typical new agent.

## Test checklist

When you add a new agent, cover these behaviours with fakes (`_FakeHandle`,
`FakeLivenessProbe`, `FakeClock`):

- Parser flush invariants: short lines accumulate, paragraph boundaries flush,
  iterator exhaustion flushes.
- Strategy classification: non-blank lines produce `OUTPUT_LINE`, JSON errors
  produce `ERROR_LINE`, lifecycle-only lines do not keep a quiet run alive.
- End-to-end round-trip: `get_parser(name)`,
  `strategy_for_transport(transport)`, `strategy_for_command(cmd, transport)`,
  and `registry.agents[name]` all retrieve the registered pieces.
- Runtime parser resolution: `_parser_key_for_config(config)`,
  `stream_parsed_agent_activity(..., agent_config=config)`, and
  `collect_commit_agent_output(..., parser_type=resolve_parser_key(...))` all
  select the registered parser when `json_parser` is `JsonParserType.GENERIC`.
- Coexistence: two agents registered on the same transport both remain
  retrievable via `get_registered_agent_support()` and via
  `strategy_for_command(cmd, transport)` for each command.
- Dependency injection: a strategy factory that accepts `label_scope` and
  `registry` receives those kwargs from `strategy_for_transport()` and
  `strategy_for_command()`.
- Collision safety: registering a reserved built-in parser name raises
  `ValueError`, and duplicate `cmd` registrations raise `ValueError`.

## Reference tests

- `tests/agents/test_register_agent_support.py` — API contract, isolation,
  same-transport coexistence, kwargs preservation, parser-resolution paths,
  and collision guards.
- `tests/agents/test_add_a_new_agent_recipe.py` — headless end-to-end recipe.
- `tests/agents/test_add_a_new_interactive_agent_recipe.py` — interactive
  end-to-end recipe.
- `tests/test_opencode_session_execution_generic_execution_strategy.py` —
  behaviour preserved by `BaseExecutionStrategy` defaults.
