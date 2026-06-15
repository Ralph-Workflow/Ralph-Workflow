# Adding a new agent

Adding a new agent to Ralph Workflow is built around a single registration
call, `register_agent_support()`. The infrastructure is built on three shared
seams:

- `BaseExecutionStrategy` — a template-method base class with sensible defaults
  for activity classification, idle classification, and exit classification.
- `CompletionEnforcingStrategy` — a mixin for agents that require explicit
  completion evidence before a clean exit is considered terminal.
- `register_agent_support()` — a one-call API that writes the parser and
  strategy into the existing transport-keyed and parser-type-keyed registries,
  and records the agent configuration in the caller's `AgentRegistry`.

Command-builder wiring and role-classifier wiring remain transport-keyed
because they are transport concerns: transport-specific flags (`--mcp-config`,
`--print`, `--session`) and the official-docs-based role classifier vary by
transport, not by agent name. The headless vs interactive distinction is fully
covered by the `AgentTransport` enum plus the strategy's
`supports_session_continuation()` contract.

The API keeps all additional state caller-owned: you pass the target
`AgentRegistry`, and the only module-level lookup tables used are the existing
`_PARSER_REGISTRY` and `_STRATEGY_DISPATCH` pure-data registries.

## Import path

The API is opt-in. It is intentionally **not** re-exported from
`ralph.agents` so the public surface stays small.

```python
from ralph.agents.registration import register_agent_support, get_registered_agent_support
```

## Headless agent example

```python
from collections.abc import Iterator

from ralph.agents.execution_state import BaseExecutionStrategy, strategy_for_transport
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
assert isinstance(strategy_for_transport(AgentTransport.GENERIC), MyAgentStrategy)
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
```

## Custom command and flags

By default the agent's executable command (`AgentConfig.cmd`) equals the
registered `name`. For real agents you usually need to override that and other
flags. Pass them as keyword arguments:

```python
register_agent_support(
    "my-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=MyAgentParser,
    strategy_factory=MyAgentStrategy,
    agent_registry=registry,
    cmd="my-agent-cli",
    output_flag="--json",
    print_flag="--print",
    session_flag="--continue {}",
    can_commit=True,
)
```

Supported overrides mirror `AgentConfig`: `cmd`, `output_flag`, `yolo_flag`,
`verbose_flag`, `can_commit`, `model_flag`, `print_flag`, `streaming_flag`,
`session_flag`, `display_name`, and `subagent_capability`.

## Multiple agents on the same transport

The transport-keyed strategy slot is a fallback used by
`strategy_for_transport()`. Multiple custom agents may share a transport; each
keeps its own parser entry and its own configuration. Retrieve a specific
agent's strategy with `get_registered_agent_support(name)`:

```python
register_agent_support(
    "agent-a", transport=AgentTransport.GENERIC, ...
)
register_agent_support(
    "agent-b", transport=AgentTransport.GENERIC, ...
)

pair_a = get_registered_agent_support("agent-a")
pair_b = get_registered_agent_support("agent-b")
```

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
  `strategy_for_transport(transport)`, and `registry.agents[name]` all retrieve
  the registered pieces.
- Runtime parser resolution: `_parser_key_for_config(config)`,
  `stream_parsed_agent_activity(..., agent_config=config)`, and
  `collect_commit_agent_output(..., parser_type=resolve_parser_key(...))` all
  select the registered parser when `json_parser` is `JsonParserType.GENERIC`.
- Coexistence: two agents registered on the same transport both remain
  retrievable via `get_registered_agent_support()`.
- Dependency injection: a strategy factory that accepts `label_scope` and
  `registry` receives those kwargs from `strategy_for_transport()`.

## Reference tests

- `tests/agents/test_register_agent_support.py` — API contract, isolation,
  same-transport coexistence, kwargs preservation, and parser-resolution paths.
- `tests/agents/test_add_a_new_agent_recipe.py` — headless end-to-end recipe.
- `tests/agents/test_add_a_new_interactive_agent_recipe.py` — interactive
  end-to-end recipe.
- `tests/test_opencode_session_execution_generic_execution_strategy.py` —
  behaviour preserved by `BaseExecutionStrategy` defaults.
