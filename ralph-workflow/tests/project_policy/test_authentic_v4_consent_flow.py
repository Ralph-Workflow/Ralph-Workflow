"""Authentic v4 consent-flow integration tests.

Exercises the public _maybe_resolve_schema_upgrade route with an unmodified
authentic v4 corpus (v4 schema marker, v1 portfolio) against the three
supported paths: unattended refusal, interactive freeze-rewrite, and
interactive upgrade-consent.

No subprocess, no sleep, no real I/O — pure in-memory with MemoryWorkspace.
"""

from __future__ import annotations

from collections.abc import Sequence

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import _prompt_ui, _schema_upgrade, markers, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.authentic_v4_data import V4_PORTFOLIO, V4_STARTERS


class _Selector:
    """Scripted select seam: returns each queued answer in order."""

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
    """Strip starter banner and resolve placeholder tokens in v4 starter prose."""
    import re

    body = re.sub(r"<!--\s*RALPH-STARTER-TEMPLATE:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"RALPH-COMMAND:\s*PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test", body)
    body = body.replace("<date>", "2026-07-13")
    body = body.replace("<trigger>", "policy-change")
    body = body.replace("REPLACE-ME", "verified-token")
    body = body.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    return body


def _build_unmodified_v4_pack() -> MemoryWorkspace:
    """Build MemoryWorkspace with authentic v4 starters — v4 marker kept as-is.

    Unlike _build_v4_pack in test_v4_corpus_authentic.py, this does NOT
    rewrite the schema marker to "freeze v4". The v4 marker stays unchanged
    so _maybe_resolve_schema_upgrade sees outdated files and exercises
    the upgrade-or-freeze consent gate.
    """
    ws = MemoryWorkspace()
    for name in markers._V4_CORE_FILES:
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
    """Non-TTY path refuses continuation when outdated v4 files are present.

    _maybe_resolve_schema_upgrade returns False and emits the rerun message
    without rewriting any marker.
    """
    ws = _build_unmodified_v4_pack()
    messages: list[str] = []

    resolved = _schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("upgrade"),  # would upgrade if TTY were True
        is_tty=lambda: False,
    )

    assert resolved is False, "Non-TTY must refuse continuation with outdated v4 files"

    refusal = " ".join(messages)
    assert "Policy schema choice required" in refusal, (
        f"Expected refusal message about schema choice; got: {messages}"
    )

    # Verify no marker was rewritten
    for name in markers._V4_CORE_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        first_line = ws.read(path).splitlines()[0]
        assert first_line == "<!-- ralph-policy-schema: v4 -->", (
            f"Marker must not change when is_tty=False; {path}: {first_line!r}"
        )


def test_interactive_v4_freeze_rewrite_pins_first_line() -> None:
    """Interactive freeze choice rewrites every outdated file's marker to freeze v4.

    The rest of each file must be byte-identical to the input — only the
    first-line marker changes. validate_readiness must still accept the
    frozen v4 workspace (v4 manifest validates frozen v4 without v5 fields).
    """
    ws = _build_unmodified_v4_pack()
    messages: list[str] = []

    resolved = _schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        messages.append,
        select=_Selector("freeze"),
        is_tty=lambda: True,
    )

    assert resolved is True, "Interactive freeze must return True"

    for name in markers._V4_CORE_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        content = ws.read(path)
        lines = content.splitlines()
        assert lines[0] == "<!-- ralph-policy-schema: freeze v4 -->", (
            f"Expected freeze v4 marker; got: {lines[0]!r} in {path}"
        )
        # Rest of file must be unchanged — compare the raw v4 starter prose
        # (with banner stripped and placeholders resolved) against everything
        # after the rewritten first line.
        raw_body = V4_STARTERS[name]
        expected_body = _clean_policy_prose(raw_body)
        expected_rest = "\n".join(expected_body.splitlines()[1:])
        actual_rest = "\n".join(lines[1:])
        assert actual_rest == expected_rest, (
            f"Non-marker content of {path} must be byte-identical after freeze"
        )

    # Confirm validators still accept the frozen v4 workspace
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings == [], f"Frozen v4 pack must validate READY; got: {findings}"


def test_interactive_v4_upgrade_consent_returns_true_no_marker_rewrite() -> None:
    """Interactive upgrade consent returns True without rewriting any marker.

    The upgrade path in _maybe_resolve_schema_upgrade returns True and lets
    the downstream remediation agent apply the actual file upgrades; it does
    NOT rewrite files itself.
    """
    ws = _build_unmodified_v4_pack()

    resolved = _schema_upgrade._maybe_resolve_schema_upgrade(
        ws,
        list[str]().append,
        select=_Selector("upgrade"),
        is_tty=lambda: True,
    )

    assert resolved is True, "Interactive upgrade consent must return True"

    # No marker should be rewritten on upgrade consent
    for name in markers._V4_CORE_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        first_line = ws.read(path).splitlines()[0]
        assert first_line == "<!-- ralph-policy-schema: v4 -->", (
            f"Upgrade mode must not rewrite markers; {path}: {first_line!r}"
        )
