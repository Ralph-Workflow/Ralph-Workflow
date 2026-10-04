"""S-8: setup-time seeding failures surface as visible warnings.

Before the fix the five ``logger.debug`` best-effort catches in
``_sync_shipped_skills_on_pipeline_run`` swallowed every failure at
default log level, so a broken user-global skill root, a failed
project-scope install, an unreadable ``.gitignore``, a failed
auto-commit, or a failed retention sweep were all invisible. After the
fix the run still proceeds (best-effort by design) but each failure
emits a ``logger.warning`` with the remediation hint so the operator
can investigate without re-running with ``--verbose``.

This test pins the post-fix behavior: monkeypatch each collaborator
to raise and assert the warning message is emitted and no exception
propagates. No real subprocess, no real sleep (per
``ralph.testing.audit_test_policy``); the test runs in well under the
1 s per-test budget.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.cli.commands import run as run_module


def _capture_warnings() -> tuple[list[str], int]:
    """Return a (records, sink_id) pair for warning capture.

    The caller is responsible for ``logger.remove(sink_id)`` in a
    ``finally`` block. Captures at WARNING level so we never miss the
    post-fix upgrade from ``logger.debug``.
    """
    records: list[str] = []

    def _sink(message: object) -> None:
        records.append(str(message))

    sink_id = logger.add(_sink, level="WARNING", format="{message}")
    return records, sink_id


def test_user_global_skill_update_check_failure_emits_visible_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken user-global skill root surfaces as a warning, not a silent debug line."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.side_effect = RuntimeError("user-global broken")
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)

    records, sink_id = _capture_warnings()
    try:
        # Must not raise.
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    warning_text = "\n".join(records)
    assert "User-global skill update check failed" in warning_text, (
        f"user-global skill check failure MUST surface as a visible warning; got: {warning_text!r}"
    )
    assert "--force-init-skills" in warning_text, (
        f"warning MUST point at the remediation command; got: {warning_text!r}"
    )


def test_project_skill_install_failure_emits_visible_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A project-scope skill install error surfaces as a warning."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(
        run_module,
        "_project_skills_need_install",
        lambda _root: True,
    )
    monkeypatch.setattr(
        run_module,
        "install_project_baseline_skills",
        MagicMock(side_effect=RuntimeError("project install broken")),
    )

    records, sink_id = _capture_warnings()
    try:
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    warning_text = "\n".join(records)
    assert "Project-scope skill install failed" in warning_text, (
        f"project install failure MUST surface as a visible warning; got: {warning_text!r}"
    )


def test_gitignore_autoseed_failure_emits_visible_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A .gitignore/.git/info/exclude auto-seed error surfaces as a warning."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(
        run_module,
        "_project_skills_need_install",
        lambda _root: False,
    )

    bs_module = pytest.importorskip("ralph.config.bootstrap")

    def _failing_seed(_root: Path) -> list[str]:
        raise RuntimeError("gitignore seed broken")

    monkeypatch.setattr(bs_module, "auto_seed_default_gitignore", _failing_seed)
    monkeypatch.setattr(bs_module, "auto_seed_default_git_exclude", _failing_seed)

    records, sink_id = _capture_warnings()
    try:
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    warning_text = "\n".join(records)
    assert "gitignore" in warning_text.lower() or "exclude" in warning_text.lower(), (
        f"gitignore / exclude auto-seed failure MUST surface as a "
        f"visible warning; got: {warning_text!r}"
    )
    assert "non-fatal" in warning_text, (
        f"warning MUST label the failure as non-fatal; got: {warning_text!r}"
    )


def test_retention_sweep_failure_emits_visible_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A retention sweep error surfaces as a warning, never as a swallowed debug line."""
    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(
        run_module,
        "_project_skills_need_install",
        lambda _root: False,
    )

    retention_module = pytest.importorskip("ralph.workspace.agent_dir_retention")
    monkeypatch.setattr(
        retention_module,
        "sweep_agent_dir",
        MagicMock(side_effect=RuntimeError("sweep broken")),
    )

    records, sink_id = _capture_warnings()
    try:
        run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)
    finally:
        logger.remove(sink_id)

    warning_text = "\n".join(records)
    assert "Retention sweep failed" in warning_text, (
        f"retention sweep failure MUST surface as a visible warning; got: {warning_text!r}"
    )
