"""Black-box pin: post-tool-recovery session-id continuity triple contract.

The pre-fix code silently lost the recovered session id on the
``No such tool available: mcp__<server>__<tool>`` failure mode. The
shared recovery surface (in ``ralph/agents/invoke/``) MUST honor the
triple contract:

1. A tool-availability failure followed by ``reset_tool_registry=True``
   yields ``recovery_action_for_failure_reason(...) == 'resume'`` on
   the next attempt (NOT ``'fresh'``).
2. The recovered session id is threaded into the next attempt via
   ``resolve_resume_session_id(has_prior_session=True,
   prior_session_id=<recovered id>, recovery_action='resume')``.
3. An ordinary new-phase transition that explicitly invokes
   ``fresh_session_options(InvokeOptions(session_id=<recovered id>))``
   yields ``session_id is None`` (the new phase starts fresh even when
   the prior attempt recovered a session id).

The three tests below pin each leg of the contract. They are
deterministic, in-process, and run in well under 1s combined — they
must not blow the 60s combined test budget.

These tests are REQUIRED by ``.agent/PLAN.md`` step 10(d). They import
via the public ``ralph.agents.invoke`` surface ONLY (no private
``from ralph.agents.invoke._session import ...``). Patching private
symbols is a silent dead-code relapse; the public surface is the same
one the production code uses.
"""

from __future__ import annotations

import uuid

from ralph.agents.invoke import (
    fresh_session_options,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._types import InvokeOptions


def test_tool_availability_failure_yields_resume_action() -> None:
    """A ``No such tool available: mcp__<server>__<tool>`` failure mode
    with ``reset_tool_registry=True`` must yield ``'resume'`` so the
    next attempt continues the prior session (the tool registry has
    been rebuilt via ``RestartAwareMcpBridge.reset_tool_registry()``).

    Pre-fix: this returned ``'fresh'`` which made every
    tool-availability retry re-read the prompt and lose the session.
    """
    action = recovery_action_for_failure_reason(
        "No such tool available: mcp__ralph__read_file",
        has_prior_session=True,
        reset_tool_registry=True,
    )
    assert action == "resume", (
        f"expected 'resume' for tool-availability failure with "
        f"reset_tool_registry=True; got {action!r}"
    )


def test_resolve_resume_session_id_threads_recovered_id() -> None:
    """``resolve_resume_session_id`` must thread the recovered session
    id into the next attempt when the recovery action is ``'resume'``,
    so the agent subprocess reuses the same session.
    """
    recovered_session_id = uuid.uuid4().hex
    next_session_id = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id=recovered_session_id,
        recovery_action="resume",
    )
    assert next_session_id == recovered_session_id, (
        f"resolve_resume_session_id must thread the recovered session id "
        f"({recovered_session_id!r}); got {next_session_id!r}"
    )


def test_fresh_session_options_clears_session_id() -> None:
    """``fresh_session_options`` must always clear ``session_id`` for
    an ordinary new-phase transition, even when the prior attempt
    recovered a session id. The new phase is explicitly fresh — it
    must NOT inherit the recovered id.
    """
    recovered_session_id = uuid.uuid4().hex
    opts = InvokeOptions(session_id=recovered_session_id, verbose=True)
    fresh = fresh_session_options(opts)
    assert fresh.session_id is None, (
        f"fresh_session_options must clear session_id for new-phase "
        f"transitions; got session_id={fresh.session_id!r}"
    )
    # Other fields are preserved so the caller's verbose/workspace
    # settings still apply on the new phase.
    assert fresh.verbose is True
    # The input is not mutated (the helper is pure / returns a new
    # frozen dataclass).
    assert opts.session_id == recovered_session_id
