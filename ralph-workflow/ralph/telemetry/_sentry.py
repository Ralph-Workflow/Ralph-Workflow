"""Sentry initialization with privacy-compliant PII scrubbing.

Captures only anonymous metalevel telemetry: OS, architecture, runtime markers,
Python/Ralph version, virtualenv flag, session timing, and a coarse exit
outcome. The before_send scrubber redacts home-directory, cwd, and argv
prefixes and drops server_name + stack-frame abs_path so no codebase identity
leaves the process. RALPH_DISABLE_TELEMETRY=1 (or true/yes/on) or
``telemetry_enabled = false`` in ``ralph-workflow.toml`` skips initialization
at the single CLI chokepoint in ``_init_telemetry``.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, get_args, runtime_checkable

import sentry_sdk
import sentry_sdk.metrics as sentry_metrics

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
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.config.agent_config import AgentConfig

_DSN: str = "https://418c4f0099a0db0987b420c3cd1d5bb0@o4511480216158208.ingest.de.sentry.io/4511480219959376"
_HOME_PREFIX: str = str(Path.home())

# Minimum length to qualify as a Windows drive-letter path: ``X:\``.
# Avoids matching ``C:foo`` (no separator after the colon).
_WINDOWS_DRIVE_LETTER_PREFIX_LEN: int = 3

_TRUE_DISABLE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})

# Module-level mutable accumulator compliant: tuple (immutable). Populated by
# init_sentry from os.getcwd() and path-like sys.argv entries.
_EXTRA_SCRUB_PREFIXES: tuple[str, ...] = ()

# Module-level scalars only (no list/dict/set/deque). Populated by the
# lifecycle functions; consumed by finalize_session.
_SESSION_STARTED_AT: float | None = None
_SESSION_STARTED_AT_UTC: datetime | None = None
_SESSION_OUTCOME: str = "unknown"
_SESSION_FINALIZED: bool = False
_INITIALIZED: bool = False
_SESSION_TRANSACTION: object | None = None

# PhaseRole closed vocabulary — auto-derived from the Literal type alias via
# ``get_args`` so the validation set cannot drift from the type. Phase telemetry
# keys exclusively on these values; raw phase names are never forwarded.
_PHASE_ROLES: frozenset[str] = frozenset(cast("tuple[str, ...]", get_args(PhaseRole)))
_PHASE_OUTCOMES: frozenset[str] = frozenset({"success", "failure", "skipped", "crashed"})
# Mirrors ralph.project_policy.schema_state.POLICY_SCHEMA_STATES. Restated here
# rather than imported so telemetry keeps a dependency-free import graph (it is
# loaded on the pipeline-runner import path); test_telemetry_sentry.py pins the
# two vocabularies together so they cannot drift.
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

# Aggregate phase statistics across the whole pipeline run. Keyed by PhaseRole,
# drained at session finalize. Bounded by the 8-value closed vocabulary above
# (not by user input).
_PHASE_STATS: dict[str, dict[str, object]] = (
    {}
)  # bounded-accumulator-ok: bounded by PhaseRole vocabulary (8 values); drained at session finalize

# bounded-accumulator-ok: bounded by _AGENT_STATS_MAX_KEYS; drained at session finalize
_AGENT_STATS: dict[str, dict[str, object]] = {}

# Coarse UTC time-of-day buckets captured once at session start for aggregate
# ``when`` analytics. ``None`` until set_session_wallclock_start() runs; the
# session context only attaches ``wallclock`` when populated.
_SESSION_WALLCLOCK_BUCKETS: dict[str, object] | None = None


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


def is_telemetry_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when RALPH_DISABLE_TELEMETRY is set to a truthy value."""
    mapping = env if env is not None else os.environ
    raw = mapping.get("RALPH_DISABLE_TELEMETRY")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUE_DISABLE_VALUES


def is_telemetry_disabled_by_config(env: Mapping[str, str] | None = None) -> bool:
    """Return True when a global or project-local ralph-workflow.toml opts out."""
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


def _telemetry_is_inactive() -> bool:
    return is_telemetry_disabled() or is_telemetry_disabled_by_config() or not _INITIALIZED


def is_telemetry_active() -> bool:
    """Return True when telemetry is enabled AND Sentry was initialized.

    Lets a caller skip work whose ONLY purpose is to feed telemetry (e.g.
    reading the policy pack to derive its schema state) when nothing would be
    forwarded anyway.
    """
    return not _telemetry_is_inactive()


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


def _scrub_string(value: str) -> str:
    """Redact home, cwd, and argv prefixes from a string value.

    The first matching prefix wins (the order is _HOME_PREFIX first so that
    home directories nested inside argv/cwd prefixes still collapse to ``~``).
    Non-matching strings are returned verbatim.
    """
    if _HOME_PREFIX and _HOME_PREFIX in value:
        return value.replace(_HOME_PREFIX, "~")
    for prefix in _EXTRA_SCRUB_PREFIXES:
        if prefix and prefix in value:
            return value.replace(prefix, "<redacted>")
    return value


def _scrub_obj(obj: object) -> None:
    """Recursively replace home-directory and extra prefixes in all string values."""
    if isinstance(obj, dict):
        d = cast("dict[str, object]", obj)
        for key in list(d.keys()):
            val = d[key]
            if isinstance(val, str):
                d[key] = _scrub_string(val)
            else:
                _scrub_obj(val)
    elif isinstance(obj, list):
        lst = cast("list[object]", obj)
        for i, item in enumerate(lst):
            if isinstance(item, str):
                lst[i] = _scrub_string(item)
            else:
                _scrub_obj(item)


def _is_absolute_filename(value: object) -> bool:
    """Return True if ``value`` looks like an absolute filename on POSIX or Windows.

    POSIX: any path beginning with ``/``.
    Windows: drive-letter paths (``C:\\...`` or ``C:/...``) and UNC paths
    (starting with ``\\\\``). The cross-platform check is required so the
    scrubber redacts stack-frame basenames and argv prefixes on
    non-POSIX platforms too (AC-04 / AC-06).
    """
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/"):
        return True
    if len(value) >= _WINDOWS_DRIVE_LETTER_PREFIX_LEN and value[1] == ":" and value[0].isalpha():
        rest = value[2]
        return rest in ("\\", "/")
    return value.startswith("\\\\")


def _is_path_like_filename(value: object) -> bool:
    """Return True if ``value`` looks like a filename carrying a path separator.

    Relative paths like ``ralph/foo.py`` or ``src\\foo.py`` reveal codebase
    structure (module hierarchy, package layout) even without an absolute
    prefix, so the scrubber collapses them to their basename. Bare module
    names like ``foo.py`` (no separator) are NOT considered path-like and are
    left intact — they are too generic to identify a codebase.
    """
    if not isinstance(value, str) or not value:
        return False
    return "/" in value or "\\" in value


def _basename_of_path_like(filename: str) -> str:
    """Return the basename of a path-like filename (relative or absolute).

    Prefers ``\\`` as the separator when present so mixed Windows-style
    strings like ``a\\b/c.py`` collapse correctly to ``c.py``.
    """
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


def _scrub_event(event: object, _hint: object) -> object:
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
    _scrub_obj(event)
    return event


def _build_extra_scrub_prefixes() -> tuple[str, ...]:
    """Collect cwd + path-like sys.argv entries into an immutable tuple of prefixes.

    Non-path strings (e.g. flag values, prompts, inline arguments) are filtered
    out by requiring an absolute-path prefix and a non-empty value. Empty
    strings are dropped. Order is preserved (cwd first, then argv) for
    deterministic scrubber output. The check is cross-platform: POSIX
    absolute (``/...``) and Windows absolute (``C:\\...``, ``C:/...``,
    ``\\\\server\\share\\...``) prefixes are all eligible.
    """
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


def init_sentry(user_id: str, session_id: str) -> None:
    """Initialize Sentry with anonymous user identity and PII scrubbing."""
    global _EXTRA_SCRUB_PREFIXES, _INITIALIZED  # noqa: PLW0603
    _EXTRA_SCRUB_PREFIXES = _build_extra_scrub_prefixes()

    sentry_sdk.init(
        dsn=_DSN,
        send_default_pii=False,
        release=f"ralph-workflow@{ralph_version}",
        environment=_sentry_environment(),
        auto_session_tracking=True,
        send_client_reports=True,
        # Automatic integrations can add HTTP/subprocess spans containing
        # URLs, argv, cwd, or other non-metadata details. Keep tracing
        # limited to the manual ``ralph.session`` transaction below.
        default_integrations=False,
        auto_enabling_integrations=False,
        traces_sample_rate=1.0,
        # Profiling samples stack frames outside the event scrubber path, so
        # keep it disabled to preserve Ralph Workflow's metadata-only contract.
        profiles_sample_rate=0.0,
        profile_session_sample_rate=0.0,
        # Do not enable Sentry log capture by default: application logs can
        # contain prompts, paths, or model output. Ralph emits explicit
        # metadata-only breadcrumbs and metrics instead.
        enable_logs=False,
        # Disable local-variable capture on stack frames: the scrubber can
        # only redact known prefixes (home/cwd/argv) so a local like
        # ``inline_prompt`` could otherwise be forwarded verbatim. We want
        # stack frames (file/line/function) but never their locals.
        include_local_variables=False,
        before_send=_scrub_event,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        before_send_transaction=_scrub_event,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    )
    sentry_sdk.set_user({"id": user_id})  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    sentry_sdk.set_tag("session_id", session_id)
    _INITIALIZED = True


def set_environment_context() -> None:
    """Attach anonymous runtime/OS context to Sentry.

    Sources ONLY privacy-safe metadata: OS + architecture + environment
    markers + package_manager from ``current_platform()``, Python
    major.minor.micro + implementation from ``PythonVersionInfo.from_sys``,
    Ralph ``__version__``, and the boolean ``in_virtualenv`` flag from
    ``detect_runtime_environment``. NEVER reads or forwards
    ``sys.executable``, ``sys.prefix/base_prefix/exec_prefix``,
    ``virtualenv_path``, or any ``os.environ`` value.
    """
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
        # Telemetry must never break the host process.
        pass


def set_agent_config_context(agents: Mapping[str, AgentConfig]) -> None:
    """Attach the metadata-only agent-configuration snapshot to Sentry.

    Called once at config load so the payload rides on EVERY subsequent event —
    including crashes — rather than only a cleanly finalized session. The
    sanitization contract lives in ``_agent_config_payload``: user-authored
    agent names, raw ``cmd`` strings, and flag values never leave the process.
    No-op when telemetry is disabled or Sentry was never initialized.
    Fail-soft.
    """
    if _telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        payload = build_agent_config_payload(agents)
        sentry_sdk.set_context("agent_config", payload)
        sentry_sdk.set_tag("agent_count", payload.get("agent_count"))
        sentry_sdk.set_tag("agent_families", payload.get("agent_families"))


def set_policy_schema_context(state: str) -> None:
    """Attach the project's policy-schema state as a closed-vocabulary tag.

    The caller derives the state (see
    :func:`ralph.project_policy.schema_state.policy_schema_state`); telemetry
    stays a pure sink and never reaches into the policy layer. Values outside
    the closed vocabulary collapse to ``unknown`` rather than being forwarded.
    No-op when telemetry is disabled or Sentry was never initialized.
    Fail-soft.
    """
    if _telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        safe_state = state if state in _POLICY_SCHEMA_STATES else "unknown"
        sentry_sdk.set_tag("policy_schema_state", safe_state)


def _python_version() -> str | None:
    """Return the running ``major.minor.micro`` Python version, or None."""
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
    """Record the monotonic-clock session start time.

    Tests inject ``now=<float>`` for determinism (audit_test_policy forbids
    ``time.monotonic()`` in test files). Production code passes ``now=None``
    so the call resolves to ``time.monotonic()`` at runtime.
    """
    global _SESSION_FINALIZED, _SESSION_STARTED_AT, _SESSION_STARTED_AT_UTC, _SESSION_TRANSACTION  # noqa: PLW0603
    if now is None:
        now = time.monotonic()
    _SESSION_STARTED_AT = float(now)
    _SESSION_STARTED_AT_UTC = now_dt if now_dt is not None else datetime.now(UTC)
    _SESSION_FINALIZED = False
    if _telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        sentry_sdk.start_session(session_mode="application")
    with contextlib.suppress(Exception):
        _SESSION_TRANSACTION = sentry_sdk.start_transaction(
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
    """Record the coarse session outcome: success / failure / interrupted / unknown."""
    global _SESSION_OUTCOME  # noqa: PLW0603
    _SESSION_OUTCOME = outcome


def record_command_invocation(command: str) -> None:
    """Record the invoked CLI command as a privacy-safe closed-vocabulary tag.

    The closed-vocabulary guarantee is at the call site (the CLI passes
    ``ctx.invoked_subcommand`` or the literal ``'pipeline'``); this function
    forwards the value verbatim via ``sentry_sdk.set_tag('command', ...)``.
    No-op when telemetry is disabled or Sentry was never initialized.
    Fail-soft: any exception from ``sentry_sdk.set_tag`` is swallowed so the
    host CLI/pipeline is never broken by telemetry.
    """
    if _telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        sentry_sdk.set_tag("command", command)
        _metric_count("ralph.command", 1.0, attributes={"command": command})
        _add_breadcrumb(
            category="ralph.command",
            message="command invocation",
            data={"command": command},
        )


_WEEKDAY_COUNT = 5  # Mon-Fri inclusive; weekday cutoff for weekday/weekend bucketing.


def _compute_wallclock_buckets(dt: datetime) -> dict[str, object]:
    """Compute coarse UTC time-of-day buckets from a timezone-aware datetime."""
    return {
        "hour_of_day": dt.hour,
        "day_of_week": dt.weekday(),
        "is_weekday": dt.weekday() < _WEEKDAY_COUNT,
    }


def set_session_wallclock_start(now_dt: datetime | None = None) -> None:
    """Capture coarse UTC time-of-day buckets for aggregate when-analytics.

    No full timestamp or timezone string is forwarded: only ``hour_of_day``
    (0-23), ``day_of_week`` (0-6, Monday=0), and ``is_weekday`` (bool). The
    buckets are attached to the session context at ``finalize_session`` time.
    Tests inject ``now_dt=<datetime>`` for determinism; production code passes
    ``now_dt=None`` so the call resolves to ``datetime.now(timezone.utc)``.
    No-op when telemetry is disabled or Sentry was never initialized.
    Fail-soft.
    """
    global _SESSION_WALLCLOCK_BUCKETS  # noqa: PLW0603
    if _telemetry_is_inactive():
        return
    with contextlib.suppress(Exception):
        dt = now_dt if now_dt is not None else datetime.now(UTC)
        _SESSION_WALLCLOCK_BUCKETS = _compute_wallclock_buckets(dt)


def record_phase_execution(*, role: str, duration_s: int, outcome: str) -> None:
    """Record a completed phase execution aggregated by PhaseRole (closed vocabulary).

    The aggregate is flushed as part of the session context at finalize time
    (one snapshot per run, no per-phase ``capture_message`` events). Unknown
    roles or outcomes are silently dropped — this enforces the privacy
    invariant that no user-customizable phase identifier ever leaves the
    process. ``duration_s`` is a whole-second ``int`` matching the production
    ``PhaseTimingRecord.elapsed_seconds`` type.
    No-op when telemetry is disabled or Sentry was never initialized.
    Fail-soft.
    """
    if _telemetry_is_inactive():
        return
    if role not in _PHASE_ROLES or outcome not in _PHASE_OUTCOMES:
        return
    with contextlib.suppress(Exception):
        slot = _PHASE_STATS.get(role)
        if slot is None:
            slot = {
                "count": 0,
                "total_duration_s": 0,
                "outcomes": dict.fromkeys(_PHASE_OUTCOMES, 0),
            }
            _PHASE_STATS[role] = slot
        current_count_raw = slot.get("count", 0)
        current_total_raw = slot.get("total_duration_s", 0)
        outcomes_map_raw = slot.get("outcomes")
        if (
            not isinstance(current_count_raw, int)
            or not isinstance(current_total_raw, int)
            or not isinstance(outcomes_map_raw, dict)
        ):
            # Defensive: ignore malformed accumulator state rather than crash.
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
    """Record one logical agent invocation using only bounded dimensions.

    Agent names and custom policy identifiers are deliberately absent from the
    payload. The resolved transport, closed phase role, allowlisted bundled
    drain name, and custom/default pipeline profile provide useful attribution
    without forwarding user-authored labels.
    """
    if _telemetry_is_inactive():
        return
    transport_value = transport.value if isinstance(transport, AgentTransport) else str(transport)
    attributes: dict[str, object] = {
        "agent_family": AGENT_FAMILY_BY_TRANSPORT.get(transport_value, "custom"),
        "transport": transport_value if transport_value in AGENT_FAMILY_BY_TRANSPORT else "generic",
        "pipeline_profile": pipeline_profile if pipeline_profile in {"default", "custom"} else "custom",
        "phase_role": phase_role if phase_role in _PHASE_ROLES else "unknown",
        "drain": drain if drain in _SAFE_DRAIN_NAMES else "custom",
        "drain_class": drain_class if drain_class in _PHASE_ROLES else "unknown",
        "outcome": outcome if outcome in {"success", "failure", "interrupted", "crashed"} else "crashed",
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
        if key not in _AGENT_STATS and len(_AGENT_STATS) >= _AGENT_STATS_MAX_KEYS:
            key = "overflow"
        slot = _AGENT_STATS.setdefault(key, {"count": 0, "duration_s": 0.0})
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


def flush_telemetry(timeout: float = 2.0) -> None:
    """Bounded, fail-soft Sentry flush."""
    with contextlib.suppress(Exception):
        sentry_sdk.flush(timeout=timeout)


def finalize_session(
    now: float | None = None,
    end_dt: datetime | None = None,
    flush_timeout: float = 2.0,
) -> float | None:
    """Emit the session-end context + message and flush. Returns the duration in seconds.

    No-op (returns ``None``) when Sentry was never initialized or no session
    start was recorded — so tests that monkeypatch ``_init_telemetry`` to a
    no-op or skip the lifecycle do not flush real network I/O.

    The session context includes explicit start/end monotonic timing
    markers so the session timing payload is observable (matches the
    README's "Session timing (start, duration)" claim). These monotonic
    values are process-local: they are meaningful only inside this
    process instance and leak no real-world clock information.
    """
    global _SESSION_FINALIZED, _SESSION_TRANSACTION  # noqa: PLW0603
    if not _INITIALIZED or _SESSION_STARTED_AT is None or _SESSION_FINALIZED:
        return None

    started = _SESSION_STARTED_AT
    end_clock = time.monotonic() if now is None else float(now)
    duration = max(0.0, end_clock - started)
    ended_at_utc = end_dt if end_dt is not None else datetime.now(UTC)
    _SESSION_FINALIZED = True

    try:
        session_payload: dict[str, object] = {
            "duration_s": duration,
            "started_monotonic_s": started,
            "ended_monotonic_s": end_clock,
            "outcome": _SESSION_OUTCOME,
            # Ralph's version also ships as the Sentry release + a global tag;
            # restating it on the session makes the run self-describing when a
            # session is inspected in isolation.
            "ralph_version": ralph_version,
        }
        python_version = _python_version()
        if python_version is not None:
            session_payload["python_version"] = python_version
        if _SESSION_STARTED_AT_UTC is not None:
            session_payload["started_at_utc"] = _format_utc_timestamp(_SESSION_STARTED_AT_UTC)
        session_payload["ended_at_utc"] = _format_utc_timestamp(ended_at_utc)
        if _SESSION_WALLCLOCK_BUCKETS is not None:
            session_payload["wallclock"] = dict(_SESSION_WALLCLOCK_BUCKETS)
        if _PHASE_STATS:
            session_payload["phases"] = {
                role: dict(stats) for role, stats in _PHASE_STATS.items()
            }
        if _AGENT_STATS:
            session_payload["agent_invocations"] = {
                key: dict(stats) for key, stats in _AGENT_STATS.items()
            }
        sentry_sdk.set_context("session", session_payload)
        attributes = {"outcome": _SESSION_OUTCOME}
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
        transaction = _SESSION_TRANSACTION
        if isinstance(transaction, _FinishableTransaction):
            transaction.finish()
        sentry_sdk.end_session()
        flush_telemetry(flush_timeout)
    except Exception:
        pass
    # Drain aggregates AFTER the snapshot is sent so a subsequent session can
    # reuse the module-level accumulators safely (bounded-accumulator-ok).
    _PHASE_STATS.clear()
    _AGENT_STATS.clear()
    _SESSION_TRANSACTION = None
    return duration
