# Adding, Updating, and Removing Agent Support

This guide covers the workflows for managing agent support in Ralph. It describes how to register new agents, update existing ones, and remove agent definitions using the public catalog and registry APIs.

The main public entry point for agent registration is `register_agent_support` (defined in `ralph/agents/registration.py`). For advanced or test-specific scenarios, you can use the test-friendly seam `register_agent_support_to_catalog` or directly call `AgentCatalog.add`.

---

## Add a new agent

To add support for a new agent, register it using the `register_agent_support` function. This writes to the default catalog and configures the agent properties.

### Headless Agent Example

A headless agent runs non-interactively. You can register it by specifying the transport, parser factory, strategy factory, and an agent registry:

```python
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport


class MyParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str):
        from ralph.agents.parsers.agent_output_line import AgentOutputLine
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class MyStrategy(BaseExecutionStrategy):
    pass


my_registry = AgentRegistry()

register_agent_support(
    name="my-headless-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=MyParser,
    strategy_factory=MyStrategy,
    agent_registry=my_registry,
    cmd="my-agent-binary",
)
```

### Interactive Agent Example

An interactive agent requires a pseudo-terminal (PTY) to handle user interaction. Set `interactive=True` and use a PTY-capable transport:

```python
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport


class MyParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str):
        from ralph.agents.parsers.agent_output_line import AgentOutputLine
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class MyStrategy(BaseExecutionStrategy):
    pass


my_registry = AgentRegistry()

register_agent_support(
    name="my-interactive-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser_factory=MyParser,
    strategy_factory=MyStrategy,
    agent_registry=my_registry,
    interactive=True,
    cmd="my-agent-binary",
)
```

> [!NOTE]
> The six built-in agents (`claude`, `claude-headless`, `codex`, `opencode`, `nanocoder`, `agy`) are populated at module import time and live in the caller's `AgentRegistry` config, not the global `default_catalog()`. Custom agents registered via `register_agent_support` are added to the global `default_catalog()`.

---

## Update an existing agent

Updating an agent depends on whether you are modifying the global catalog or the caller's configuration registry.

### Updating the Catalog

Because `AgentCatalog.add` raises a `ValueError` on duplicate names, you must first remove the old definition before adding the updated one:

```python
from ralph.agents.catalog import default_catalog
from ralph.agents.registration import register_agent_support

# 1. Remove the old registration from the catalog
default_catalog().remove("my-agent")

# 2. Re-register the agent with new parameters
register_agent_support(
    name="my-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=new_parser,
    strategy_factory=new_strategy,
    agent_registry=my_registry,
)
```

### Updating the Caller's AgentRegistry

If you are updating the config-level registry (`AgentRegistry`), re-calling the `register` method will silently overwrite the existing configuration without raising an error:

```python
# The registry silently overwrites existing registrations
registry.register("my-agent", new_agent_config)
```

---

## Remove an agent

### Removing from the Catalog

To remove an agent from the global catalog, call the `remove` method on the default catalog:

```python
from ralph.agents.catalog import default_catalog

default_catalog().remove("my-agent")
```

### Removing from the AgentRegistry

`AgentRegistry` does not have an `unregister` method. To remove an agent from the registry, delete it directly from the mapping:

```python
# Remove a custom agent from the registry
del registry.agents["my-agent"]
```

> [!WARNING]
> The six built-in agents cannot be permanently removed from the caller's `AgentRegistry` because they are re-registered by `AgentRegistry.from_config` on every load.

---

## Common mistakes

* **Forgetting `interactive=True`**: Interactive agents require `interactive=True` to enable session continuation.
* **Not removing before re-registering**: Calling `default_catalog().add()` or `register_agent_support()` with a name that is already registered in the catalog will raise a `ValueError`.
* **Calling non-existent unregister methods**: `AgentRegistry` does not expose an `.unregister()` method; use `del registry.agents[name]` instead.
* **Adding a built-in agent name**: Attempting to register a custom agent under a built-in name can cause namespace clashes.

---

## See also

* [Architecture Documentation](../agents/architecture.md)
* [Registration Module](../../ralph/agents/registration.py)
* [Recipe Test (covers headless + interactive)](../../tests/agents/test_add_a_new_agent_recipe.py)
