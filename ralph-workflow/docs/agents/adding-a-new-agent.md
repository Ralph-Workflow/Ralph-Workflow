# Adding, Updating, and Removing Agent Support

See also: [Agent Subsystem README](README.md) for the discoverable entry point.

This guide covers the workflows for managing agent support in Ralph. It describes how to register new agents, update existing ones, and remove agent definitions.

The single canonical entry point for agent registration is `register_agent_support` (defined in `ralph/agents/registration.py`). For the 90% case, prefer the **opinionated 5-line recipe** `register_my_agent` (also in `ralph/agents/registration.py`); it picks a transport-derived default strategy so an interactive caller can never accidentally register an interactive agent with `BaseExecutionStrategy`. Advanced scenarios may use `register_agent_support_to_catalog` (test-friendly) or `AgentCatalog.add` directly.

---

## Strategy selection by transport

The opinionated `register_my_agent` helper picks the right execution strategy
for you based on the transport, so the typical 5-line recipe is just:

<!-- BLACKBOX_RECIPE_START -->
```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-agent",
    transport=AgentTransport.GENERIC,
    parser=GenericParser,
    agent_registry=my_registry,
)
```
<!-- BLACKBOX_RECIPE_END -->

When `strategy` is omitted, the helper picks from this table so an interactive
caller can never accidentally register an interactive agent with
`BaseExecutionStrategy`:

| `AgentTransport`         | Default strategy class                          |
| ------------------------ | ----------------------------------------------- |
| `CLAUDE_INTERACTIVE`     | `ClaudeInteractiveExecutionStrategy`            |
| `AGY`                    | `_make_agy_strategy` (agy PTY strategy)         |
| `OPENCODE`               | `OpenCodeExecutionStrategy`                     |
| `CLAUDE`                 | `ClaudeExecutionStrategy`                       |
| `CODEX`                  | `GenericExecutionStrategy`                      |
| `NANOCODER`              | `GenericExecutionStrategy`                      |
| `PI`                     | `GenericExecutionStrategy`                      |
| `GENERIC`                | `GenericExecutionStrategy`                      |

For interactive agents (`interactive=True`) the helper also auto-applies
the `--resume {}` session template unless `no_default_session_flag=True` is
passed (agy does this). Pass an explicit `strategy=` to override the
transport-derived default; pass an explicit `session_flag=` to override the
auto-applied `--resume {}` template.

## Migrating from `register_agent_support` to `register_my_agent`

The long-form `register_agent_support` is unchanged and still supported for
all 14 kwargs.  For the 90% case, `register_my_agent` collapses the recipe to
3 common shapes:

### Headless agent (was 14 kwargs)

```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-headless-agent",
    transport=AgentTransport.GENERIC,
    parser=GenericParser,
    agent_registry=my_registry,
    cmd="my-agent-binary",
)
```

### Interactive agent (was 14 kwargs + risk of BaseExecutionStrategy)

```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-interactive-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser=ClaudeParser,
    agent_registry=my_registry,
    interactive=True,
    cmd="my-agent-binary",
)
```

### Interactive agent that opts out of the default `--resume {}` template (was the hidden `name != "agy"` special case)

```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-no-resume-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser=GenericParser,
    agent_registry=my_registry,
    interactive=True,
    no_default_session_flag=True,
)
```

The `cmd` kwarg defaults to `name`; only pass it when the executable
binary name differs from the agent name.

---

## Add a new agent

To add support for a new agent, register it using the `register_agent_support` function. This writes to the default catalog and configures the agent properties.

### Headless Agent Example

A headless agent runs non-interactively. You can register it by specifying the transport, parser factory, strategy factory, and an agent registry:

```python
from ralph.agents import register_agent_support
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.execution_state._base import BaseExecutionStrategy
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
from ralph.agents import register_agent_support
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.execution_state._base import BaseExecutionStrategy
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
> The seven built-in agents (`claude`, `claude-headless`, `codex`, `opencode`, `nanocoder`, `agy`, `pi`) come from `ralph/agents/builtin.py` via `builtin_supports()`. `AgentRegistry.from_config()` and `AgentRegistry(catalog=...)` both call `_seed_catalog_with_builtins` so the registry and the catalog stay in lockstep. The `default_catalog()` global is seeded only when `AgentRegistry.from_config()` runs; it is not auto-seeded at module import.

---

## Update an existing agent

Updating an agent is done by first unregistering the old definition and then re-registering it with the new parameters.

### Updating the Catalog and AgentRegistry

To update an agent atomically, call the `unregister` method on the registry (which removes it from both the registry and the catalog) and then re-call `register_agent_support`:

```python
from ralph.agents import register_agent_support
from ralph.config.enums import AgentTransport

# 1. Remove the old registration from the registry and catalog
my_registry.unregister("my-agent")

# 2. Re-register the agent with new parameters
register_agent_support(
    name="my-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=new_parser,
    strategy_factory=new_strategy,
    agent_registry=my_registry,
)
```

---

## Remove an agent

### Recommended Approach: unregister()

To remove an agent from both the configuration registry and the global catalog, call the `unregister` method on your `AgentRegistry` instance:

```python
# Unregisters the agent from both the registry and the catalog
registry.unregister("my-agent")
```

### Legacy Fallback

Alternatively, you can manually delete it from the registry's config mapping, but this does not clean up the catalog:

```python
# Legacy fallback (removes from registry configuration only):
del registry.agents["my-agent"]
```

> [!WARNING]
> The seven built-in agents cannot be permanently removed from the caller's `AgentRegistry` because they are re-registered by `AgentRegistry.from_config` on every load.

---

## Common mistakes

* **Forgetting `interactive=True`**: Interactive agents require `interactive=True` to enable session continuation.
* **Not unregistering before re-registering**: Calling `register_agent_support()` with a name that is already registered in the catalog will raise a `ValueError`. Always call `registry.unregister(name)` first.
* **Using legacy deletion**: Deleting via `del registry.agents[name]` is deprecated; use `registry.unregister(name)` to clean up both the registry and the catalog atomically.
* **Adding a built-in agent name**: Attempting to register a custom agent under a built-in name can cause namespace clashes.

---

## See also

* [Architecture Documentation](../agents/architecture.md)
* [Registration Module](../../ralph/agents/registration.py)
* [Recipe Test (covers headless + interactive)](../../tests/agents/test_add_a_new_agent_recipe.py)
