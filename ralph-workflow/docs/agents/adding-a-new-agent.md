# Adding, Updating, and Removing Agent Support

This guide covers the workflows for managing agent support in Ralph. It describes how to register new agents, update existing ones, and remove agent definitions using the public catalog and registry APIs.

The main public entry point for agent registration is [register_agent_support](file:///Volumes/Crucial%20X9/ext-Projects/Ralph-Workflow/wt-016-consolidate-agent/ralph-workflow/ralph/agents/registration.py). For advanced or test-specific scenarios, you can use the test-friendly seam [register_agent_support_to_catalog](file:///Volumes/Crucial%20X9/ext-Projects/Ralph-Workflow/wt-016-consolidate-agent/ralph-workflow/ralph/agents/registration.py) or directly call [AgentCatalog.add](file:///Volumes/Crucial%20X9/ext-Projects/Ralph-Workflow/wt-016-consolidate-agent/ralph-workflow/ralph/agents/catalog.py).

---

## Add a new agent

To add support for a new agent, register it using the `register_agent_support` function. This writes to the default catalog and configures the agent properties.

### Headless Agent Example

A headless agent runs non-interactively. You can register it by specifying the parser factory and strategy factory:

```python
from ralph.agents.registration import register_agent_support
from ralph.agents.spec import AgentSpec
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

register_agent_support(
    name="my-headless-agent",
    spec=AgentSpec(
        name="my-headless-agent",
        interactive=False,
        requires_pty=False,
        transport=AgentTransport.GENERIC,
    ),
    parser_factory=MyParser,
    strategy_factory=MyStrategy,
    config=AgentConfig(
        cmd="my-agent-binary",
        transport=AgentTransport.GENERIC,
    ),
)
```

### Interactive Agent Example

An interactive agent requires a pseudo-terminal (PTY) to handle user interaction:

```python
from ralph.agents.registration import register_agent_support
from ralph.agents.spec import AgentSpec
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

register_agent_support(
    name="my-interactive-agent",
    spec=AgentSpec(
        name="my-interactive-agent",
        interactive=True,
        requires_pty=True,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    ),
    parser_factory=MyParser,
    strategy_factory=MyStrategy,
    config=AgentConfig(
        cmd="my-agent-binary",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    ),
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
    spec=new_spec,
    parser_factory=new_parser,
    strategy_factory=new_strategy,
    config=new_config,
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

* **Forgetting `interactive=True`**: Setting `requires_pty=True` in `AgentSpec` without setting `interactive=True` will raise a `ValueError`.
* **Not removing before re-registering**: Calling `default_catalog().add()` or `register_agent_support()` with a name that is already registered in the catalog will raise a `ValueError`.
* **Calling non-existent unregister methods**: `AgentRegistry` does not expose an `.unregister()` method; use `del registry.agents[name]` instead.
* **Adding a built-in agent name**: Attempting to register a custom agent under a built-in name can cause namespace clashes.

---

## See also

* [Architecture Documentation](file:///Volumes/Crucial%20X9/ext-Projects/Ralph-Workflow/wt-016-consolidate-agent/ralph-workflow/docs/agents/architecture.md)
* [Registration Module](file:///Volumes/Crucial%20X9/ext-Projects/Ralph-Workflow/wt-016-consolidate-agent/ralph-workflow/ralph/agents/registration.py)
* [Headless Recipe Test](file:///Volumes/Crucial%20X9/ext-Projects/Ralph-Workflow/wt-016-consolidate-agent/ralph-workflow/tests/agents/test_add_a_new_agent_recipe.py)
* [Interactive Recipe Test](file:///Volumes/Crucial%20X9/ext-Projects/Ralph-Workflow/wt-016-consolidate-agent/ralph-workflow/tests/agents/test_add_a_new_interactive_agent_recipe.py)
