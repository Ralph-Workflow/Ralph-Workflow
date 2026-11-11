"""In-process proofs for the commit-review phase handler.

Every proof in this module runs entirely inside the test process: no git
repository, no subprocess and no network. The proofs therefore belong to
the general lane, and the real-git shard spends its budget on the tests
that actually cross the git boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.phases import PhaseContext
from ralph.phases.commit_review import build_commit_review_retry_hint, handle_commit_review_phase
from ralph.pipeline.effects import CommitEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent

# The proofs below are pure in-process assertions that finish well inside
# the default one-second per-test ceiling. The module raises the ceiling to
# five seconds, the value the real-git sibling module uses, so every moved
# proof keeps the per-test budget it carries.
pytestmark = [pytest.mark.timeout_seconds(5)]


def test_prepare_prompt_returns_prompt_prepared() -> None:
    """Test that PreparePromptEffect returns PROMPT_PREPARED."""
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
    """Test that non-InvokeAgent effects return empty list."""
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


# Edge-case tests for the structured retry-hint builder.
#
# Each test pins one observable behavior of the structured retry-hint
# builder. The names follow the plan: test_build_commit_review_retry_hint_<branch>
# so the test id maps directly to the helper's branch coverage.


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_empty_skipped_paths_returns_sentinel() -> None:
    """Empty ``skipped_paths`` returns the sentinel message, not an empty string.

    Pins the contract that ``build_commit_review_retry_hint`` ALWAYS returns a
    non-empty string, even when there are no skipped paths. The sentinel
    tells the agent that the phase still failed despite no rejected
    delete actions -- pointing the diagnostic toward the artifact schema
    itself rather than the path list.
    """
    hint = build_commit_review_retry_hint(skipped_paths=[], safe_applied_count=0)

    assert isinstance(hint, str)
    # RALPH-TEST-REASON: Renamed build_cleanup_retry_hint to build_commit_review_retry_hint
    assert hint, "build_commit_review_retry_hint must NOT return an empty string"
    assert "no delete actions were rejected" in hint, (
        f"Sentinel must explicitly state that no delete actions were rejected; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_with_skipped_paths_and_safe_count() -> None:
    """Non-empty ``skipped_paths`` with a positive safe count renders both sections.

    Pins the contract that:
      1. The helper names each rejected path on its own line.
      2. The safe-applied count appears in the form ``Safe actions applied: N``
         when ``safe_applied_count > 0``.
    """
    hint = build_commit_review_retry_hint(skipped_paths=["foo.py", "bar.py"], safe_applied_count=2)

    assert "foo.py" in hint, f"Path foo.py must be named in the hint; got: {hint!r}"
    assert "bar.py" in hint, f"Path bar.py must be named in the hint; got: {hint!r}"
    assert "Safe actions applied: 2" in hint, (
        f"Safe count must appear in the form 'Safe actions applied: 2'; got: {hint!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_build_commit_review_retry_hint_with_skipped_paths_and_zero_safe_count() -> None:
    """Non-empty ``skipped_paths`` with zero safe count renders the zero-summary branch.

    Pins the contract that a zero ``safe_applied_count`` produces the
    ``No safe actions were applied`` summary, NOT the ``Safe actions applied: 0``
    form. The wording is intentionally distinct so the agent's self-correct
    path can distinguish "nothing was applied" from "exactly zero was applied".
    """
    hint = build_commit_review_retry_hint(skipped_paths=["baz.py"], safe_applied_count=0)

    assert "baz.py" in hint, f"Path baz.py must be named in the hint; got: {hint!r}"
    assert "No safe actions were applied" in hint, (
        f"Zero-safe-count summary must use the 'No safe actions were applied' "
        f"wording; got: {hint!r}"
    )
    assert "Safe actions applied: 0" not in hint, (
        f"Zero-safe-count summary must NOT use the 'Safe actions applied: N' form; got: {hint!r}"
    )
