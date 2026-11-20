from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.cli.commands import run as run_module
from ralph.pipeline import checkpoint as ckpt
from ralph.prompts.master_prompt import materialize_master_prompt
from ralph.prompts.prompt_digest import (
    PromptCopyReconciliation,
    prompt_changed_since_last_materialization,
    prompt_digest_path,
    reconcile_materialized_prompt,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

PROMPT = "# Goal\n\n1. Build the thing.\n"
AGENT_NOTES = "# Goal\n\n1. **Done, with recorded gaps.** Build the thing.\n"
CHECKPOINT = '{"phase": "development", "iteration": 4}'


def _materialized_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PROMPT_PATH", raising=False)
    (tmp_path / "PROMPT.md").write_text(PROMPT, encoding="utf-8")
    materialize_master_prompt(workspace_root=tmp_path, name="MASTER_PROMPT.md")
    ckpt.run_state_path(tmp_path).write_text(CHECKPOINT, encoding="utf-8")
    (tmp_path / ".agent" / "PLAN.md").write_text("plan", encoding="utf-8")
    return tmp_path / ".agent" / "PRODUCT_CRITERIA.md"


def test_materialization_records_an_engine_owned_prompt_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = _materialized_run(tmp_path, monkeypatch)

    assert copy.read_text(encoding="utf-8") == PROMPT
    assert prompt_digest_path(tmp_path).is_file()


def test_a_resume_after_an_agent_edited_only_the_copy_keeps_state_and_restores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = _materialized_run(tmp_path, monkeypatch)
    copy.write_text(AGENT_NOTES, encoding="utf-8")

    outcome = run_module.invalidate_pipeline_state_if_prompt_changed(tmp_path)

    assert outcome is PromptCopyReconciliation.COPY_RESTORED
    assert ckpt.run_state_path(tmp_path).read_text(encoding="utf-8") == CHECKPOINT
    assert (tmp_path / ".agent" / "PLAN.md").is_file()
    assert copy.read_text(encoding="utf-8") == PROMPT
    assert prompt_changed_since_last_materialization(tmp_path) is False


def test_a_real_prompt_edit_still_invalidates_saved_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialized_run(tmp_path, monkeypatch)
    (tmp_path / "PROMPT.md").write_text(PROMPT + "2. And another thing.\n", encoding="utf-8")

    assert prompt_changed_since_last_materialization(tmp_path) is True

    outcome = run_module.invalidate_pipeline_state_if_prompt_changed(tmp_path)

    assert outcome is PromptCopyReconciliation.PROMPT_CHANGED
    assert not ckpt.run_state_path(tmp_path).exists()
    assert not (tmp_path / ".agent" / "PLAN.md").exists()


def test_a_prompt_edit_is_detected_once_and_settles_after_rematerialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialized_run(tmp_path, monkeypatch)
    (tmp_path / "PROMPT.md").write_text("# New goal\n", encoding="utf-8")
    run_module.invalidate_pipeline_state_if_prompt_changed(tmp_path)

    materialize_master_prompt(workspace_root=tmp_path, name="MASTER_PROMPT.md")

    assert prompt_changed_since_last_materialization(tmp_path) is False
    assert reconcile_materialized_prompt(tmp_path) is PromptCopyReconciliation.UNCHANGED


def test_a_legacy_run_without_a_digest_never_wipes_and_records_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = _materialized_run(tmp_path, monkeypatch)
    prompt_digest_path(tmp_path).unlink()
    copy.write_text(AGENT_NOTES, encoding="utf-8")

    assert prompt_changed_since_last_materialization(tmp_path) is False

    outcome = run_module.invalidate_pipeline_state_if_prompt_changed(tmp_path)

    assert outcome is PromptCopyReconciliation.COPY_RESTORED
    assert ckpt.run_state_path(tmp_path).read_text(encoding="utf-8") == CHECKPOINT
    assert copy.read_text(encoding="utf-8") == PROMPT
    assert prompt_digest_path(tmp_path).is_file()


def test_a_legacy_run_whose_copy_matches_records_a_digest_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialized_run(tmp_path, monkeypatch)
    prompt_digest_path(tmp_path).unlink()

    outcome = run_module.invalidate_pipeline_state_if_prompt_changed(tmp_path)

    assert outcome is PromptCopyReconciliation.DIGEST_RECORDED
    assert ckpt.run_state_path(tmp_path).read_text(encoding="utf-8") == CHECKPOINT
    assert prompt_digest_path(tmp_path).is_file()


def test_a_fresh_workspace_is_left_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PROMPT_PATH", raising=False)
    (tmp_path / "PROMPT.md").write_text(PROMPT, encoding="utf-8")

    outcome = run_module.invalidate_pipeline_state_if_prompt_changed(tmp_path)

    assert outcome is PromptCopyReconciliation.UNCHANGED
    assert not (tmp_path / ".agent").exists()
