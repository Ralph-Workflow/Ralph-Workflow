"""Tests for the optional-delegation planning guidance audit."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.testing.audit_parallelization_dormant as audit_module
from ralph.testing.audit_parallelization_dormant import main as audit_main

if TYPE_CHECKING:
    import pytest


def test_audit_returns_zero_when_all_invariants_satisfied() -> None:
    assert audit_main([]) == 0


def test_audit_module_path() -> None:
    assert hasattr(audit_module, "main")


def test_audit_blocks_required_guidance_regression(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_read = audit_module._read
    planning_path = "prompts/templates/planning.jinja"

    def _read_with_guidance_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == planning_path:
            return content.replace(
                "Use subagents only when independent repository discovery", "GUIDANCE_REMOVED"
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_guidance_removed)
    assert audit_main([]) == 1
    assert planning_path in capsys.readouterr().out


def test_audit_blocks_document_shape_review_regression(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_read = audit_module._read
    analysis_path = "prompts/templates/planning_analysis.jinja"

    def _read_with_review_guidance_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == analysis_path:
            return content.replace("do not grade document shape", "GUIDANCE_REMOVED")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_review_guidance_removed)
    assert audit_main([]) == 1
    assert analysis_path in capsys.readouterr().out
