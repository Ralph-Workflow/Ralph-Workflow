from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.telemetry import _sentry, _user_identity

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.agent_config import AgentConfig


def _register_exit_handler(handler: Callable[[], None]) -> object:
    atexit.register(handler)
    return None


@dataclass(frozen=True)
class TelemetryLifecycle:
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
