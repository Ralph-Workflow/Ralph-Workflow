from __future__ import annotations

import contextlib
import os
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeGuard, cast, get_args, runtime_checkable

import sentry_sdk
import sentry_sdk.metrics as sentry_metrics
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.excepthook import ExcepthookIntegration

from ralph import __version__ as ralph_version
from ralph.config.agent_transport import AgentTransport
from ralph.platform.detection import current_platform
from ralph.policy.models._types import PhaseRole
from ralph.runtime._version_info import PythonVersionInfo
from ralph.runtime.environment import detect_runtime_environment
from ralph.telemetry._agent_config_payload import (
    AGENT_FAMILY_BY_TRANSPORT,
    build_agent_config_payload,
)
from ralph.telemetry._sentry_scrub import ScrubPolicy
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.agent_config import AgentConfig

_DSN: str = "https://418c4f0099a0db0987b420c3cd1d5bb0@o4511480216158208.ingest.de.sentry.io/4511480219959376"

_WINDOWS_DRIVE_LETTER_PREFIX_LEN: int = 3

_TRUE_DISABLE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})

_PHASE_ROLES: frozenset[str] = frozenset(cast("tuple[str, ...]", get_args(PhaseRole)))
_PHASE_OUTCOMES: frozenset[str] = frozenset({"success", "failure", "skipped", "crashed"})
_POLICY_SCHEMA_STATES: frozenset[str] = frozenset({"current", "outdated", "absent", "unknown"})
_SAFE_DRAIN_NAMES: frozenset[str] = frozenset(
    {
        "planning",
        "development",
        "development_analysis",
        "planning_analysis",
        "development_commit",
        "analysis",
        "commit",
        "policy_remediation",
        "policy_remediation_analysis",
    }
)
_AGENT_STATS_MAX_KEYS = 128


@dataclass
class TelemetryState:
    initialized: bool = False
    started_at: float | None = None
    started_at_utc: datetime | None = None
    outcome: str = "unknown"
    finalized: bool = False
    transaction: object | None = None
    wallclock_buckets: dict[str, object] | None = None
    phase_stats: dict[str, dict[str, object]] = field(default_factory=dict)
    agent_stats: dict[str, dict[str, object]] = field(default_factory=dict)
    home_prefix: str = field(default_factory=lambda: str(Path.home()))
    extra_scrub_prefixes: tuple[str, ...] = ()
    is_disabled: Callable[[], bool] | None = None
    is_disabled_by_config: Callable[[], bool] | None = None


_STATE = TelemetryState()


def telemetry_state() -> TelemetryState:
    return _STATE


def reset_telemetry_state() -> None:
    _STATE.initialized = False
    _STATE.started_at = None
    _STATE.started_at_utc = None
    _STATE.outcome = "unknown"
    _STATE.finalized = False
    _STATE.transaction = None
    _STATE.wallclock_buckets = None
    _STATE.phase_stats.clear()
    _STATE.agent_stats.clear()
    _STATE.home_prefix = str(Path.home())
    _STATE.extra_scrub_prefixes = ()
    _STATE.is_disabled = None
    _STATE.is_disabled_by_config = None


def current_scrub_policy() -> ScrubPolicy:
    return ScrubPolicy(
        home_prefix=_STATE.home_prefix,
        extra_prefixes=_STATE.extra_scrub_prefixes,
    )


@runtime_checkable
class _FinishableTransaction(Protocol):
    def finish(self) -> None: ...


class _BreadcrumbRecorder(Protocol):
    def __call__(
        self,
        *,
        category: str,
        message: str,
        level: str,
        data: dict[str, object],
    ) -> None: ...


class _MetricCounter(Protocol):
    def __call__(
        self,
        name: str,
        value: float,
        *,
        attributes: dict[str, object] | None = None,
    ) -> None: ...


class _MetricDistribution(Protocol):
    def __call__(
        self,
        name: str,
        value: float,
        *,
        unit: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None: ...


class _SentryInit(Protocol):
    def __call__(self, **kwargs: object) -> None: ...


class _SentrySetUser(Protocol):
    def __call__(self, user: dict[str, object] | None) -> None: ...


def _is_sentry_init(value: object) -> TypeGuard[_SentryInit]:
    return callable(value)


def _is_sentry_set_user(value: object) -> TypeGuard[_SentrySetUser]:
    return callable(value)


def is_telemetry_disabled(env: Mapping[str, str] | None = None) -> bool:
    mapping = env if env is not None else os.environ
    raw = mapping.get("RALPH_DISABLE_TELEMETRY")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUE_DISABLE_VALUES


def is_telemetry_disabled_by_config(env: Mapping[str, str] | None = None) -> bool:
    mapping = env if env is not None else os.environ
    xdg_config_home = mapping.get("XDG_CONFIG_HOME")
    global_config_path = (
        Path(xdg_config_home) / "ralph-workflow.toml"
        if xdg_config_home
        else Path.home() / ".config" / "ralph-workflow.toml"
    )
    return _config_file_disables_telemetry(global_config_path) or _local_config_disables_telemetry()


def _local_config_disables_telemetry() -> bool:
    nearest_config = _nearest_local_config_path()
    if nearest_config is not None and _config_file_disables_telemetry(nearest_config):
        return True
    with contextlib.suppress(Exception):
        scope = resolve_workspace_scope()
        if _config_file_disables_telemetry(scope.local_config_path):
            return True
        return any(_config_file_disables_telemetry(path) for path in scope.propagated_config_paths)
    return _config_file_disables_telemetry(Path(".agent") / "ralph-workflow.toml")


def _nearest_local_config_path() -> Path | None:
    with contextlib.suppress(OSError):
        current = Path.cwd().resolve()
        while True:
            candidate = current / ".agent" / "ralph-workflow.toml"
            if candidate.exists():
                return candidate
            parent = current.parent
            if parent == current:
                return None
            current = parent
    return None


def _config_file_disables_telemetry(config_path: Path) -> bool:
    try:
        if not config_path.exists():
            return False
        with config_path.open("rb") as fh:
            data = cast("dict[str, object]", tomllib.load(fh))
    except (OSError, ValueError):
        return True
    raw_general = data.get("general")
    if not isinstance(raw_general, dict):
        return False
    general = cast("dict[str, object]", raw_general)
    return general.get("telemetry_enabled") is False


def telemetry_is_inactive() -> bool:
    state = _STATE
    disabled = state.is_disabled if state.is_disabled is not None else is_telemetry_disabled
    disabled_by_config = (
        state.is_disabled_by_config
        if state.is_disabled_by_config is not None
        else is_telemetry_disabled_by_config
    )
    return disabled() or disabled_by_config() or not state.initialized


def is_telemetry_active() -> bool:
    return not telemetry_is_inactive()


def _sentry_environment() -> str:
    try:
        platform = current_platform()
    except Exception:
        return "default"
    return "ci" if platform.environment.ci else "default"


def _add_breadcrumb(*, category: str, message: str, data: Mapping[str, object]) -> None:
    with contextlib.suppress(Exception):
        add_breadcrumb = cast("_BreadcrumbRecorder", sentry_sdk.add_breadcrumb)
        payload: dict[str, object] = dict(data)
        add_breadcrumb(
            category=category,
            message=message,
            level="info",
            data=payload,
        )


def _metric_count(
    name: str,
    value: float,
    *,
    attributes: Mapping[str, object],
) -> None:
    with contextlib.suppress(Exception):
        count = cast("_MetricCounter", sentry_metrics.count)
        payload: dict[str, object] = dict(attributes)
        count(name, value, attributes=payload)


def _metric_distribution(
    name: str,
    value: float,
    *,
    unit: str,
    attributes: Mapping[str, object],
) -> None:
    with contextlib.suppress(Exception):
        distribution = cast("_MetricDistribution", sentry_metrics.distribution)
        payload: dict[str, object] = dict(attributes)
        distribution(name, value, unit=unit, attributes=payload)


def _scrub_string(value: str, policy: ScrubPolicy | None = None) -> str:
    active = policy if policy is not None else current_scrub_policy()
    if active.home_prefix and active.home_prefix in value:
        return value.replace(active.home_prefix, "~")
    for prefix in active.extra_prefixes:
        if prefix and prefix in value:
            return value.replace(prefix, "<redacted>")
    return value


def scrub_obj(obj: object, policy: ScrubPolicy | None = None) -> None:
    if isinstance(obj, dict):
        d = cast("dict[str, object]", obj)
        for key in list(d.keys()):
            val = d[key]
            if isinstance(val, str):
                d[key] = _scrub_string(val, policy)
            else:
                scrub_obj(val, policy)
    elif isinstance(obj, list):
        lst = cast("list[object]", obj)
        for i, item in enumerate(lst):
            if isinstance(item, str):
                lst[i] = _scrub_string(item, policy)
            else:
                scrub_obj(item, policy)


scrub_object = scrub_obj
_scrub_obj = scrub_obj


def _is_absolute_filename(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/"):
        return True
    if len(value) >= _WINDOWS_DRIVE_LETTER_PREFIX_LEN and value[1] == ":" and value[0].isalpha():
        rest = value[2]
        return rest in ("\\", "/")
    return value.startswith("\\\\")


def _is_path_like_filename(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return "/" in value or "\\" in value


def _basename_of_path_like(filename: str) -> str:
    if "\\" in filename:
        return filename.rsplit("\\", 1)[-1]
    return filename.rsplit("/", 1)[-1]


def _scrub_frames(frames: object) -> None:
    if not isinstance(frames, list):
        return
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        d_frame = cast("dict[str, object]", frame)
        d_frame.pop("abs_path", None)
        filename = d_frame.get("filename")
        if not _is_path_like_filename(filename):
            continue
        d_frame["filename"] = _basename_of_path_like(cast("str", filename))


def scrub_event(
    event: object,
    _hint: object = None,
    policy: ScrubPolicy | None = None,
) -> object:
    if isinstance(event, dict):
        d = cast("dict[str, object]", event)
        d.pop("server_name", None)
        exception = d.get("exception")
        if isinstance(exception, dict):
            values = cast("dict[str, object]", exception).get("values")
            if isinstance(values, list):
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    stacktrace = cast("dict[str, object]", value).get("stacktrace")
                    if isinstance(stacktrace, dict):
                        _scrub_frames(cast("dict[str, object]", stacktrace).get("frames"))
    scrub_obj(event, policy)
    return event


_scrub_event = scrub_event


def _build_extra_scrub_prefixes() -> tuple[str, ...]:
    prefixes: list[str] = []
    try:
        cwd = str(Path.cwd())
    except OSError:
        cwd = ""
    if cwd and cwd not in prefixes:
        prefixes.append(cwd)
    for entry in sys.argv:
        if not isinstance(entry, str) or not entry:
            continue
        if not _is_absolute_filename(entry):
            continue
        if entry not in prefixes:
            prefixes.append(entry)
    return tuple(prefixes)


def _before_send(event: dict[str, object], _hint: dict[str, object]) -> dict[str, object] | None:
    scrub_event(event)
    return event


def init_sentry(user_id: str, session_id: str) -> None:
    _STATE.extra_scrub_prefixes = _build_extra_scrub_prefixes()

    sentry_init: object = sentry_sdk.init
    if not _is_sentry_init(sentry_init):
        raise TypeError("sentry_sdk.init is not callable")
    sentry_init(
        dsn=_DSN,
        send_default_pii=False,
        release=f"ralph-workflow@{ralph_version}",
        environment=_sentry_environment(),
        auto_session_tracking=True,
        send_client_reports=True,
        default_integrations=False,
        integrations=[ExcepthookIntegration(always_run=True), DedupeIntegration()],
        auto_enabling_integrations=False,
        traces_sample_rate=1.0,
        profiles_sample_rate=0.0,
        profile_session_sample_rate=0.0,
        enable_logs=False,
        include_local_variables=False,
        before_send=_before_send,
        before_send_transaction=_before_send,
    )
    set_user: object = sentry_sdk.set_user
    if not _is_sentry_set_user(set_user):
        raise TypeError("sentry_sdk.set_user is not callable")
    set_user({"id": user_id})
    sentry_sdk.set_tag("session_id", session_id)
    _STATE.initialized = True


def set_environment_context() -> None:
    try:
        platform = current_platform()
        python_info = PythonVersionInfo.from_sys(sys)
        runtime_env = detect_runtime_environment()
        python_version = f"{python_info.major}.{python_info.minor}.{python_info.micro}"

        sentry_sdk.set_tag("os", platform.os.value)
        sentry_sdk.set_tag("architecture", platform.architecture.value)
        sentry_sdk.set_tag("python_version", python_version)
        sentry_sdk.set_tag("ralph_version", ralph_version)
        sentry_sdk.set_tag("ci", platform.environment.ci)
        sentry_sdk.set_tag("container", platform.environment.container)

        runtime_payload: dict[str, object] = {
            "python_implementation": python_info.implementation,
            "in_virtualenv": runtime_env.in_virtualenv,
            "environment_markers": platform.environment.markers(),
            "package_manager": platform.package_manager,
        }
        sentry_sdk.set_context("runtime", runtime_payload)
    except Exception:
        pass


def set_agent_config_context(agents: Mapping[str, AgentConfig]) -> None:
    if telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        payload = build_agent_config_payload(agents)
        sentry_sdk.set_context("agent_config", payload)
        sentry_sdk.set_tag("agent_count", payload.get("agent_count"))
        sentry_sdk.set_tag("agent_families", payload.get("agent_families"))


def set_policy_schema_context(state: str) -> None:
    if telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        safe_state = state if state in _POLICY_SCHEMA_STATES else "unknown"
        sentry_sdk.set_tag("policy_schema_state", safe_state)


def _python_version() -> str | None:
    try:
        info = PythonVersionInfo.from_sys(sys)
    except Exception:
        return None
    return f"{info.major}.{info.minor}.{info.micro}"


def _format_utc_timestamp(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record_session_start(
    now: float | None = None,
    now_dt: datetime | None = None,
) -> None:
    if now is None:
        now = time.monotonic()
    _STATE.started_at = float(now)
    _STATE.started_at_utc = now_dt if now_dt is not None else datetime.now(UTC)
    _STATE.finalized = False
    if telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        sentry_sdk.start_session(session_mode="application")
    with contextlib.suppress(Exception):
        _STATE.transaction = sentry_sdk.start_transaction(
            op="cli.run",
            name="ralph.session",
        )
    _add_breadcrumb(
        category="ralph.session",
        message="session start",
        data={"event": "start"},
    )
    with contextlib.suppress(Exception):
        sentry_sdk.capture_message("session start", level="info")


def set_session_outcome(outcome: str) -> None:
    _STATE.outcome = outcome


def record_command_invocation(command: str) -> None:
    if telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        sentry_sdk.set_tag("command", command)
        _metric_count("ralph.command", 1.0, attributes={"command": command})
        _add_breadcrumb(
            category="ralph.command",
            message="command invocation",
            data={"command": command},
        )


_WEEKDAY_COUNT = 5


def _compute_wallclock_buckets(dt: datetime) -> dict[str, object]:
    return {
        "hour_of_day": dt.hour,
        "day_of_week": dt.weekday(),
        "is_weekday": dt.weekday() < _WEEKDAY_COUNT,
    }


def set_session_wallclock_start(now_dt: datetime | None = None) -> None:
    if telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        dt = now_dt if now_dt is not None else datetime.now(UTC)
        _STATE.wallclock_buckets = _compute_wallclock_buckets(dt)


def record_phase_execution(*, role: str, duration_s: int, outcome: str) -> None:
    if telemetry_is_inactive():
        return
    if role not in _PHASE_ROLES or outcome not in _PHASE_OUTCOMES:
        return
    with contextlib.suppress(Exception):
        slot = _STATE.phase_stats.get(role)
        if slot is None:
            slot = {
                "count": 0,
                "total_duration_s": 0,
                "outcomes": dict.fromkeys(_PHASE_OUTCOMES, 0),
            }
            _STATE.phase_stats[role] = slot
        current_count_raw = slot.get("count", 0)
        current_total_raw = slot.get("total_duration_s", 0)
        outcomes_map_raw = slot.get("outcomes")
        if (
            not isinstance(current_count_raw, int)
            or not isinstance(current_total_raw, int)
            or not isinstance(outcomes_map_raw, dict)
        ):
            return
        current_count: int = current_count_raw
        current_total: int = current_total_raw
        outcomes_map: dict[str, int] = outcomes_map_raw
        delta = int(duration_s)
        slot["count"] = current_count + 1
        slot["total_duration_s"] = current_total + delta
        outcomes_map[outcome] = outcomes_map.get(outcome, 0) + 1
        attributes = {"role": role, "outcome": outcome}
        _metric_count("ralph.phase", 1.0, attributes=attributes)
        _metric_distribution(
            "ralph.phase.duration",
            float(delta),
            unit="second",
            attributes=attributes,
        )
        _add_breadcrumb(
            category="ralph.phase",
            message="phase execution",
            data=attributes,
        )


def record_agent_invocation(
    *,
    transport: AgentTransport | str,
    phase_role: str,
    drain: str | None,
    drain_class: str | None,
    pipeline_profile: str,
    duration_s: float,
    outcome: str,
) -> None:
    if telemetry_is_inactive():
        return
    transport_value = transport.value if isinstance(transport, AgentTransport) else str(transport)
    attributes: dict[str, object] = {
        "agent_family": AGENT_FAMILY_BY_TRANSPORT.get(transport_value, "custom"),
        "transport": transport_value if transport_value in AGENT_FAMILY_BY_TRANSPORT else "generic",
        "pipeline_profile": pipeline_profile
        if pipeline_profile in {"default", "custom"}
        else "custom",
        "phase_role": phase_role if phase_role in _PHASE_ROLES else "unknown",
        "drain": drain if drain in _SAFE_DRAIN_NAMES else "custom",
        "drain_class": drain_class if drain_class in _PHASE_ROLES else "unknown",
        "outcome": outcome
        if outcome in {"success", "failure", "interrupted", "crashed"}
        else "crashed",
    }
    with contextlib.suppress(Exception):
        _metric_count("ralph.agent.invocation", 1.0, attributes=attributes)
        _metric_distribution(
            "ralph.agent.duration",
            max(0.0, float(duration_s)),
            unit="second",
            attributes=attributes,
        )
        key = "|".join(str(attributes[field]) for field in attributes)
        if key not in _STATE.agent_stats and len(_STATE.agent_stats) >= _AGENT_STATS_MAX_KEYS:
            key = "overflow"
        slot = _STATE.agent_stats.setdefault(key, {"count": 0, "duration_s": 0.0})
        count_raw = slot.get("count", 0)
        duration_raw = slot.get("duration_s", 0.0)
        if not isinstance(count_raw, int) or not isinstance(duration_raw, float | int):
            return
        slot["count"] = count_raw + 1
        slot["duration_s"] = duration_raw + max(0.0, float(duration_s))
        _add_breadcrumb(
            category="ralph.agent",
            message="agent invocation",
            data=attributes,
        )


_HANDLED_FAILURE_ORIGINS: frozenset[str] = frozenset({"agent_invocation"})


def report_handled_exception(exc: BaseException, *, origin: str) -> None:
    if telemetry_is_inactive():
        return
    safe_origin = origin if origin in _HANDLED_FAILURE_ORIGINS else "unknown"
    with contextlib.suppress(Exception), sentry_sdk.new_scope() as scope:
        scope.set_tag("ralph.origin", safe_origin)
        scope.set_tag("ralph.handled", "true")
        sentry_sdk.capture_exception(exc)


def flush_telemetry(timeout: float = 2.0) -> None:
    with contextlib.suppress(Exception):
        sentry_sdk.flush(timeout=timeout)


def finalize_session(
    now: float | None = None,
    end_dt: datetime | None = None,
    flush_timeout: float = 2.0,
) -> float | None:
    if not _STATE.initialized or _STATE.started_at is None or _STATE.finalized:
        return None

    started = _STATE.started_at
    end_clock = time.monotonic() if now is None else float(now)
    duration = max(0.0, end_clock - started)
    ended_at_utc = end_dt if end_dt is not None else datetime.now(UTC)
    _STATE.finalized = True

    try:
        session_payload: dict[str, object] = {
            "duration_s": duration,
            "started_monotonic_s": started,
            "ended_monotonic_s": end_clock,
            "outcome": _STATE.outcome,
            "ralph_version": ralph_version,
        }
        python_version = _python_version()
        if python_version is not None:
            session_payload["python_version"] = python_version
        if _STATE.started_at_utc is not None:
            session_payload["started_at_utc"] = _format_utc_timestamp(_STATE.started_at_utc)
        session_payload["ended_at_utc"] = _format_utc_timestamp(ended_at_utc)
        if _STATE.wallclock_buckets is not None:
            session_payload["wallclock"] = dict(_STATE.wallclock_buckets)
        if _STATE.phase_stats:
            session_payload["phases"] = {
                role: dict(stats) for role, stats in _STATE.phase_stats.items()
            }
        if _STATE.agent_stats:
            session_payload["agent_invocations"] = {
                key: dict(stats) for key, stats in _STATE.agent_stats.items()
            }
        sentry_sdk.set_context("session", session_payload)
        attributes = {"outcome": _STATE.outcome}
        _metric_count("ralph.session", 1.0, attributes=attributes)
        _metric_distribution(
            "ralph.session.duration",
            duration,
            unit="second",
            attributes=attributes,
        )
        _add_breadcrumb(
            category="ralph.session",
            message="session end",
            data=attributes,
        )
        sentry_sdk.capture_message("session end", level="info")
        transaction = _STATE.transaction
        if isinstance(transaction, _FinishableTransaction):
            transaction.finish()
        sentry_sdk.end_session()
        flush_telemetry(flush_timeout)
    except Exception:
        pass
    _STATE.phase_stats.clear()
    _STATE.agent_stats.clear()
    _STATE.transaction = None
    return duration


__all__ = [
    "ScrubPolicy",
    "TelemetryState",
    "current_scrub_policy",
    "finalize_session",
    "flush_telemetry",
    "init_sentry",
    "is_telemetry_active",
    "is_telemetry_disabled",
    "is_telemetry_disabled_by_config",
    "record_agent_invocation",
    "record_command_invocation",
    "record_phase_execution",
    "record_session_start",
    "report_handled_exception",
    "reset_telemetry_state",
    "scrub_event",
    "scrub_obj",
    "scrub_object",
    "set_agent_config_context",
    "set_environment_context",
    "set_policy_schema_context",
    "set_session_outcome",
    "set_session_wallclock_start",
    "telemetry_is_inactive",
    "telemetry_state",
]
