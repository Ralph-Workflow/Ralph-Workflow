from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from ralph.cli.commands._load_result import _LoadResult
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy import _prompt_ui, cli_integration, markers
from ralph.project_policy import _schema_upgrade as policy_schema_upgrade
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

_SIGNIFICANT_AGENTS_MD = "# Our agent rules\n\nAlways run make test before committing.\n"

_INSIGNIFICANT_AGENTS_MD = "be nice to the codebase\n"


class _Selector:
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
        workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
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
        invoke_remediation_agent_factory=None,
        select_factory=select,
        is_tty=is_tty,
    )


def test_keep_writes_opt_out_and_skips_policy() -> None:
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
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    emit_messages: list[str] = []
    selector = _Selector("adopt")

    rc = _run(ws, emit_messages, select=selector, is_tty=lambda: True)

    assert rc == 0
    assert len(selector.questions) == 1
    content = ws.read(markers.AGENTS_MD)
    assert markers.OPT_OUT_MARKER not in content
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert content.startswith(_SIGNIFICANT_AGENTS_MD)


def test_adopt_is_the_default_choice() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, _SIGNIFICANT_AGENTS_MD)
    selector = _Selector("adopt")

    _run(ws, [], select=selector, is_tty=lambda: True)

    assert selector.defaults == ["adopt"]
    assert selector.choices[0] == ("adopt", "keep", "explain")


def test_explain_lists_policy_files_and_reasks() -> None:
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

    assert rc == 0
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

    assert rc == 0
    assert markers.AGENTS_BLOCK_BEGIN in ws.read(markers.AGENTS_MD)


@pytest.mark.timeout_seconds(5)
def test_existing_managed_block_never_prompts() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        f"# Our agent rules\n\n{markers.AGENTS_BLOCK_BEGIN}\nbody\n{markers.AGENTS_BLOCK_END}\n",
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

    assert rc == 0
    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert markers.OPT_OUT_MARKER not in content


def test_prompt_explains_the_ramifications_of_each_choice() -> None:
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
    assert "not an experienced software developer" in panel
    assert "strong engineering process" in panel


def test_menu_descriptions_fit_on_one_terminal_line() -> None:
    budget = 60
    choices = [
        *cli_integration._INIT_CHOICES,
        *policy_schema_upgrade._schema_choices(3),
    ]
    too_long = [choice.key for choice in choices if len(choice.description) > budget]
    assert not too_long, f"descriptions exceed {budget} chars: {too_long}"


def test_declining_schema_upgrade_freezes_existing_policy() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []

    policy_schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("freeze"),
        is_tty=lambda: True,
    )

    assert ws.read(path).startswith("<!-- ralph-policy-schema: freeze v1 -->")
    assert any("froze" in message.lower() for message in messages)


def test_schema_upgrade_asks_once_for_all_files() -> None:
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

    resolved = policy_schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=selector,
        is_tty=lambda: True,
    )

    assert resolved is True
    assert len(selector.questions) == 1
    assert selector.choices[0] == ("upgrade", "freeze", "explain")
    assert selector.defaults == ["upgrade"]
    for path in paths:
        assert "freeze" not in ws.read(path)


def test_schema_explain_reasks_without_writing() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []
    selector = _Selector("explain", "upgrade")

    resolved = policy_schema_upgrade._maybe_resolve_schema_upgrade(
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
    ws = MemoryWorkspace()
    paths = [
        f"{markers.CANONICAL_DIR}testing-policy.md",
        f"{markers.CANONICAL_DIR}linting-policy.md",
    ]
    for path in paths:
        ws.write(path, "<!-- ralph-policy-schema: v1 -->\n# Customized\n")
    messages: list[str] = []
    selector = _Selector("freeze")

    resolved = policy_schema_upgrade._maybe_resolve_schema_upgrade(
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
    for path in paths:
        assert path in undo
    assert "freeze" in undo
    assert "rerun" in undo.lower()


def test_future_schema_freeze_fails_closed() -> None:
    ws = MemoryWorkspace()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, "<!-- ralph-policy-schema: freeze v999 -->\n# Customized\n")
    messages: list[str] = []

    resolved = policy_schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("upgrade"),
        is_tty=lambda: True,
    )

    assert resolved is False
    assert any("invalid freeze" in message for message in messages)


@pytest.mark.timeout_seconds(5)
def test_v3_freeze_does_not_install_v4_portfolio_defaults() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import preflight

    ws = MemoryWorkspace()
    policy_path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(policy_path, "<!-- ralph-policy-schema: v3 -->\n# Customized\n")
    policy_schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        list[str]().append,
        select=_Selector("freeze"),
        is_tty=lambda: True,
    )

    preflight.run_policy_readiness_preflight(ws, ProjectStack(primary_language="Python"))

    assert not ws.exists(markers.PORTFOLIO_PATH)
    assert ws.read(policy_path).startswith("<!-- ralph-policy-schema: freeze v3 -->")


@pytest.mark.timeout_seconds(5)
def test_v3_upgrade_installs_portfolio_through_public_preflight() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import parse_portfolio_toml, preflight

    ws = MemoryWorkspace()
    policy_path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(policy_path, "<!-- ralph-policy-schema: v3 -->\n# Customized\n")
    policy_schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        list[str]().append,
        select=_Selector("upgrade"),
        is_tty=lambda: True,
    )

    preflight.run_policy_readiness_preflight(ws, ProjectStack(primary_language="Python"))

    portfolio = parse_portfolio_toml(ws.read(markers.PORTFOLIO_PATH))
    assert portfolio.schema_version == markers.PORTFOLIO_SCHEMA_VERSION
