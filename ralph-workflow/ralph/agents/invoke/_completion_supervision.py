from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import PostExitVerdict, PostExitWatchdog
from ralph.agents.invoke._completion_options import CompletionCheckOptions, _completion_run_id
from ralph.agents.invoke._session import _bounded_output_lines
from ralph.agents.invoke.attempt_supervision import AttemptSupervisor
from ralph.agents.invoke.attempt_supervision._issuer_mapping import _teardown_issuer_for_verdict
from ralph.agents.invoke.attempt_supervision.teardown_fn import TeardownFn, resolve_teardown
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.process.liveness import DefaultLivenessProbe
from ralph.process.teardown import teardown_subtree

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess
    from ralph.process.teardown import ProcessTeardown

_POST_EXIT_FIRE_VERDICTS: frozenset[PostExitVerdict] = frozenset(
    {PostExitVerdict.FIRE_DESCENDANT_HANG, PostExitVerdict.FIRE_PROCESS_EXIT_HANG}
)


def _required_option[T](value: T | None, name: str) -> T:
    if value is None:
        raise AssertionError(f"completion option {name} is required")
    return value


def completion_teardown(process_teardown: ProcessTeardown | None) -> TeardownFn:
    return resolve_teardown(process_teardown, teardown_subtree)


def _record_post_exit_watchdog_fire(
    supervisor: AttemptSupervisor, verdict: PostExitVerdict
) -> None:
    if verdict in _POST_EXIT_FIRE_VERDICTS:
        supervisor.recorder.record_post_exit_watchdog_fire()


def _wait_for_completion_grace(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    opts: CompletionCheckOptions,
    parsed_output: list[str],
    *,
    clock: Clock | None = None,
    teardown: ProcessTeardown | None = None,
    supervisor: AttemptSupervisor | None = None,
) -> AgentExecutionState:
    workspace_path = _required_option(opts.workspace_path, "workspace_path")
    execution_strategy = _required_option(opts.execution_strategy, "execution_strategy")

    effective_clock: Clock = clock or SystemClock()
    probe = opts.liveness_probe or DefaultLivenessProbe()

    _eval_fn = (
        opts.evaluate_completion_fn
        if opts.evaluate_completion_fn is not None
        else evaluate_completion
    )

    def classify_exit_state() -> AgentExecutionState:
        signals = _eval_fn(
            workspace_path,
            _bounded_output_lines(
                parsed_output,
                explicit_completion_seen=opts.explicit_completion_seen,
            ),
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    effective_supervisor = supervisor or AttemptSupervisor.for_recorder(None, handle)
    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_parent_exit_grace(classify_exit_state)
    _record_post_exit_watchdog_fire(effective_supervisor, verdict)
    effective_supervisor.cleanup(
        _teardown_issuer_for_verdict(verdict),
        teardown=completion_teardown(teardown),
    )
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    if verdict == PostExitVerdict.CHILDREN_ACTIVE:
        return AgentExecutionState.WAITING_ON_CHILD
    return AgentExecutionState.RESUMABLE_CONTINUE


def _wait_for_descendants_then_recheck(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name_or_opts: str | CompletionCheckOptions,
    opts_or_parsed_output: CompletionCheckOptions | list[str],
    parsed_output: list[str] | None = None,
    *,
    clock: Clock | None = None,
    teardown: ProcessTeardown | None = None,
    supervisor: AttemptSupervisor | None = None,
) -> AgentExecutionState:
    if isinstance(agent_name_or_opts, CompletionCheckOptions):
        opts = agent_name_or_opts
        if not isinstance(opts_or_parsed_output, list):
            raise TypeError("parsed_output must be a list")
        parsed_output = opts_or_parsed_output
    else:
        if not isinstance(opts_or_parsed_output, CompletionCheckOptions) or parsed_output is None:
            raise TypeError("completion options and parsed_output are required")
        opts = opts_or_parsed_output

    workspace_path = _required_option(opts.workspace_path, "workspace_path")
    execution_strategy = _required_option(opts.execution_strategy, "execution_strategy")

    effective_clock: Clock = clock or SystemClock()
    probe = opts.liveness_probe or DefaultLivenessProbe()

    _eval_fn = (
        opts.evaluate_completion_fn
        if opts.evaluate_completion_fn is not None
        else evaluate_completion
    )

    def classify_exit_state() -> AgentExecutionState:
        signals = _eval_fn(
            workspace_path,
            _bounded_output_lines(
                parsed_output,
                explicit_completion_seen=opts.explicit_completion_seen,
            ),
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    effective_supervisor = supervisor or AttemptSupervisor.for_recorder(None, handle)
    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_descendant_quiesce(classify_exit_state)
    _record_post_exit_watchdog_fire(effective_supervisor, verdict)
    effective_supervisor.cleanup(
        _teardown_issuer_for_verdict(verdict),
        teardown=completion_teardown(teardown),
    )
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    return AgentExecutionState.RESUMABLE_CONTINUE


__all__ = [
    "_record_post_exit_watchdog_fire",
    "_wait_for_completion_grace",
    "_wait_for_descendants_then_recheck",
    "completion_teardown",
]
