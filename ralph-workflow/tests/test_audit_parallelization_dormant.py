"""Tests for ``ralph.testing.audit_parallelization_dormant``.

The audit enforces seven non-vacuous literal-string invariants that
keep the prompt, continuation template, format doc, effect-router
WARNING, bundled pipeline.toml, and planning_analysis.jinja rubric in
lockstep with the bundled default (``dispatch_mode = 'agent_subagents'``,
fan-out dormant).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.testing.audit_parallelization_dormant as audit_module
from ralph.testing.audit_parallelization_dormant import main as audit_main

if TYPE_CHECKING:
    import pytest


def test_audit_returns_zero_when_all_invariants_satisfied() -> None:
    assert audit_main([]) == 0


def test_audit_main_returns_zero_on_clean_tree() -> None:
    """The same ``main()`` entry point used by ``make verify`` must return 0
    against the in-tree literals. Catches the case where the invariants
    regress but the imports still load.
    """
    assert audit_main([]) == 0


def test_audit_module_path() -> None:
    """Audit must be importable as ``ralph.testing.audit_parallelization_dormant``."""
    assert hasattr(audit_module, "main")


def test_audit_seventh_invariant_present_on_clean_tree() -> None:
    """The 7th invariant in ``_INVARIANTS`` must point at the continuation
    template and require the new ``## PARALLEL EXECUTION`` heading. Pins
    the audit contract: removing the invariant would silently drop the
    continuation-template guard.
    """
    invariant_paths = {inv.rel_path for inv in audit_module._INVARIANTS}
    assert (
        "prompts/templates/developer_iteration_continuation.jinja" in invariant_paths
    )

    continuation_invariant = next(
        inv
        for inv in audit_module._INVARIANTS
        if inv.rel_path
        == "prompts/templates/developer_iteration_continuation.jinja"
    )
    assert "## PARALLEL EXECUTION (when the plan declares" in continuation_invariant.present


def test_audit_blocks_regression_when_continuation_heading_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression test: the 7th invariant must catch a continuation template
    that drops the new ``## PARALLEL EXECUTION`` heading. Monkey-patches
    the internal ``_read`` to return content with the heading renamed
    (so the present literal substring is gone) and asserts the audit
    returns 1 with the violation message referencing the continuation
    template path. The real template file is never touched.
    """
    real_read = audit_module._read
    continuation_path = "prompts/templates/developer_iteration_continuation.jinja"

    def _read_with_heading_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == continuation_path:
            return content.replace(
                "## PARALLEL EXECUTION (when the plan declares",
                "## PARALLEL EXECUTION DISABLED",
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_heading_removed)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, (
        "audit must exit 1 when the continuation template drops the new heading"
    )
    assert continuation_path in captured.out, (
        "violation message must reference the continuation template path so "
        "an operator can locate the regression"
    )
    assert "missing required literal" in captured.out
