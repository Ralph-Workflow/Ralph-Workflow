from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.execution_state import is_prompt_echo_line
from ralph.agents.idle_watchdog._circumstantial_evidence import (
    is_structurally_small_bounded_output,
)
from ralph.agents.invoke._broken_agent_exit_error import BrokenAgentExitError
from ralph.agents.invoke._completion_exit_logging import log_invocation_exit
from ralph.agents.invoke._completion_exit_status import (
    credentials_failure_needs_broken_exit,
    looks_like_credentials_failure,
)
from ralph.agents.invoke._completion_supervision import _teardown_subtree_if_pid_available
from ralph.agents.invoke._completion_termination import (
    observed_returncode,
    termination_issuer_for_teardown_issuer,
)
from ralph.agents.invoke._completion_verdicts import (
    _agy_has_terminal_completion_evidence,
    _has_verified_completion,
    _raise_if_pi_reported_failed_exit,
)
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._quota_detection import raise_if_quota_exhausted
from ralph.agents.invoke._session import _bounded_output_lines
from ralph.agents.invoke.termination_recorder import return_code_may_be_a_signal
from ralph.process._agent_launch_error import AgentLaunchError
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.timeout_defaults import BROKEN_AGENT_OUTPUT_GRACE_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.invoke._completion_options import CompletionCheckOptions
    from ralph.agents.invoke._completion_termination import _TerminationRecorder
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess
    from ralph.process.teardown import ProcessTeardown


def _raise_if_broken_agent_exit(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    bounded_output: list[str],
    opts: CompletionCheckOptions,
    *,
    stderr_text: str = "",
    teardown: ProcessTeardown | None = None,
) -> None:
    returncode = int(handle.returncode or 0)
    credentials_marker_seen = looks_like_credentials_failure(stderr_text) or any(
        looks_like_credentials_failure(line) for line in bounded_output
    )
    if credentials_marker_seen and (
        not bounded_output or is_structurally_small_bounded_output(bounded_output)
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=opts.elapsed_seconds,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    if (
        bounded_output
        and opts.has_meaningful_output is False
        and is_structurally_small_bounded_output(bounded_output)
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_llm_activity",
            elapsed_seconds=opts.elapsed_seconds,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    if opts.elapsed_seconds is not None and not bounded_output:
        _teardown_subtree_if_pid_available(
            handle,
            issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=opts.elapsed_seconds,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    nonblank_output = [line for line in bounded_output if line.strip()]
    if nonblank_output and all(
        is_prompt_echo_line(line, opts.input_prompt) for line in nonblank_output
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="prompt_echo",
            returncode=returncode,
            stderr=stderr_text,
        )


def _has_interactive_completion_bypass(
    handle: ManagedProcess | ManagedPtyProcess,
    parsed_output: list[str] | None,
    check_options: CompletionCheckOptions | None,
) -> bool:
    if check_options is None:
        return False
    with contextlib.suppress(Exception):
        handle_terminal_reason = cast(
            "Callable[[], str | None] | None", getattr(handle, "original_terminal_reason", None)
        )
        if (
            handle_terminal_reason is not None
            and handle_terminal_reason() == "interactive_completion"
        ):
            bounded_output = _bounded_output_lines(
                parsed_output or [],
                explicit_completion_seen=bool(check_options.explicit_completion_seen),
            )
            return bool(_agy_has_terminal_completion_evidence(bounded_output, check_options))
    return False


def _handle_non_zero_returncode(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None,
    check_options: CompletionCheckOptions | None,
    returncode: int,
    stderr_text: str,
    stderr_available: bool,
    process_teardown: ProcessTeardown | None,
    *,
    termination_recorder: _TerminationRecorder | None = None,
) -> None:
    issuer_method = cast(
        "Callable[[], str | None] | None", getattr(handle, "termination_issuer", None)
    )
    issuer = issuer_method() if issuer_method is not None else None
    intentional = returncode in {-15, 143} and isinstance(issuer, str)
    if termination_recorder is not None:
        termination_recorder.record_observed_returncode(observed_returncode(handle))
        termination_recorder.record_reported_returncode(returncode)
        termination_recorder.record_issuer(
            termination_issuer_for_teardown_issuer(issuer)
            if return_code_may_be_a_signal(returncode)
            else TerminationIssuer.AGENT_EXIT
        )
    bounded_output = _bounded_output_lines(
        parsed_output or [],
        explicit_completion_seen=bool(check_options and check_options.explicit_completion_seen),
    )
    if _has_verified_completion(bounded_output, check_options):
        logger.info(
            "Agent {} exited with code {} after verified completion; accepting as success",
            agent_name,
            returncode,
        )
        _teardown_subtree_if_pid_available(
            handle,
            issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=process_teardown,
        )
        return
    raise_if_quota_exhausted(
        agent_name,
        stderr_text,
        parsed_output,
        include_output=True,
    )
    if not intentional:
        _raise_if_pi_reported_failed_exit(handle, agent_name, bounded_output, process_teardown)
    if credentials_failure_needs_broken_exit(
        stderr_text,
        check_options.elapsed_seconds if check_options is not None else None,
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=process_teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=check_options.elapsed_seconds if check_options is not None else None,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    stderr = stderr_text if stderr_available else ""
    exc = AgentInvocationError(
        agent_name,
        returncode,
        stderr,
        bounded_output,
        failure_origin=(
            "runtime_launch"
            if isinstance(handle, AgentLaunchError)
            else "intentional_termination"
            if intentional
            else "agent"
        ),
        issuer=issuer if intentional else None,
    )
    log_invocation_exit(exc)
    _teardown_subtree_if_pid_available(
        handle,
        issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
        teardown=process_teardown,
    )
    raise exc


def _record_exit_termination(
    recorder: _TerminationRecorder | None,
    handle: object,
    returncode: int | None,
) -> None:
    if recorder is None:
        return
    recorder.record_observed_returncode(observed_returncode(handle))
    recorder.record_reported_returncode(returncode)
    recorder.record_issuer(TerminationIssuer.AGENT_EXIT)
    recorder.record_liveness(False)


__all__ = [
    "_handle_non_zero_returncode",
    "_has_interactive_completion_bypass",
    "_raise_if_broken_agent_exit",
    "_record_exit_termination",
]
