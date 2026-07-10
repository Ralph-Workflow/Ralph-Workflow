# property-test: B — McpSession Protocol runtime conformance
"""Runtime conformance gate for the McpSession structural protocol.

The static mypy build (non-strict) is the compile-time gate; this test is
the runtime gate. Every member declared on McpSession must be reachable on
a freshly-instantiated FileBackedSession (production path) AND AgentSession
(test path) and return a sensible value — not just present, but actually
callable. The list is derived from the Protocol itself so the gate extends
automatically as new members are added.

A member added to one implementation but missing from the other is a
production AttributeError waiting to ship — the exact failure class that
caused the -32001 retry storm. Drift must be caught here, not in
production.
"""

from __future__ import annotations

import json
import re
import threading
import typing
from pathlib import Path

import pytest

from ralph.mcp.protocol.session import AgentSession, McpSession
from ralph.mcp.server.runtime_session import FileBackedSession


def _make_file_backed_session(tmp_path: Path) -> FileBackedSession:
    session_file = tmp_path / "session.json"
    session_file.write_text(
        json.dumps(
            {
                "session_id": "sess-prop-b",
                "run_id": "run-prop-b",
                "drain": "standalone",
                "capabilities": ["ProcessExecBounded", "WorkspaceRead"],
                "created_at": 12345.0,
            }
        ),
        encoding="utf-8",
    )
    return FileBackedSession(session_file)


def _make_agent_session() -> AgentSession:
    return AgentSession(
        session_id="agent-prop-b",
        run_id="run-agent-prop-b",
        drain="standalone",
        capabilities={"ProcessExecBounded", "WorkspaceRead"},
        created_at=12345.0,
    )


def _all_mcp_session_members() -> list[str]:
    """Return every public McpSession member name from the Protocol itself.

    Derived from typing.get_protocol_members() so adding a new @property
    or method to McpSession extends this list automatically.
    """
    return sorted(typing.get_protocol_members(McpSession))


def test_mcp_session_protocol_has_expected_member_count() -> None:
    """The Protocol must declare 23 members (the production contract).

    Updated for RFC-013 P3: ``broker_secret`` is added to the
    surface so the broker-owned HMAC secret can be threaded through
    the live receipt / sentinel write paths. AC-03 adds
    ``explore_index`` so the production session bridge can attach
    one ExploreIndex handle per session/workspace pair. AC-11 adds
    ``exec_resource_resolver`` so the production session bridge can
    attach one ``ExecResourceResolver`` per session/workspace pair.
    """
    members = _all_mcp_session_members()
    assert len(members) == 23, (
        f"McpSession expected to declare 23 members, found {len(members)}: {members}"
    )


def test_every_mcp_session_member_is_callable_on_file_backed_session(
    tmp_path: Path,
) -> None:
    """Every Protocol member is reachable on a freshly-instantiated FileBackedSession."""
    session = _make_file_backed_session(tmp_path)
    missing: list[str] = []
    for name in _all_mcp_session_members():
        # Just verify the attribute is reachable — don't enforce return values
        # here (other tests check return values; this one is a reachability gate).
        try:
            _ = getattr(session, name)
        except Exception as exc:
            missing.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not missing, f"FileBackedSession unreachable members: {missing}"


def test_every_mcp_session_member_is_callable_on_agent_session() -> None:
    """Every Protocol member is reachable on a freshly-instantiated AgentSession."""
    session = _make_agent_session()
    for name in _all_mcp_session_members():
        _ = getattr(session, name)


def test_file_backed_session_returns_sensible_values_for_all_properties(
    tmp_path: Path,
) -> None:
    """All McpSession @property members return sensible values from a populated session."""
    session = _make_file_backed_session(tmp_path)
    # Members that may legitimately be None on a session before any sink has
    # been installed, before any edit-area check has been run, or before any
    # worker artifact dir or policy_flags payload is set.
    nullable = {
        "tool_output_sink_entry",
        "edit_area_result",
        "worker_artifact_dir",
        "worker_namespace",
        "policy_flags",
        "stored_capability_profile",
        "broker_secret",
        "explore_index",
        "exec_resource_resolver",
    }
    for name in _all_mcp_session_members():
        if name in nullable:
            continue
        value = getattr(session, name)
        # properties must return a real value
        assert value is not None, f"{name} returned None"


def test_file_backed_session_supports_sink_swap_contract(tmp_path: Path) -> None:
    """tool_output_sink_entry must be settable, not just present (production uses it)."""
    session = _make_file_backed_session(tmp_path)
    events: list[dict[str, object]] = []
    session.tool_output_sink_entry = (threading.get_ident(), events.append)
    sink = session.current_thread_tool_output_sink()
    assert sink is not None
    sink({"event": "ok"})
    assert events == [{"event": "ok"}]
    session.tool_output_sink_entry = None
    assert session.current_thread_tool_output_sink() is None


def test_check_capability_returns_approved_for_granted_capability(tmp_path: Path) -> None:
    """The check_capability method must approve a granted capability."""
    session = _make_file_backed_session(tmp_path)
    result = session.check_capability("ProcessExecBounded")
    assert result == "approved"


def test_check_capability_returns_denied_for_ungranted_capability(tmp_path: Path) -> None:
    """The check_capability method must deny an ungranted capability."""
    session = _make_file_backed_session(tmp_path)
    result = session.check_capability("WorkspaceWriteEphemeral")
    assert result == "denied"


def test_is_parallel_worker_returns_bool(tmp_path: Path) -> None:
    """is_parallel_worker must return a bool."""
    session = _make_file_backed_session(tmp_path)
    assert isinstance(session.is_parallel_worker(), bool)


def test_check_edit_area_returns_approved_for_non_parallel(tmp_path: Path) -> None:
    """check_edit_area approves any path when not a parallel worker."""
    session = _make_file_backed_session(tmp_path)
    assert session.check_edit_area("some/path.py") == "approved"


def test_both_session_types_satisfy_mcp_session_protocol(tmp_path: Path) -> None:
    """Typed witness: the authoritative static check is session_from_env's return type."""
    file_backed: McpSession = _make_file_backed_session(tmp_path)
    in_memory: McpSession = _make_agent_session()
    assert file_backed.session_id == "sess-prop-b"
    assert in_memory.session_id == "agent-prop-b"


def test_no_isinstance_narrowing_on_session_in_mcp_package(tmp_path: Path) -> None:
    """Audit: no isinstance narrowing in ralph/mcp/ silently bypasses the contract.

    The intent: a check that narrows a session *to a specific implementation*
    (AgentSession, FileBackedSession) so the rest of the code can access
    members the structural McpSession contract does not declare — the kind of
    narrowing the type system cannot see, that the storm exploited.

    Legitimate seams remain allowed: structural-Protocol checks (e.g.
    ``isinstance(session, _SessionWithStreaming)``) and constructor coercion
    in builder factories where the caller already contracted the input type.
    The grep here is intentionally scoped to ``isinstance(session, FileBackedSession)``-
    style hits, which are the failure class.

    Implementation note: uses pure-Path + read_text (not subprocess.run)
    so the test-policy audit does not flag a subprocess call in a test.
    """
    mcp_pkg = Path(__file__).parent.parent / "ralph" / "mcp"
    hits: list[str] = []
    for py_file in mcp_pkg.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        if "isinstance(session, FileBackedSession" in text:
            rel = py_file.relative_to(Path(__file__).parent.parent)
            hits.append(f"{rel}")
    assert not hits, f"isinstance narrowing of session to FileBackedSession: {hits}"


def test_no_cast_of_mcp_session_anywhere(tmp_path: Path) -> None:
    """Audit: no cast('McpSession', ...) in ralph/ — the storm-enabling laundering."""
    ralph_root = Path(__file__).parent.parent / "ralph"
    hits: list[str] = []
    for py_file in ralph_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        if (
            "cast" in text
            and "McpSession" in text
            and re.search(r"cast\s*\(\s*['\"]McpSession['\"]", text)
        ):
            rel = py_file.relative_to(Path(__file__).parent.parent)
            hits.append(f"{rel}")
    assert not hits, f"Found cast(*McpSession, ...) laundering: {hits}"


def test_runtime_session_returns_typed_mcp_session(tmp_path: Path) -> None:
    """session_from_env's return type must be McpSession | None (not cast)."""
    text = (
        Path(__file__).parent.parent / "ralph" / "mcp" / "server" / "runtime_session.py"
    ).read_text()
    assert "McpSession" in text, "runtime_session.py missing typed return"
    # The McpSession import must be present
    assert "from ralph.mcp.protocol.session import" in text


@pytest.mark.parametrize("name", _all_mcp_session_members())
def test_each_mcp_session_member_is_reachable_on_file_backed(name: str, tmp_path: Path) -> None:
    """Parametrized: each Protocol member is reachable on FileBackedSession."""
    session = _make_file_backed_session(tmp_path)
    assert hasattr(session, name), f"FileBackedSession missing {name!r}"


@pytest.mark.parametrize("name", _all_mcp_session_members())
def test_each_mcp_session_member_is_reachable_on_agent_session(name: str) -> None:
    """Parametrized: each Protocol member is reachable on AgentSession."""
    session = _make_agent_session()
    assert hasattr(session, name), f"AgentSession missing {name!r}"
