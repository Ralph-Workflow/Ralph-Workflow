from __future__ import annotations

from ralph.pipeline import build_integration_status
from ralph.pipeline.conflict_resolution import driver
from ralph.pipeline.conflict_resolution._driver_events import announce_declared_decisions
from ralph.process.manager._process_manager_runtime import expected_exit_codes
from ralph.recovery.agent_selection import (
    agent_availability,
    format_selection_evidence,
    select_preferred_agent,
)


def test_partial_conflict_scope_renders_each_path_reason() -> None:
    status = build_integration_status(
        cause="OUT_OF_REACH",
        paths_with_reasons=(
            ("vendor/module", "gitlink conflict"),
            ("asset.bin", "binary conflict"),
        ),
        command="git add -- vendor/module asset.bin",
        agents_can_finish=False,
    )

    scope = driver._status_scope(status)

    assert "vendor/module: gitlink conflict" in scope
    assert "asset.bin: binary conflict" in scope


def test_declared_decision_announcement_renders_each_path_reason(monkeypatch) -> None:
    emitted: list[str] = []
    status = build_integration_status(
        cause="DECLARED_DECISION",
        paths_with_reasons=(
            ("deleted.txt", "delete/modify declaration"),
            ("asset.bin", "binary declaration"),
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution._driver_events._emit",
        lambda _display, line: emitted.append(line),
    )

    announce_declared_decisions(None, status)

    assert emitted == [
        "conflict(s) git could not write markers into, so the resolution cannot be "
        "read off the file and must be declared: deleted.txt: delete/modify declaration; "
        "asset.bin: binary declaration"
    ]


def test_lower_priority_evidence_explains_configured_chain_order() -> None:
    selection = select_preferred_agent(
        (
            agent_availability("claude", True, 0, False),
            agent_availability("opencode", True, 0, False),
        )
    )

    evidence = format_selection_evidence("integration", selection)

    assert "opencode: lower_priority" not in evidence.replace(
        "lower_priority (listed after selected agent claude in configured chain order)",
        "explained",
    )
    assert "listed after selected agent claude in configured chain order" in evidence


def test_benign_git_probe_exit_codes_are_expected_answers() -> None:
    assert expected_exit_codes("git-symbolic-ref-head") == frozenset({1})
    assert expected_exit_codes("git-rev-parse-head") == frozenset({128})
    assert expected_exit_codes("git-rev-parse-commit-parent") == frozenset({128})
    assert expected_exit_codes("git-rev-parse-local-branch") == frozenset({128})
    assert expected_exit_codes("git-rev-parse-remote-tracking") == frozenset({128})
