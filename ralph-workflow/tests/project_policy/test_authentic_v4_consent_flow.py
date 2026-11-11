from __future__ import annotations

from collections.abc import Sequence

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import _prompt_ui, _schema_upgrade, markers, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.authentic_v4_data import V4_PORTFOLIO, V4_STARTERS

V4_CORE_FILES = markers.core_files_for_version("v4")


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
        self.choices.append(tuple(c.key for c in choices))
        self.defaults.append(default)
        return self._answers.pop(0)


def _clean_policy_prose(body: str) -> str:
    import re

    body = re.sub(r"<!--\s*RALPH-STARTER-TEMPLATE:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"RALPH-COMMAND:\s*PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test", body)
    body = body.replace("<date>", "2026-07-13")
    body = body.replace("<trigger>", "policy-change")
    body = body.replace("REPLACE-ME", "verified-token")
    body = body.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    return body


def _build_unmodified_v4_pack() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    for name in V4_CORE_FILES:
        raw_body = V4_STARTERS[name]
        cleaned_body = _clean_policy_prose(raw_body)
        ws.write(f"{markers.CANONICAL_DIR}{name}", cleaned_body)

    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(markers.PORTFOLIO_PATH, V4_PORTFOLIO)
    return ws


def test_unmodified_v4_refuses_unattended_continuation() -> None:
    ws = _build_unmodified_v4_pack()
    messages: list[str] = []

    resolved = _schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("upgrade"),
        is_tty=lambda: False,
    )

    assert resolved is False, "Non-TTY must refuse continuation with outdated v4 files"

    refusal = " ".join(messages)
    assert "Policy schema choice required" in refusal, (
        f"Expected refusal message about schema choice; got: {messages}"
    )

    for name in V4_CORE_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        first_line = ws.read(path).splitlines()[0]
        assert first_line == "<!-- ralph-policy-schema: v4 -->", (
            f"Marker must not change when is_tty=False; {path}: {first_line!r}"
        )


def test_interactive_v4_freeze_rewrite_pins_first_line() -> None:
    ws = _build_unmodified_v4_pack()
    messages: list[str] = []

    resolved = _schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("freeze"),
        is_tty=lambda: True,
    )

    assert resolved is True, "Interactive freeze must return True"

    for name in V4_CORE_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        content = ws.read(path)
        lines = content.splitlines()
        assert lines[0] == "<!-- ralph-policy-schema: freeze v4 -->", (
            f"Expected freeze v4 marker; got: {lines[0]!r} in {path}"
        )
        raw_body = V4_STARTERS[name]
        expected_body = _clean_policy_prose(raw_body)
        expected_rest = "\n".join(expected_body.splitlines()[1:])
        actual_rest = "\n".join(lines[1:])
        assert actual_rest == expected_rest, (
            f"Non-marker content of {path} must be byte-identical after freeze"
        )

    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings == [], f"Frozen v4 pack must validate READY; got: {findings}"


def test_interactive_v4_upgrade_consent_returns_true_no_marker_rewrite() -> None:
    ws = _build_unmodified_v4_pack()

    resolved = _schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        list[str]().append,
        select=_Selector("upgrade"),
        is_tty=lambda: True,
    )

    assert resolved is True, "Interactive upgrade consent must return True"

    for name in V4_CORE_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        first_line = ws.read(path).splitlines()[0]
        assert first_line == "<!-- ralph-policy-schema: v4 -->", (
            f"Upgrade mode must not rewrite markers; {path}: {first_line!r}"
        )


def test_upgrade_matrix_v4_and_v5_to_v6() -> None:
    ws = MemoryWorkspace()
    ws.write(
        f"{markers.CANONICAL_DIR}testing-policy.md", "<!-- ralph-policy-schema: v4 -->\n# Test\n"
    )
    ws.write(
        f"{markers.CANONICAL_DIR}linting-policy.md", "<!-- ralph-policy-schema: v5 -->\n# Lint\n"
    )

    emitted: list[str] = []
    resolved = _schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        emit=emitted.append,
        select=_Selector("freeze"),
        is_tty=lambda: True,
    )
    assert resolved is True
    assert ws.read(f"{markers.CANONICAL_DIR}testing-policy.md").startswith(
        "<!-- ralph-policy-schema: freeze v4 -->"
    )
    assert ws.read(f"{markers.CANONICAL_DIR}linting-policy.md").startswith(
        "<!-- ralph-policy-schema: freeze v5 -->"
    )
