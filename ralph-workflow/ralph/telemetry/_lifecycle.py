"""The injectable telemetry lifecycle the CLI wires at its chokepoints.

The CLI reaches Sentry through exactly three seams: the session bootstrap
(:func:`ralph.cli.main._init_telemetry`), the command-invocation tag
(:func:`ralph.cli.main._record_cli_command`), and the pipeline-outcome
verdict (:func:`ralph.cli.main._run_pipeline`). Each of them used to
import the telemetry module directly, which made the wiring untestable
without rebinding module attributes.

:class:`TelemetryLifecycle` is the collaborator those seams now take.
Every field is a plain callable, so a caller can inject recording fakes
and assert on the recorded calls instead of patching production
internals. :func:`default_telemetry_lifecycle` reproduces the production
wiring exactly, so the default behaviour is unchanged.

The Sentry-backed telemetry modules are imported at module import time
(``ralph.telemetry`` itself imports nothing, so there is no cycle), while
the collaborators are read from those modules when
:func:`default_telemetry_lifecycle` is called — a caller that patches
``ralph.telemetry._sentry`` before the call still gets the patched
callable.
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.telemetry import _sentry, _user_identity

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.agent_config import AgentConfig


def _register_exit_handler(handler: Callable[[], None]) -> object:
    """Register the session finalizer with the interpreter exit hook."""
    atexit.register(handler)
    return None


@dataclass(frozen=True)
class TelemetryLifecycle:
    """The telemetry collaborators the CLI reaches for.

    Attributes:
        is_disabled: True when ``RALPH_DISABLE_TELEMETRY`` opts out.
        is_disabled_by_config: True when a global or project-local
            configuration file opts out.
        is_active: True when telemetry is enabled AND Sentry was
            initialized (i.e. an event would actually be forwarded).
        get_user_id: Return the persistent anonymous user id.
        generate_session_id: Return a fresh session id.
        init_sentry: Initialize the Sentry client.
        set_environment_context: Attach the anonymous runtime context.
        record_session_start: Record the session start.
        set_session_wallclock_start: Capture the coarse wallclock buckets.
        record_command_invocation: Record the invoked CLI command.
        set_session_outcome: Record the coarse session outcome.
        set_agent_config_context: Attach the agent-configuration snapshot.
        set_policy_schema_context: Attach the policy-schema state.
        finalize_session: Emit the session-end context and flush.
        register_exit_handler: Register the session finalizer for exit.
    """

    is_disabled: Callable[[], bool]
    is_disabled_by_config: Callable[[], bool]
    is_active: Callable[[], bool]
    get_user_id: Callable[[], str]
    generate_session_id: Callable[[], str]
    init_sentry: Callable[[str, str], None]
    set_environment_context: Callable[[], None]
    record_session_start: Callable[[], None]
    set_session_wallclock_start: Callable[[], None]
    record_command_invocation: Callable[[str], None]
    set_session_outcome: Callable[[str], None]
    set_agent_config_context: Callable[[Mapping[str, AgentConfig]], None]
    set_policy_schema_context: Callable[[str], None]
    finalize_session: Callable[[], object]
    register_exit_handler: Callable[[Callable[[], None]], object]


def default_telemetry_lifecycle() -> TelemetryLifecycle:
    """Return the production lifecycle (the real Sentry-backed sink).

    The collaborators are resolved as module attributes at call time, so
    the returned lifecycle always reflects the current state of
    ``ralph.telemetry._sentry`` and ``ralph.telemetry._user_identity``.
    """
    return TelemetryLifecycle(
        is_disabled=_sentry.is_telemetry_disabled,
        is_disabled_by_config=_sentry.is_telemetry_disabled_by_config,
        is_active=_sentry.is_telemetry_active,
        get_user_id=_user_identity.get_or_create_user_id,
        generate_session_id=_user_identity.generate_session_id,
        init_sentry=_sentry.init_sentry,
        set_environment_context=_sentry.set_environment_context,
        record_session_start=_sentry.record_session_start,
        set_session_wallclock_start=_sentry.set_session_wallclock_start,
        record_command_invocation=_sentry.record_command_invocation,
        set_session_outcome=_sentry.set_session_outcome,
        set_agent_config_context=_sentry.set_agent_config_context,
        set_policy_schema_context=_sentry.set_policy_schema_context,
        finalize_session=_sentry.finalize_session,
        register_exit_handler=_register_exit_handler,
    )
