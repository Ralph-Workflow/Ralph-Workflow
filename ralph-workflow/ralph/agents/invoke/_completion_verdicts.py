from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.agents.completion_signals import completion_signals_terminal, evaluate_completion
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.invoke._completion_evidence import apply_sentinel_signal
from ralph.agents.invoke._completion_options import CompletionCheckOptions, _completion_run_id
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._implicit_completion import is_implicit_development_exit
from ralph.agents.invoke._pi_context_exhausted_exit_error import PiContextExhaustedExitError
from ralph.agents.invoke._pi_exit_signals import (
    has_pi_context_exhaustion_signal,
    pi_provider_failure_reason,
)
from ralph.agents.invoke._pi_provider_failure_exit_error import PiProviderFailureExitError
from ralph.agents.invoke._quota_detection import raise_if_quota_exhausted
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.invoke._session import (
    _bounded_output_lines,
    extract_transport_session_id,
    extract_transport_session_id_with_visible_tui,
)
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer

if TYPE_CHECKING:
    from ralph.agents.invoke._completion_termination import _TerminationRecorder
    from ralph.agents.invoke.attempt_supervision import AttemptSupervisor
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess


def _raise_if_pi_reported_failed_exit(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    output: list[str],
    *,
    supervisor: AttemptSupervisor,
) -> None:
    if has_pi_context_exhaustion_signal(agent_name, output):
        raise PiContextExhaustedExitError(agent_name)
    provider_failure = pi_provider_failure_reason(agent_name, output)
    if provider_failure is None:
        return
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN)
    raise PiProviderFailureExitError(agent_name, provider_failure)


def _raise_if_transport_reported_error(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    output: list[str],
) -> None:
    for line in output:
        try:
            event = cast("object", json.loads(line))
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and cast("dict[str, object]", event).get("type") == "error":
            raise AgentInvocationError(agent_name, int(handle.returncode or 0), "", output)


def _accepts_implicit_development_exit(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    output: list[str],
    opts: CompletionCheckOptions,
    exit_state: AgentExecutionState,
    *,
    supervisor: AttemptSupervisor,
    waited_on_child: bool = False,
) -> bool:
    if (
        waited_on_child
        or exit_state != AgentExecutionState.RESUMABLE_CONTINUE
        or not is_implicit_development_exit(opts.required_artifact)
    ):
        return False
    _raise_if_transport_reported_error(handle, agent_name, output)
    _raise_if_pi_reported_failed_exit(handle, agent_name, output, supervisor=supervisor)
    return True


def _resolved_session_id(opts: CompletionCheckOptions, output: list[str]) -> str | None:
    session_id = opts.captured_session_id or extract_transport_session_id(output)
    if session_id is not None or not output:
        return session_id
    return next(
        (
            candidate
            for line in output
            if (candidate := extract_transport_session_id_with_visible_tui(line)) is not None
        ),
        None,
    )


def _has_verified_completion(output: list[str], opts: CompletionCheckOptions | None) -> bool:
    if opts is None or opts.workspace_path is None:
        return False
    evaluate = opts.evaluate_completion_fn or evaluate_completion
    signals = evaluate(
        opts.workspace_path,
        output,
        required_artifact=opts.required_artifact,
        run_id=_completion_run_id(opts),
        sentinel_secret=opts.sentinel_secret,
        receipt_secret=opts.receipt_secret,
    )
    return completion_signals_terminal(
        apply_sentinel_signal(signals, opts, sentinel_run_id=_completion_run_id(opts))
    )


def _agy_has_terminal_completion_evidence(
    output: list[str], opts: CompletionCheckOptions | None
) -> bool:
    if (
        opts is None
        or opts.execution_strategy is None
        or not opts.execution_strategy.supports_incomplete_exit_reprompt()
    ):
        return False
    return _has_verified_completion(output, opts)


def _raise_quota_unless_agy_completed(
    agent_name: str,
    stderr_text: str,
    parsed_output: list[str] | None,
    opts: CompletionCheckOptions | None,
) -> None:
    try:
        raise_if_quota_exhausted(
            agent_name,
            stderr_text,
            parsed_output,
            include_output=False,
        )
    except QuotaExhaustedError:
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=bool(opts and opts.explicit_completion_seen),
        )
        if not _agy_has_terminal_completion_evidence(bounded_output, opts):
            raise


def _recorded_issuer_value(recorder: _TerminationRecorder | None) -> str | None:
    return recorder.termination().issuer.value if recorder is not None else None


__all__ = [
    "_accepts_implicit_development_exit",
    "_agy_has_terminal_completion_evidence",
    "_has_verified_completion",
    "_raise_if_pi_reported_failed_exit",
    "_raise_if_transport_reported_error",
    "_raise_quota_unless_agy_completed",
    "_recorded_issuer_value",
    "_resolved_session_id",
]
