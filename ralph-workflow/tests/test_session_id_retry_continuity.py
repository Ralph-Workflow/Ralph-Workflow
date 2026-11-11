from __future__ import annotations

import uuid

from ralph.agents.invoke import (
    InvokeOptions,
    fresh_session_options,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)


def test_tool_availability_failure_yields_resume_action() -> None:
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
    recovered_session_id = uuid.uuid4().hex
    opts = InvokeOptions(session_id=recovered_session_id, verbose=True)
    fresh = fresh_session_options(opts)
    assert fresh.session_id is None, (
        f"fresh_session_options must clear session_id for new-phase "
        f"transitions; got session_id={fresh.session_id!r}"
    )
    assert fresh.verbose is True
    assert opts.session_id == recovered_session_id
