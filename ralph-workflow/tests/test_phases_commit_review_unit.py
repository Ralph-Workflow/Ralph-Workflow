from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.phases import PhaseContext
from ralph.phases.commit_review import build_commit_review_retry_hint, handle_commit_review_phase
from ralph.pipeline.effects import CommitEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent

pytestmark = [pytest.mark.timeout_seconds(5)]


def test_prepare_prompt_returns_prompt_prepared() -> None:
    workspace = MagicMock()
    workspace.exists.return_value = False
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = PreparePromptEffect(phase="development_commit_review", iteration=1)
    result = handle_commit_review_phase(effect, ctx)
    assert result == [PipelineEvent.PROMPT_PREPARED]


def test_non_agent_effect_returns_empty() -> None:
    workspace = MagicMock()
    workspace.exists.return_value = False
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = CommitEffect(message_file="/tmp/msg.txt")
    result = handle_commit_review_phase(effect, ctx)
    assert result == []


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_empty_skipped_paths_returns_sentinel() -> None:
    hint = build_commit_review_retry_hint(skipped_paths=[], safe_applied_count=0)

    assert isinstance(hint, str)
    assert hint, "build_commit_review_retry_hint must NOT return an empty string"
    assert "no delete actions were rejected" in hint, (
        f"Sentinel must explicitly state that no delete actions were rejected; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_with_skipped_paths_and_safe_count() -> None:
    hint = build_commit_review_retry_hint(skipped_paths=["foo.py", "bar.py"], safe_applied_count=2)

    assert "foo.py" in hint, f"Path foo.py must be named in the hint; got: {hint!r}"
    assert "bar.py" in hint, f"Path bar.py must be named in the hint; got: {hint!r}"
    assert "Safe actions applied: 2" in hint, (
        f"Safe count must appear in the form 'Safe actions applied: 2'; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_with_skipped_paths_and_zero_safe_count() -> None:
    hint = build_commit_review_retry_hint(skipped_paths=["baz.py"], safe_applied_count=0)

    assert "baz.py" in hint, f"Path baz.py must be named in the hint; got: {hint!r}"
    assert "No safe actions were applied" in hint, (
        f"Zero-safe-count summary must use the 'No safe actions were applied' "
        f"wording; got: {hint!r}"
    )
    assert "Safe actions applied: 0" not in hint, (
        f"Zero-safe-count summary must NOT use the 'Safe actions applied: N' form; got: {hint!r}"
    )
