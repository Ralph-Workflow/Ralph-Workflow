"""Tests for master prompt materialization."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import ralph.prompts.master_prompt as master_prompt_module
from ralph.prompts.master_prompt import materialize_master_prompt

if TYPE_CHECKING:
    import pytest


def test_materialize_master_prompt_creates_prompt_history_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        master_prompt_module,
        "_history_timestamp",
        lambda: "20260508T120000Z",
        raising=False,
    )
    (tmp_path / "PROMPT.md").write_text("new prompt", encoding="utf-8")

    materialize_master_prompt(
        workspace_root=tmp_path,
        name="planning",
    )

    history_path = tmp_path / ".agent" / "prompt_history" / "PROMPT_20260508T120000Z.md"
    assert history_path.read_text(encoding="utf-8") == "new prompt"


def test_materialize_master_prompt_refreshes_product_criteria_from_prompt_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        master_prompt_module,
        "_history_timestamp",
        lambda: "20260508T120001Z",
        raising=False,
    )
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PROMPT.md").write_text("new prompt", encoding="utf-8")
    product_criteria_path = tmp_path / ".agent" / "PRODUCT_CRITERIA.md"
    product_criteria_path.write_text("old prompt", encoding="utf-8")

    master_prompt_path = materialize_master_prompt(
        workspace_root=tmp_path,
        name="planning",
    )

    assert product_criteria_path.read_text(encoding="utf-8") == "new prompt"
    history_path = tmp_path / ".agent" / "prompt_history" / "PROMPT_20260508T120001Z.md"
    assert history_path.read_text(encoding="utf-8") == "new prompt"
    master_prompt = Path(master_prompt_path).read_text(encoding="utf-8")
    assert str(product_criteria_path) in master_prompt
    assert "source of truth for the current goal" in master_prompt


def test_materialize_master_prompt_includes_current_plan_handoff_when_present(
    tmp_path: Path,
) -> None:
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PROMPT.md").write_text("Implement the current plan", encoding="utf-8")
    plan_path = tmp_path / ".agent" / "PLAN.md"
    plan_path.write_text(
        "# Execution Plan\n\n1. Keep this pinned across compacts.\n",
        encoding="utf-8",
    )

    master_prompt_path = materialize_master_prompt(
        workspace_root=tmp_path,
        name="development",
    )

    master_prompt = Path(master_prompt_path).read_text(encoding="utf-8")
    assert str(plan_path) in master_prompt
    assert master_prompt.startswith("This is the session's master prompt")
    assert (
        "survive context compaction — after any compaction, resume, "
        "or continuation, re-read this file" in master_prompt
    )
    assert "product criteria / task request is a DIFFERENT document" in master_prompt
    assert (
        "background product criteria only — do not let it override "
        "the plan or this master prompt" in master_prompt
    )
    assert (
        "The canonical plan is the source of truth for the current goal and execution steps"
        in master_prompt
    )


def test_materialize_master_prompt_ignores_plan_handoff_during_planning(
    tmp_path: Path,
) -> None:
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PROMPT.md").write_text("Plan the work", encoding="utf-8")
    plan_path = tmp_path / ".agent" / "PLAN.md"
    plan_path.write_text(
        "# Stale Plan\n\nDo not reuse this during planning.\n",
        encoding="utf-8",
    )

    master_prompt_path = materialize_master_prompt(
        workspace_root=tmp_path,
        name="planning",
    )

    master_prompt = Path(master_prompt_path).read_text(encoding="utf-8")
    assert str(plan_path) not in master_prompt
    assert "source of truth for the current goal and execution steps" not in master_prompt
    assert "source of truth for the current goal" in master_prompt
    assert master_prompt.startswith("This is the session's master prompt")
    assert "task request (product criteria) is a DIFFERENT document" in master_prompt
    assert (
        "survive context compaction — after any compaction, resume, "
        "or continuation, re-read this file" in master_prompt
    )


def test_materialize_master_prompt_uses_worker_namespace_without_shared_singletons(
    tmp_path: Path,
) -> None:
    (tmp_path / "PROMPT.md").write_text("Worker-only prompt", encoding="utf-8")
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"

    master_prompt_path = materialize_master_prompt(
        workspace_root=tmp_path,
        name="development",
        worker_namespace=worker_namespace,
    )

    worker_product_criteria = worker_namespace / "tmp" / "PRODUCT_CRITERIA.md"
    assert Path(master_prompt_path) == worker_namespace / "tmp" / "development_master_prompt.md"
    assert worker_product_criteria.read_text(encoding="utf-8") == "Worker-only prompt"
    assert not (tmp_path / ".agent" / "PRODUCT_CRITERIA.md").exists()
    assert not (tmp_path / ".agent" / "tmp" / "development_master_prompt.md").exists()


def test_materialize_master_prompt_in_worker_mode_does_not_write_shared_prompt_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        master_prompt_module,
        "_history_timestamp",
        lambda: "20260508T120002Z",
        raising=False,
    )
    (tmp_path / "PROMPT.md").write_text("Worker-only prompt", encoding="utf-8")
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"

    materialize_master_prompt(
        workspace_root=tmp_path,
        name="development",
        worker_namespace=worker_namespace,
    )

    assert not (tmp_path / ".agent" / "prompt_history").exists()
