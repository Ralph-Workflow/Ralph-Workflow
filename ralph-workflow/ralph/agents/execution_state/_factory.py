"""Transport-keyed strategy factory.

To add a new transport, append one entry to the default catalog's
``_DEFAULT_STRATEGIES`` table (in ``ralph.agents.catalog``); the factory
itself never needs editing.
"""

from __future__ import annotations

import json
import sys
import types
from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal

from ._completion_mixin import CompletionEnforcingStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.agents._contracts import StrategyFactory
    from ralph.agents.catalog import _ParserRegistryEntry
    from ralph.config.enums import AgentTransport
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.monitor import SubagentPidSource

    from ._base import BaseExecutionStrategy


def _make_opencode_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    """Forward transport kwargs to the OpenCode strategy constructor."""
    del _kwargs
    return OpenCodeExecutionStrategy(
        label_scope=label_scope,
        registry=registry,
        subagent_pid_source=subagent_pid_source,
    )


def _make_agy_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    """Factory for AGY strategy: CompletionEnforcingStrategy wrapping GenericExecutionStrategy.

    Uses inheritance (not composition) because CompletionEnforcingStrategy is a mixin
    that requires being inherited to properly initialize via MRO. Pure composition
    via CompletionEnforcingStrategy(GenericExecutionStrategy(...)) fails because
    the mixin has no __init__ that accepts an argument.
    """

    class AgyExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        pass

    return AgyExecutionStrategy(
        label_scope=label_scope,
        registry=registry,
        subagent_pid_source=subagent_pid_source,
    )


def _make_cursor_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    """Factory for Cursor strategy: CompletionEnforcingStrategy wrapping GenericExecutionStrategy.

    Mirrors the AGY factory: uses inheritance (not composition) because
    :class:`CompletionEnforcingStrategy` is a mixin that requires being
    inherited to properly initialize via MRO.  The Cursor transport
    surfaces ``tool_call`` events as ``type='tool_use'`` and
    ``tool_result`` events with ``is_error=true`` as ``type='error'``
    via :class:`CursorParser`, so the strategy is a stock
    :class:`GenericExecutionStrategy` subclass that inherits the
    completion-enforcement contract from the AGY / Pi factories.
    """

    class CursorExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        pass

    return CursorExecutionStrategy(
        label_scope=label_scope,
        registry=registry,
        subagent_pid_source=subagent_pid_source,
    )


def _make_pi_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    """Factory for Pi's session-capable completion-enforcing strategy."""

    class PiExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
            signal = _classify_pi_activity(line)
            if signal is not None:
                return signal
            return super().classify_activity_line(line)

        def supports_session_continuation(self) -> bool:
            return True

    return PiExecutionStrategy(
        label_scope=label_scope,
        registry=registry,
        subagent_pid_source=subagent_pid_source,
    )


def _classify_pi_activity(line: str) -> AgentActivitySignal | None:
    """Classify pi.dev tool and error envelopes for watchdog control."""
    obj = _parse_json_object(line)
    if obj is None:
        return None
    event_type = obj.get("type")
    if event_type == "tool_execution_start":
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
    if event_type == "tool_execution_end" and obj.get("isError") is True:
        return AgentActivitySignal(
            AgentActivityKind.ERROR_LINE,
            raw=_pi_result_text(obj.get("result")) or "tool execution failed",
        )
    if event_type != "message_update":
        return None
    return _classify_pi_assistant_event(obj, line)


def _parse_json_object(line: str) -> dict[str, object] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed: object = json.loads(stripped, strict=False)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, object]", parsed)


def _classify_pi_assistant_event(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    assistant_event = obj.get("assistantMessageEvent")
    if not isinstance(assistant_event, dict):
        return None
    assistant_obj = cast("dict[str, object]", assistant_event)
    if assistant_obj.get("type") == "toolcall_end":
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
    if assistant_obj.get("type") == "error":
        reason = assistant_obj.get("reason", "error")
        return AgentActivitySignal(AgentActivityKind.ERROR_LINE, raw=str(reason))
    return None


def _pi_result_text(result: object) -> str:
    """Extract user-visible text from a pi.dev tool result payload."""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result) if result else ""
    content = result.get("content")
    if not isinstance(content, list):
        return str(result)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_dict = cast("dict[str, object]", block)
        if block_dict.get("type") != "text":
            continue
        text = block_dict.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


# Public read-only view over the default catalog's ``state.strategies`` dict.
#
# The catalog is the single source of truth for the transport-to-strategy
# dispatch table.  This view is created lazily (on first attribute access)
# to break the catalog <-> _factory import cycle: catalog.py imports the
# strategy classes from this module at module load time, so the
# ``__getattr__`` below defers the catalog import until first access.
#
# The variable is NOT defined at module level so that Python's
# module-attribute lookup falls through to ``__getattr__`` on first
# access.  Once created, the view is cached in ``globals()`` so
# subsequent accesses return the same object.


def __getattr__(name: str) -> object:
    if name == "_STRATEGY_DISPATCH":
        from ralph.agents.catalog import (  # noqa: PLC0415  # reason: lazy import breaks catalog<->_factory cycle
            default_catalog,
        )

        state_strategies: Mapping[AgentTransport, StrategyFactory] = cast(
            "Mapping[AgentTransport, StrategyFactory]",
            default_catalog()._state.strategies,
        )
        value: Mapping[AgentTransport, StrategyFactory] = types.MappingProxyType(state_strategies)
        globals()["_STRATEGY_DISPATCH"] = value  # type: ignore[misc]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
        return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def _view(name: str) -> Mapping[AgentTransport, StrategyFactory]:
    """Module-attribute lookup that triggers ``__getattr__`` for the lazy view.

    Used by internal functions (``strategy_for_transport``,
    ``strategy_for_command``) that need to read the legacy module-level
    view at call time.
    """
    value: Mapping[AgentTransport, StrategyFactory] = getattr(sys.modules[__name__], name)
    return value


def strategy_for_transport(
    transport: object,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    """Return the appropriate ExecutionStrategy for an agent transport."""
    factory = _view("_STRATEGY_DISPATCH").get(
        cast("AgentTransport", transport), GenericExecutionStrategy
    )
    return factory(
        label_scope=label_scope,
        registry=registry,
        subagent_pid_source=subagent_pid_source,
    )


def strategy_for_command(
    cmd: str,
    transport: AgentTransport,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    """Return the execution strategy registered for ``cmd`` when one exists.

    Custom agents registered via ``register_agent_support()`` are keyed by
    their full executable command string.  When a matching entry exists and
    its registered transport matches ``transport``, its strategy factory is
    used; otherwise the transport-keyed fallback from
    :func:`strategy_for_transport` is used.
    """
    from ralph.agents.catalog import (  # noqa: PLC0415  # reason: lazy import breaks catalog<->_factory cycle
        _ParserRegistryEntry,
    )
    from ralph.agents.parsers import (  # noqa: PLC0415  # reason: lazy import breaks catalog<->_factory cycle
        _CUSTOM_COMMAND_REGISTRY,
    )

    command_lower = cmd.lower() if cmd else ""
    custom_entry = cast(
        "_ParserRegistryEntry | None",
        _CUSTOM_COMMAND_REGISTRY.get(command_lower),  # type: ignore[attr-defined]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
    )
    if isinstance(custom_entry, _ParserRegistryEntry) and custom_entry.transport == transport:
        return custom_entry.strategy_factory(
            label_scope=label_scope,
            registry=registry,
            subagent_pid_source=subagent_pid_source,
        )
    return strategy_for_transport(
        transport,
        label_scope=label_scope,
        registry=registry,
        subagent_pid_source=subagent_pid_source,
    )
