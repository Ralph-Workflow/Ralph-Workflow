"""AgyIncompleteExitError — deterministic rc=0 incomplete-exit classification.

AGY's headless mode can end a run WITHOUT the required completion
evidence: the process exits rc=0, but the durable ``declare_complete``
sentinel is missing, or a required artifact receipt is missing. Measured
causes (see ``tests/display/_fixtures/agy_wire_provenance.md`` and
``ralph/agents/_agy_upstream_diagnostic.py``) include the provider-owned
print deadline expiring mid-run, a permission auto-deny that empties the
stream, and the model simply stopping before finishing the assigned
work — including runs where the model waited for interactive input or
asked for clarification that a non-interactive run can never answer.

AGY exposes NO stable wire-level "waiting for user input" signal: no
measured capture contains a waiting/question event, so Ralph cannot
reliably classify a LIVE waiting condition and never will guess one from
conversational prose. Recovery is therefore limited to the OBJECTIVE
condition "process exited without required completion evidence", which
this exception types. The condition is also NEVER conflated with
``AgentExecutionState.WAITING_ON_CHILD`` (live descendant work), which
is classified by the execution strategies, not by this raise site.

Deterministic classification contract (mirrors
``_open_code_resumable_exit_error.py``):

    1. ``AgyIncompleteExitError`` is a typed-cause exception that the
       failure classifier MUST recognize BEFORE the broader
       ``AgentInvocationError`` branch
       (``ralph/recovery/failure_classifier.py::_categorize_exc``), so
       the signature NEVER falls through to ``FailureCategory.AMBIGUOUS``.

    2. ``retryable_agent_failure_reason``
       (``ralph/pipeline/retryable_failure.py``) maps it to the
       canonical reason "the agent exited without required completion
       evidence", which engages the recovery layer's retry-prompt
       machinery.

    3. Recovery is BOUNDED TO ONE automatic reprompt per invocation,
       enforced at two points (one invariant, two enforcement points):
       ``build_agent_recovery_plan`` returns ``None`` once
       ``AgentRecoveryInput.completion_reprompt_used`` is True, and the
       ``run_with_direct_mcp_recovery`` /
       ``iter_with_direct_mcp_recovery`` loops stop driving retries for
       this type after the first reprompt. There is no unbounded retry
       loop.

    4. The reprompt is ALWAYS a FRESH AGY invocation
       (``recovery_action_for_failure_reason`` returns ``"fresh"`` for
       this type): AGY does not demonstrably support resumable sessions
       (the v1.1.8 continuation probes did not expose session identity;
       the transport opts out via ``no_default_session_flag`` and its
       strategy's ``supports_session_continuation()`` is False). The
       fresh invocation carries the original task plus an explicit
       completion instruction (continue autonomously, finish the
       assigned work, submit any required artifact via the canonical
       ``ralph_submit_md_artifact`` MCP tool, and call
       ``declare_complete``).

    5. The reprompt itself is NEVER completion evidence: the fresh
       invocation re-earns the sentinel/receipt through the real MCP
       tools, and ``check_process_result`` re-evaluates evidence on its
       exit. Sentinel, receipt, and artifact-proof requirements are not
       weakened in any way.

    6. When completion evidence is still missing after the bounded
       reprompt, this exception surfaces as the terminal, actionable
       error: the message names the missing evidence and the exact MCP
       tools the agent must call.

The typed error is raised only for strategies that declare
``supports_incomplete_exit_reprompt()`` (the AGY strategy); every other
completion-enforcing transport keeps the legacy plain
``AgentInvocationError``.

Lock-in regression tests:
    ``tests/recovery/test_agy_incomplete_exit_recovery.py``
    ``tests/test_agy_incomplete_exit_reprompt.py``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError

if TYPE_CHECKING:
    from ralph.agents.execution_state import BaseExecutionStrategy


class AgyIncompleteExitError(AgentInvocationError):
    """Raised when AGY exits rc=0 without required completion evidence.

    The run cannot be resumed (AGY has no demonstrably resumable
    session), so the recovery layer maps this into ONE bounded
    fresh-session reprompt; see the module docstring for the full
    contract.
    """

    def __init__(self, agent_name: str, diagnostic: str | None = None) -> None:
        canonical = (
            "agent exited without required completion evidence "
            "(completion sentinel missing, or required artifact receipt missing). "
            "Ralph issues exactly one automatic recovery reprompt per invocation "
            "for this condition; seeing this error means that reprompt was "
            "already spent or did not produce the evidence. Action: inspect the "
            "agent transcript — the agent must finish the assigned work "
            "autonomously (no interactive input is available), submit any "
            "required artifact via the ralph_submit_md_artifact MCP tool, and "
            "call declare_complete before exiting."
        )
        message = f"{diagnostic}\n{canonical}" if diagnostic else canonical
        super().__init__(agent_name, 0, message)


def raise_missing_completion_evidence(
    agent_name: str,
    execution_strategy: BaseExecutionStrategy,
    diagnostic: str | None,
) -> NoReturn:
    """Raise the missing-completion-evidence error for a rc=0 exit.

    Strategies that declare ``supports_incomplete_exit_reprompt()`` (AGY)
    get the typed ``AgyIncompleteExitError`` so the recovery layer issues
    exactly ONE bounded fresh-session reprompt (original task plus an
    explicit completion instruction); every other completion-enforcing
    transport keeps the legacy plain ``AgentInvocationError``.
    """
    if execution_strategy.supports_incomplete_exit_reprompt():
        raise AgyIncompleteExitError(agent_name, diagnostic=diagnostic)
    canonical = (
        "agent exited without required completion evidence "
        "(completion sentinel missing, or required artifact receipt missing)"
    )
    message = f"{diagnostic}\n{canonical}" if diagnostic else canonical
    raise AgentInvocationError(agent_name, 0, message)


__all__ = ["AgyIncompleteExitError", "raise_missing_completion_evidence"]
