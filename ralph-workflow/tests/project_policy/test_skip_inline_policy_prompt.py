"""Integration tests for the interactive skip-inline-policy offer.

When a marker-free AGENTS.md carries significant user content, the run
preflight offers (TTY only) to keep the existing policy instead of
appending Ralph's managed block. All tests exercise the real
``cli_integration.run_project_policy_readiness`` with injected
``MemoryWorkspace`` / emit / confirm / is_tty seams — no real filesystem,
no real stdin.
"""

from __future__ import annotations

from collections.abc import Callable

from ralph.cli.commands._load_result import _LoadResult
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy import cli_integration, markers
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

#: A marker-free AGENTS.md with a heading — significant per the heuristic.
_SIGNIFICANT_AGENTS_MD = (
    "# Our agent rules\n\nAlways run make test before committing.\n"
)

#: Marker-free content below both significance thresholds.
_INSIGNIFICANT_AGENTS_MD = "be nice to the codebase\n"


def _stub_load_result() -> _LoadResult:
    return _LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root="/test/project", allowed_roots=["/test/project"]
        ),
        initial_state=PipelineState(
            phase="planning", policy_entry_phase="planning"
        ),
        policy_bundle=None,
        run_id="test-run-id",
    )


def _run(
    ws: MemoryWorkspace,
    emit_messages: list[str],
    *,
    confirm: Callable[[str], bool],
    is_tty: Callable[[], bool],
) -> int:
    return cli_integration.run_project_policy_readiness(
        load_result=_stub_load_result(),
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=emit_messages.append,
        invoke_remediation_agent_factory=lambda _w: (lambda _p: False),
        confirm_factory=confirm,
        is_tty=is_tty,
    )


def test_decline_writes_opt_out_and_skips_policy() -> None:
    """Declining the prompt persists the opt-out marker: the preflight
    returns SKIPPED, no managed block is appended, no starters are seeded."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []
    confirm_calls: list[str] = []

    def decline(question: str) -> bool:
        confirm_calls.append(question)
        return False

    rc = _run(ws, emit_messages, confirm=decline, is_tty=lambda: True)

    assert rc == 0
    assert len(confirm_calls) == 1
    content = ws.read(markers.AGENTS_MD)
    assert content.startswith(_SIGNIFICANT_AGENTS_MD)
    assert markers.OPT_OUT_MARKER in content
    assert markers.AGENTS_BLOCK_BEGIN not in content
    assert not ws.exists(f"{markers.CANONICAL_DIR}testing-policy.md")
    assert any("skipped" in m for m in emit_messages)


def test_accept_appends_managed_block_and_proceeds() -> None:
    """Accepting the prompt keeps today's behavior: the managed block is
    appended and the normal readiness flow continues."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []
    confirm_calls: list[str] = []

    def accept(question: str) -> bool:
        confirm_calls.append(question)
        return True

    rc = _run(ws, emit_messages, confirm=accept, is_tty=lambda: True)

    # The fake remediation agent never fixes anything: blocked, not skipped.
    assert rc == 2
    assert len(confirm_calls) == 1
    content = ws.read(markers.AGENTS_MD)
    assert markers.OPT_OUT_MARKER not in content
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert content.startswith(_SIGNIFICANT_AGENTS_MD)


def test_no_tty_never_prompts_and_keeps_current_behavior() -> None:
    """Non-interactive runs (CI/unattended) must never prompt or hang."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    def must_not_be_called(question: str) -> bool:
        raise AssertionError("confirm must not be called without a TTY")

    rc = _run(ws, emit_messages, confirm=must_not_be_called, is_tty=lambda: False)

    assert rc == 2
    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert markers.OPT_OUT_MARKER not in content


def test_insignificant_content_never_prompts() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _INSIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    def must_not_be_called(question: str) -> bool:
        raise AssertionError("confirm must not be called for trivial content")

    rc = _run(ws, emit_messages, confirm=must_not_be_called, is_tty=lambda: True)

    assert rc == 2
    assert markers.AGENTS_BLOCK_BEGIN in ws.read(markers.AGENTS_MD)


def test_existing_managed_block_never_prompts() -> None:
    """A repo that already bootstrapped must not be re-asked."""
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        "# Our agent rules\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\nbody\n{markers.AGENTS_BLOCK_END}\n",
    )
    emit_messages: list[str] = []

    def must_not_be_called(question: str) -> bool:
        raise AssertionError("confirm must not be called when markers exist")

    _run(ws, emit_messages, confirm=must_not_be_called, is_tty=lambda: True)


def test_prompt_failure_falls_back_to_current_behavior() -> None:
    """A crashing prompt (EOF despite isatty, broken pipe) must never block
    the run: fall back to appending the managed block."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    def broken(question: str) -> bool:
        raise EOFError("stdin closed")

    rc = _run(ws, emit_messages, confirm=broken, is_tty=lambda: True)

    assert rc == 2
    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert markers.OPT_OUT_MARKER not in content


def test_prompt_explains_both_choices_clearly() -> None:
    """The messaging contract: before asking, the user is told the repo may
    already have its own policy, that Ralph's is a good default if they are
    not confident, and that keeping theirs writes an opt-out marker."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    _run(ws, emit_messages, confirm=lambda _q: False, is_tty=lambda: True)

    explanation = "\n".join(emit_messages)
    assert "already contains project instructions" in explanation
    assert "good default" in explanation
    assert "opt-out marker" in explanation


def test_declining_schema_upgrade_freezes_existing_policy() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []

    cli_integration._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        confirm=lambda _question: False,
        is_tty=lambda: True,
    )

    assert ws.read(path).startswith("<!-- ralph-policy-schema: freeze v1 -->")
    assert any("froze" in message.lower() for message in messages)


def test_future_schema_freeze_fails_closed() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: freeze v999 -->\n# Customized\n")
    messages: list[str] = []

    resolved = cli_integration._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        confirm=lambda _question: True,
        is_tty=lambda: True,
    )

    assert resolved is False
    assert any("invalid freeze" in message for message in messages)
