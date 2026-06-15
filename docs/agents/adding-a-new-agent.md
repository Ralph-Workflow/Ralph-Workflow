# Adding a new agent

Adding a new agent to Ralph Workflow is a single registration call. The
infrastructure is built on three shared seams:

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

## Reference tests

- `tests/agents/test_register_agent_support.py` — API contract and isolation.
- `tests/agents/test_add_a_new_agent_recipe.py` — headless end-to-end recipe.
- `tests/agents/test_add_a_new_interactive_agent_recipe.py` — interactive
  end-to-end recipe.
- `tests/test_opencode_session_execution_generic_execution_strategy.py` —
  behaviour preserved by `BaseExecutionStrategy` defaults.
