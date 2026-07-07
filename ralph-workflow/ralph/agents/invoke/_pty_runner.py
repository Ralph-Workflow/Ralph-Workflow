"""Process spawning and line-yield helpers for PTY-based invocation."""

from __future__ import annotations

import contextlib
import sys
from collections import deque
from typing import TYPE_CHECKING, Protocol, TypeGuard, cast

from tqdm import tqdm

from ralph.agents.execution_state import GenericExecutionStrategy
from ralph.agents.idle_watchdog import PostExitVerdict, PostExitWatchdog, WatchdogFireReason
from ralph.agents.invoke._completion import (
    _check_process_result,
    _CompletionCheckOptions,
    completion_run_id_from_extra_env,
)
from ralph.agents.invoke._errors import (
    AgentInactivityTimeoutError,
    InactivityTimeoutOpts,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._process_reader import (
    _MAX_PARSED_OUTPUT_LINES,
    _agent_command_name,
    _collect_r7_diagnostic_fields,
    _is_resumable_fire_reason,
    _parent_broker_secret,
    _subprocess_env,
)
from ralph.agents.invoke._pty_extras import _PtyExtras
from ralph.agents.invoke._pty_helpers import _visible_tui_text
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._session import (
    _EXPLICIT_COMPLETION_MARKER,
    _bounded_output_lines,
    extract_transport_session_id_with_visible_tui,
)
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.manager import PtySpawnOptions, get_process_manager
from ralph.process.teardown import teardown_subtree

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke._agent_run_ctx import _AgentRunCtx


class _CompletionExitSentReader(Protocol):
    @property
    def completion_exit_sent(self) -> bool: ...


def _has_completion_exit_sent(reader: object) -> TypeGuard[_CompletionExitSentReader]:
    return hasattr(reader, "completion_exit_sent")


def _completion_exit_sent(reader: object) -> bool:
    return _has_completion_exit_sent(reader) and reader.completion_exit_sent


def run_pty_and_read_lines(
    cmd: list[str],
    ctx: _AgentRunCtx,
    extras: _PtyExtras | None = None,
) -> Iterator[str]:
    _extras = extras or _PtyExtras()
    expected_session_id = _extras.expected_session_id
    completion_run_id = completion_run_id_from_extra_env(ctx.extra_env)
    if _extras.stop_sentinel_path is not None:
        with contextlib.suppress(FileNotFoundError):
            _extras.stop_sentinel_path.unlink()
    clock: Clock = ctx.clock or SystemClock()
    handle = get_process_manager().spawn_pty(
        cmd,
        PtySpawnOptions(
            cwd=str(ctx.workspace_path) if ctx.workspace_path is not None else None,
            env=_subprocess_env(ctx.extra_env),
            label=f"invoke:{_agent_command_name(ctx.config)}",
        ),
    )
    strategy = ctx.execution_strategy or GenericExecutionStrategy()
    probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
    with handle:
        pty_reader = PtyLineReader(
            handle,
            _agent_command_name(ctx.config),
            ctx,
            clock,
            _extras,
        )
        lines_iter = pty_reader.read_lines()
        parsed_output: deque[str] = deque(maxlen=_MAX_PARSED_OUTPUT_LINES)
        explicit_completion_seen = False
        captured_session_id: str | None = None
        try:
            if ctx.show_progress:
                agent_name = _agent_command_name(ctx.config)
                progress_iter = cast(
                    "Iterator[str]",
                    tqdm(
                        lines_iter,
                        desc=f"[{agent_name}]",
                        unit="line",
                        leave=False,
                        file=sys.stdout,
                    ),
                )
                for line in progress_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                        or _EXPLICIT_COMPLETION_MARKER in _visible_tui_text(stripped_line)
                    )
                    session_id = extract_transport_session_id_with_visible_tui(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line
            else:
                for line in lines_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                        or _EXPLICIT_COMPLETION_MARKER in _visible_tui_text(stripped_line)
                    )
                    session_id = extract_transport_session_id_with_visible_tui(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line

            if captured_session_id is None:
                captured_session_id = expected_session_id

            if not _completion_exit_sent(pty_reader):
                post_exit = PostExitWatchdog(ctx.policy, clock)
                verdict = post_exit.wait_for_process_exit(lambda: handle.poll() is not None)
                if verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG:
                    handle.terminate(grace_period_s=0.5)
                    exit_pid = cast("int | None", getattr(handle, "pid", None))
                    if exit_pid is not None:
                        teardown_subtree(exit_pid)
                    raise _IdleStreamTimeoutError(
                        ctx.policy.process_exit_wait_seconds,
                        WatchdogFireReason.PROCESS_EXIT_HANG,
                    )
        except _IdleStreamTimeoutError as exc:
            session_resume_safe = _is_resumable_fire_reason(exc.reason)
            raise AgentInactivityTimeoutError(
                _agent_command_name(ctx.config),
                exc.timeout_seconds,
                _bounded_output_lines(
                    tuple(parsed_output),
                    explicit_completion_seen=explicit_completion_seen,
                ),
                InactivityTimeoutOpts(
                    reason=exc.reason,
                    session_resume_safe=session_resume_safe,
                    resumable_session_id=captured_session_id or expected_session_id,
                    diagnostic=exc.diagnostic,
                ),
            ) from exc

        # R7 (Trustworthy Idle Watchdog): populate the diagnostic
        # fields on ``_CompletionCheckOptions`` from the watchdog
        # state held on the PTY line reader (``pty_reader._watchdog``
        # was set at the start of ``read_lines()``). The helper at
        # ``_process_reader._collect_r7_diagnostic_fields`` extracts
        # the four fields into a tuple so this function stays under
        # the PLR0912 / PLR0915 branch / statement limits.
        (
            evidence_summary_str,
            last_tool_call_str,
            elapsed_value,
            transcript_tail,
        ) = _collect_r7_diagnostic_fields(
            reader=pty_reader,
            clock=clock,
            parsed_output=parsed_output,
        )

        _check_process_result(
            handle,
            _agent_command_name(ctx.config),
            list(parsed_output),
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=ctx.workspace_path,
                liveness_probe=probe,
                policy=ctx.policy,
                required_artifact=ctx.required_artifact,
                explicit_completion_seen=explicit_completion_seen,
                captured_session_id=captured_session_id,
                completion_run_id=completion_run_id,
                evaluate_completion_fn=ctx.evaluate_completion_fn,
                last_observed_tool_call=last_tool_call_str,
                last_evidence_summary=evidence_summary_str,
                elapsed_seconds=elapsed_value,
                transcript_tail=transcript_tail,
                sentinel_secret=_parent_broker_secret(),
                receipt_secret=_parent_broker_secret(),
            ),
            _clock=clock,
        )
