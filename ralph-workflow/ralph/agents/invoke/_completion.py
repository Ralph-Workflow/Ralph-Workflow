from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.invoke._agy_incomplete_exit_error import raise_missing_completion_evidence
from ralph.agents.invoke._completion_evidence import (
    apply_sentinel_signal,
    completion_run_id_from_extra_env,
)
from ralph.agents.invoke._completion_exit_logging import (
    _extract_rejected_session_id_from_failure,
    log_invocation_exit,
)
from ralph.agents.invoke._completion_exit_status import (
    _MAX_STDERR_CAPTURE_BYTES,
    _truncation_marker,
    read_bounded_stderr,
    terminal_returncode,
)
from ralph.agents.invoke._completion_failures import (
    _handle_non_zero_returncode,
    _has_interactive_completion_bypass,
    _raise_if_broken_agent_exit,
    _record_exit_termination,
)
from ralph.agents.invoke._completion_options import CompletionCheckOptions, _completion_run_id
from ralph.agents.invoke._completion_supervision import (
    _teardown_subtree_if_pid_available,
    _wait_for_completion_grace,
    _wait_for_descendants_then_recheck,
)
from ralph.agents.invoke._completion_termination import (
    TerminationRecorder,
    attach_attempt_facts,
    attempt_facts_for_failure,
    attempt_facts_from_exception,
    observed_returncode,
    termination_issuer_for_exception,
    termination_issuer_for_teardown_issuer,
)
from ralph.agents.invoke._completion_verdicts import (
    _accepts_implicit_development_exit,
    _raise_if_pi_reported_failed_exit,
    _raise_quota_unless_agy_completed,
    _recorded_issuer_value,
    _resolved_session_id,
)
from ralph.agents.invoke._errors import OpenCodeResumableExitError
from ralph.agents.invoke._fire_reason_issuer import termination_issuer_for_fire_reason
from ralph.agents.invoke._session import _bounded_output_lines
from ralph.agents.registry import lookup_empty_output_diagnostic_factory
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer

if TYPE_CHECKING:
    from ralph.agents.timeout_clock import Clock
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess
    from ralph.process.teardown import ProcessTeardown

_TerminationRecorder = TerminationRecorder

__all__ = (
    "_MAX_STDERR_CAPTURE_BYTES",
    "CompletionCheckOptions",
    "TerminationRecorder",
    "_TerminationRecorder",
    "_extract_rejected_session_id_from_failure",
    "_truncation_marker",
    "_wait_for_descendants_then_recheck",
    "attach_attempt_facts",
    "attempt_facts_for_failure",
    "attempt_facts_from_exception",
    "check_process_result",
    "completion_run_id_from_extra_env",
    "log_invocation_exit",
    "observed_returncode",
    "read_bounded_stderr",
    "termination_issuer_for_exception",
    "termination_issuer_for_fire_reason",
    "termination_issuer_for_teardown_issuer",
)


def check_process_result(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None = None,
    check_options: CompletionCheckOptions | None = None,
    *,
    _clock: Clock | None = None,
    process_teardown: ProcessTeardown | None = None,
    termination_recorder: TerminationRecorder | None = None,
) -> None:
    try:
        _check_process_result(
            handle,
            agent_name,
            parsed_output,
            check_options,
            _clock=_clock,
            process_teardown=process_teardown,
            termination_recorder=termination_recorder,
        )
    except BaseException as exc:
        if termination_recorder is not None:
            attach_attempt_facts(exc, termination_recorder)
        raise


def _check_process_result(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None = None,
    check_options: CompletionCheckOptions | None = None,
    *,
    _clock: Clock | None = None,
    process_teardown: ProcessTeardown | None = None,
    termination_recorder: TerminationRecorder | None = None,
) -> None:
    if _has_interactive_completion_bypass(handle, parsed_output, check_options):
        _record_exit_termination(termination_recorder, handle, None)
        return

    returncode = terminal_returncode(handle, agent_name=agent_name)
    stderr_text = read_bounded_stderr(handle)
    stderr_attr: object = getattr(handle, "stderr", None)
    stderr_available = stderr_attr is not None
    if returncode != 0:
        _handle_non_zero_returncode(
            handle,
            agent_name,
            parsed_output,
            check_options,
            returncode,
            stderr_text,
            stderr_available,
            process_teardown,
            termination_recorder=termination_recorder,
        )
        return

    _record_exit_termination(termination_recorder, handle, returncode)

    _raise_quota_unless_agy_completed(
        agent_name,
        stderr_text,
        parsed_output,
        check_options,
    )

    opts = check_options
    if opts is not None and not opts.requires_completion_evidence:
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=opts.explicit_completion_seen,
        )
        _raise_if_pi_reported_failed_exit(
            handle,
            agent_name,
            bounded_output,
            process_teardown,
        )
        return
    if (
        opts is not None
        and opts.execution_strategy is not None
        and opts.execution_strategy.supports_session_continuation()
        and opts.workspace_path is not None
    ):
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=opts.explicit_completion_seen,
        )
        _eval_fn = (
            opts.evaluate_completion_fn
            if opts.evaluate_completion_fn is not None
            else evaluate_completion
        )
        signals = _eval_fn(
            opts.workspace_path,
            bounded_output,
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
        )
        signals = apply_sentinel_signal(
            signals,
            opts,
            sentinel_run_id=_completion_run_id(opts),
        )
        exit_state = opts.execution_strategy.classify_exit(
            handle, signals, liveness_probe=opts.liveness_probe
        )
        descendants_still_active = False

        if _accepts_implicit_development_exit(
            handle,
            agent_name,
            bounded_output,
            opts,
            exit_state,
            process_teardown,
        ):
            return

        if (
            exit_state == AgentExecutionState.RESUMABLE_CONTINUE
            and not opts.activity_only_supervision
        ):
            exit_state = _wait_for_completion_grace(
                handle,
                agent_name,
                opts,
                bounded_output,
                clock=_clock,
                teardown=process_teardown,
                termination_recorder=termination_recorder,
            )

        if (
            exit_state == AgentExecutionState.WAITING_ON_CHILD
            and not opts.activity_only_supervision
        ):
            exit_state = _wait_for_descendants_then_recheck(
                handle,
                agent_name,
                opts,
                bounded_output,
                clock=_clock,
                teardown=process_teardown,
                termination_recorder=termination_recorder,
            )
            descendants_still_active = (
                opts.execution_strategy.classify_exit(
                    handle,
                    signals,
                    liveness_probe=opts.liveness_probe,
                )
                == AgentExecutionState.WAITING_ON_CHILD
            )

        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            if _accepts_implicit_development_exit(
                handle,
                agent_name,
                bounded_output,
                opts,
                exit_state,
                process_teardown,
                waited_on_child=descendants_still_active,
            ):
                return
            _raise_if_broken_agent_exit(
                handle,
                agent_name,
                bounded_output,
                opts,
                stderr_text=stderr_text,
                teardown=process_teardown,
            )
            session_id = _resolved_session_id(opts, bounded_output)
            _raise_if_pi_reported_failed_exit(
                handle,
                agent_name,
                bounded_output,
                process_teardown,
            )
            raise OpenCodeResumableExitError(
                agent_name,
                session_id=session_id,
                last_observed_tool_call=opts.last_observed_tool_call,
                last_evidence_summary=opts.last_evidence_summary,
                elapsed_seconds=opts.elapsed_seconds,
                transcript_tail=opts.transcript_tail,
                returncode=observed_returncode(handle) or 0,
                issuer=_recorded_issuer_value(termination_recorder),
            )
    elif (
        opts is not None
        and opts.execution_strategy is not None
        and opts.execution_strategy.supports_completion_enforcement()
        and opts.workspace_path is not None
    ):
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=opts.explicit_completion_seen,
        )
        _eval_fn = (
            opts.evaluate_completion_fn
            if opts.evaluate_completion_fn is not None
            else evaluate_completion
        )
        signals = _eval_fn(
            opts.workspace_path,
            bounded_output,
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
        )
        signals = apply_sentinel_signal(
            signals,
            opts,
            sentinel_run_id=_completion_run_id(opts),
        )
        exit_state = opts.execution_strategy.classify_exit(
            handle, signals, liveness_probe=opts.liveness_probe
        )
        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            if _accepts_implicit_development_exit(
                handle,
                agent_name,
                bounded_output,
                opts,
                exit_state,
                process_teardown,
            ):
                return
            _teardown_subtree_if_pid_available(
                handle,
                issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
                teardown=process_teardown,
            )
            diagnostic_factory = lookup_empty_output_diagnostic_factory(agent_name)
            diagnostic = (
                diagnostic_factory(bounded_output, opts.agy_cli_log_path)
                if diagnostic_factory is not None
                else None
            )
            raise_missing_completion_evidence(
                agent_name,
                opts.execution_strategy,
                diagnostic,
            )
