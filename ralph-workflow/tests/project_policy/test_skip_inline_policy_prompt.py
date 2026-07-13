"""Integration tests for the interactive adopt-or-keep policy menu.

When a marker-free AGENTS.md carries significant user content, the run
preflight offers (TTY only) a menu: adopt Ralph Workflow's managed policy,
keep the existing policy, or ask what the policy contains. All tests
exercise the real ``cli_integration.run_project_policy_readiness`` with
injected ``MemoryWorkspace`` / emit / select / is_tty seams — no real
filesystem, no real stdin.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ralph.cli.commands._load_result import _LoadResult
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy import _prompt_ui, cli_integration, markers
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

#: A marker-free AGENTS.md with a heading — significant per the heuristic.
_SIGNIFICANT_AGENTS_MD = (
    "# Our agent rules\n\nAlways run make test before committing.\n"
)

#: Marker-free content below both significance thresholds.
_INSIGNIFICANT_AGENTS_MD = "be nice to the codebase\n"


class _Selector:
    """Scripted select seam: answers each menu with the next queued key."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.questions: list[str] = []
        self.choices: list[tuple[str, ...]] = []
        self.defaults: list[str] = []

    def __call__(
        self,
        question: str,
        choices: Sequence[_prompt_ui.PromptChoice],
        default: str,
    ) -> str:
        self.questions.append(question)
        self.choices.append(tuple(choice.key for choice in choices))
        self.defaults.append(default)
        return self._answers.pop(0)


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
    select: _prompt_ui.SelectFn,
    is_tty: Callable[[], bool],
) -> int:
    return cli_integration.run_project_policy_readiness(
        load_result=_stub_load_result(),
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=emit_messages.append,
        invoke_remediation_agent_factory=lambda _w: (lambda _p: False),
        select_factory=select,
        is_tty=is_tty,
    )


def test_keep_writes_opt_out_and_skips_policy() -> None:
    """Keeping the existing policy persists the opt-out marker: the preflight
    returns SKIPPED, no managed block is appended, no starters are seeded."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []
    selector = _Selector("keep")

    rc = _run(ws, emit_messages, select=selector, is_tty=lambda: True)

    assert rc == 0
    assert len(selector.questions) == 1
    content = ws.read(markers.AGENTS_MD)
    assert content.startswith(_SIGNIFICANT_AGENTS_MD)
    assert markers.OPT_OUT_MARKER in content
    assert markers.AGENTS_BLOCK_BEGIN not in content
    assert not ws.exists(f"{markers.CANONICAL_DIR}testing-policy.md")
    assert any("skipped" in m for m in emit_messages)


def test_adopt_appends_managed_block_and_proceeds() -> None:
    """Adopting keeps today's behavior: the managed block is appended and the
    normal readiness flow continues."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []
    selector = _Selector("adopt")

    rc = _run(ws, emit_messages, select=selector, is_tty=lambda: True)

    # The fake remediation agent never fixes anything: blocked, not skipped.
    assert rc == 2
    assert len(selector.questions) == 1
    content = ws.read(markers.AGENTS_MD)
    assert markers.OPT_OUT_MARKER not in content
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert content.startswith(_SIGNIFICANT_AGENTS_MD)


def test_adopt_is_the_default_choice() -> None:
    """Enter-through must start the setup, not disable enforcement — the
    default answer is the one the prompt falls back on when it cannot run."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    selector = _Selector("adopt")

    _run(ws, [], select=selector, is_tty=lambda: True)

    assert selector.defaults == ["adopt"]
    assert selector.choices[0] == ("adopt", "keep", "explain")


def test_explain_lists_policy_files_and_reasks() -> None:
    """The 'what does it contain' choice writes nothing, names the policy
    files that would be created, and returns to the menu."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []
    selector = _Selector("explain", "keep")

    _run(ws, emit_messages, select=selector, is_tty=lambda: True)

    assert len(selector.questions) == 2
    detail = "\n".join(emit_messages)
    for name in markers.CORE_POLICY_FILES:
        assert f"{markers.CANONICAL_DIR}{name}" in detail


def test_no_tty_never_prompts_and_keeps_current_behavior() -> None:
    """Non-interactive runs (CI/unattended) must never prompt or hang."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    def must_not_be_called(
        question: str,
        choices: Sequence[_prompt_ui.PromptChoice],
        default: str,
    ) -> str:
        raise AssertionError("select must not be called without a TTY")

    rc = _run(ws, emit_messages, select=must_not_be_called, is_tty=lambda: False)

    assert rc == 2
    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert markers.OPT_OUT_MARKER not in content


def test_insignificant_content_never_prompts() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _INSIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    def must_not_be_called(
        question: str,
        choices: Sequence[_prompt_ui.PromptChoice],
        default: str,
    ) -> str:
        raise AssertionError("select must not be called for trivial content")

    rc = _run(ws, emit_messages, select=must_not_be_called, is_tty=lambda: True)

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

    def must_not_be_called(
        question: str,
        choices: Sequence[_prompt_ui.PromptChoice],
        default: str,
    ) -> str:
        raise AssertionError("select must not be called when markers exist")

    _run(ws, emit_messages, select=must_not_be_called, is_tty=lambda: True)


def test_prompt_failure_falls_back_to_current_behavior() -> None:
    """A crashing prompt (EOF despite isatty, broken pipe) must never block
    the run: fall back to the default, which appends the managed block."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    def broken(
        question: str,
        choices: Sequence[_prompt_ui.PromptChoice],
        default: str,
    ) -> str:
        raise EOFError("stdin closed")

    rc = _run(ws, emit_messages, select=broken, is_tty=lambda: True)

    assert rc == 2
    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert markers.OPT_OUT_MARKER not in content


def test_prompt_explains_the_ramifications_of_each_choice() -> None:
    """The messaging contract. Before choosing, the user must be told: the
    repo may already have its own policy; adopting is a one-time ~30 minute
    agent setup; the policy files help any AI assistant, not just Ralph
    Workflow runs; keeping theirs is for an established process and writes an
    opt-out marker."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []

    _run(ws, emit_messages, select=_Selector("keep"), is_tty=lambda: True)

    panel = "\n".join(emit_messages)
    assert "already contains agent instructions" in panel
    assert "ONE-TIME" in panel
    assert "30 minutes" in panel
    assert "token spend" in panel
    assert "not just Ralph Workflow runs" in panel
    assert "opt-out marker" in panel
    # Who each choice is for. questionary clips a menu description to one
    # line, so this guidance has to survive in the panel, not in the menu.
    assert "not an experienced software developer" in panel
    assert "strong engineering process" in panel


def test_menu_descriptions_fit_on_one_terminal_line() -> None:
    """questionary renders a choice description as a single line and clips it
    at the terminal width. A description that overflows a narrow terminal is
    shown to the user cut off mid-word, so keep them short."""
    budget = 60
    choices = [
        *cli_integration._INIT_CHOICES,
        *cli_integration._schema_choices(3),
    ]
    too_long = [
        choice.key for choice in choices if len(choice.description) > budget
    ]
    assert not too_long, f"descriptions exceed {budget} chars: {too_long}"


def test_declining_schema_upgrade_freezes_existing_policy() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []

    cli_integration._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("freeze"),
        is_tty=lambda: True,
    )

    assert ws.read(path).startswith("<!-- ralph-policy-schema: freeze v1 -->")
    assert any("froze" in message.lower() for message in messages)


def test_schema_upgrade_asks_once_for_all_files() -> None:
    """Multiple outdated files must produce exactly ONE prompt, not one per
    file. Upgrading leaves every file for the remediation agent."""
    ws = MemoryWorkspace()
    paths = [
        f"{markers.CANONICAL_DIR}testing-policy.md",
        f"{markers.CANONICAL_DIR}linting-policy.md",
        f"{markers.CANONICAL_DIR}security-policy.md",
    ]
    for path in paths:
        ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []
    selector = _Selector("upgrade")

    resolved = cli_integration._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=selector,
        is_tty=lambda: True,
    )

    assert resolved is True
    assert len(selector.questions) == 1
    assert selector.choices[0] == ("upgrade", "freeze", "explain")
    assert selector.defaults == ["upgrade"]
    # Upgrading all leaves the files untouched (remediation upgrades later).
    for path in paths:
        assert "freeze" not in ws.read(path)


def test_schema_explain_reasks_without_writing() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []
    selector = _Selector("explain", "upgrade")

    resolved = cli_integration._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=selector,
        is_tty=lambda: True,
    )

    assert resolved is True
    assert len(selector.questions) == 2
    assert "freeze" not in ws.read(path)
    assert any("Upgrading hands each file" in message for message in messages)


def test_declining_schema_upgrade_freezes_all_and_shows_undo() -> None:
    """Freezing pins EVERY outdated file at once and tells the user where to
    remove the skip if they change their mind."""
    ws = MemoryWorkspace()
    paths = [
        f"{markers.CANONICAL_DIR}testing-policy.md",
        f"{markers.CANONICAL_DIR}linting-policy.md",
    ]
    for path in paths:
        ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []
    selector = _Selector("freeze")

    resolved = cli_integration._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=selector,
        is_tty=lambda: True,
    )

    assert resolved is True
    assert len(selector.questions) == 1
    for path in paths:
        assert ws.read(path).startswith("<!-- ralph-policy-schema: freeze v1 -->")
    undo = "\n".join(messages)
    # The freeze message must name every frozen file and explain the undo.
    for path in paths:
        assert path in undo
    assert "freeze" in undo
    assert "rerun" in undo.lower()


def test_future_schema_freeze_fails_closed() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: freeze v999 -->\n# Customized\n")
    messages: list[str] = []

    resolved = cli_integration._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("upgrade"),
        is_tty=lambda: True,
    )

    assert resolved is False
    assert any("invalid freeze" in message for message in messages)
