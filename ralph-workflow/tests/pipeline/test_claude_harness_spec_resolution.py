from __future__ import annotations

import pytest

from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec


@pytest.mark.timeout_seconds(3)
def test_smoke_harness_regression_interactive_claude_resolves_for_any_model() -> None:
    spec = resolve_smoke_harness_spec("claude/sonnet")

    assert spec.relative_dir == resolve_smoke_harness_spec("claude/haiku").relative_dir
    assert spec.run_id != resolve_smoke_harness_spec("claude/haiku").run_id


@pytest.mark.timeout_seconds(3)
def test_smoke_harness_regression_bare_claude_aliases_resolve() -> None:
    assert resolve_smoke_harness_spec("claude").run_id == "interactive-claude-smoke"
    assert resolve_smoke_harness_spec("claude-headless").run_id == "headless-claude-smoke"


@pytest.mark.timeout_seconds(3)
def test_smoke_harness_legacy_claude_haiku_layout_is_unchanged() -> None:
    spec = resolve_smoke_harness_spec("claude/haiku")

    assert spec.run_id == "interactive-claude-smoke"
    assert spec.relative_dir.as_posix() == "tmp/interactive-claude-smoke"
