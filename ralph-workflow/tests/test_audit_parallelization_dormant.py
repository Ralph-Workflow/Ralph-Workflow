"""Tests for ``ralph.testing.audit_parallelization_dormant``.

The audit enforces eight non-vacuous literal-string invariants that
keep the prompt, continuation template, format doc, effect-router
WARNING, bundled pipeline.toml, planning_analysis.jinja rubric, the
user-facing configuration docs, and the advanced pipeline-configuration
doc in lockstep with the bundled default
(``dispatch_mode = 'agent_subagents'``, fan-out dormant).
"""

from __future__ import annotations

import pytest

import ralph.testing.audit_parallelization_dormant as audit_module
from ralph.testing.audit_parallelization_dormant import main as audit_main


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
    """The continuation-template invariant in ``_INVARIANTS`` must point
    at the continuation template and require the new
    ``## PARALLEL EXECUTION`` heading. Pins the audit contract: removing
    the invariant would silently drop the continuation-template guard.
    """
    invariant_paths = {inv.rel_path for inv in audit_module._INVARIANTS}
    assert "prompts/templates/developer_iteration_continuation.jinja" in invariant_paths

    continuation_invariant = next(
        inv
        for inv in audit_module._INVARIANTS
        if inv.rel_path == "prompts/templates/developer_iteration_continuation.jinja"
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

    assert rc == 1, "audit must exit 1 when the continuation template drops the new heading"
    assert continuation_path in captured.out, (
        "violation message must reference the continuation template path so "
        "an operator can locate the regression"
    )
    assert "missing required literal" in captured.out


def test_audit_blocks_regression_when_subagent_capability_doc_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression test: the ``configuration.md`` invariant must catch the
    user-facing config page losing its ``subagent_capability`` H3
    subsection. Monkey-patches the internal ``_read`` to return content
    with the literal renamed (so the present literal substring is gone)
    and asserts the audit returns 1 with the violation message
    referencing the configuration.md path. The real config file is never
    touched.

    The test is skipped if the in-tree configuration.md does not
    actually contain ``subagent_capability`` (i.e. the doc edit was
    never made and the invariant is already failing for the wrong
    reason). A passing test on an empty doc would mask the real
    regression; the skip makes the missing premise explicit.
    """
    config_path = "../docs/sphinx/configuration.md"
    real_content = audit_module._read(config_path)
    if "subagent_capability" not in real_content:
        pytest.skip(
            "configuration.md is missing subagent_capability on the in-tree "
            "clean tree; the new H3 subsection was not added and the "
            "audit invariant is already vacuous. Re-apply step 2 of the "
            "plan before running this regression test."
        )

    real_read = audit_module._read

    def _read_with_literal_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == config_path:
            return content.replace("subagent_capability", "FEATURE_REMOVED_FOR_TEST")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_literal_removed)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, "audit must exit 1 when configuration.md drops subagent_capability"
    assert "configuration.md" in captured.out, (
        "violation message must reference the configuration.md path so an "
        "operator can locate the regression"
    )
    assert "missing required literal" in captured.out


def test_audit_blocks_regression_when_dispatch_mode_doc_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression test: the ``advanced-pipeline-configuration.md`` invariant
    must catch the advanced pipeline page losing its ``dispatch_mode``
    coverage. Monkey-patches the internal ``_read`` to return content
    with the literal renamed (so the present literal substring is gone)
    and asserts the audit returns 1 with the violation message
    referencing the advanced-pipeline-configuration.md path. The real
    doc file is never touched.

    The test is skipped if the in-tree advanced-pipeline-configuration.md
    does not actually contain ``dispatch_mode`` (i.e. the doc surface was
    never added and the invariant is already failing for the wrong
    reason). A passing test on an empty doc would mask the real
    regression; the skip makes the missing premise explicit.
    """
    doc_path = "../docs/sphinx/advanced-pipeline-configuration.md"
    real_content = audit_module._read(doc_path)
    if "dispatch_mode" not in real_content:
        pytest.skip(
            "advanced-pipeline-configuration.md is missing dispatch_mode on "
            "the in-tree clean tree; the [phases.<name>.parallelization] "
            "H3 was not added and the audit invariant is already vacuous. "
            "Re-apply the rework that introduced the dispatch_mode surface "
            "before running this regression test."
        )

    real_read = audit_module._read

    def _read_with_literal_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == doc_path:
            return content.replace("dispatch_mode", "FEATURE_REMOVED_FOR_TEST")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_literal_removed)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, "audit must exit 1 when advanced-pipeline-configuration.md drops dispatch_mode"
    assert "advanced-pipeline-configuration.md" in captured.out, (
        "violation message must reference the advanced-pipeline-configuration.md "
        "path so an operator can locate the regression"
    )
    assert "missing required literal" in captured.out
