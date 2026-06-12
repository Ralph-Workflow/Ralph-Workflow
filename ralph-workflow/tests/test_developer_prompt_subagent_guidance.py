"""The new ``## PARALLEL EXECUTION`` section must be present in
``developer_iteration.jinja`` and must follow the expected contract.

This test reads the template source directly (rather than rendering it
through the custom template engine) because the source-text checks are
exactly what the audit (``audit_parallelization_dormant``) enforces on
the bundled prompt — a drift in the rendered prompt always means a drift
in the source text.
"""

from __future__ import annotations

from pathlib import Path

_DEVELOPER_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "prompts"
    / "templates"
    / "developer_iteration.jinja"
)


def _read_developer_template() -> str:
    return _DEVELOPER_TEMPLATE.read_text(encoding="utf-8")


def test_developer_prompt_includes_parallel_execution_section() -> None:
    source = _read_developer_template()
    assert "## PARALLEL EXECUTION" in source
    assert "work_units" in source
    assert "sub-agents" in source
    assert "ralph coordinate claim" in source


def test_developer_prompt_section_tells_executor_to_dispatch_subagents() -> None:
    source = _read_developer_template()
    assert "responsible for dispatching your own sub-agents" in source
    assert "If your agent runtime does not support sub-agents" in source
    assert "execute the same plan sequentially" in source


def test_developer_prompt_keeps_development_result_block_intact() -> None:
    """Sanity check: the surrounding prompt still includes the
    DEVELOPMENT RESULT ARTIFACT block (we must not have removed it
    when inserting the PARALLEL EXECUTION block).
    """
    source = _read_developer_template()
    assert "## DEVELOPMENT RESULT ARTIFACT" in source
    assert "## PARALLEL EXECUTION" in source
    assert source.index("## PARALLEL EXECUTION") < source.index(
        "## DEVELOPMENT RESULT ARTIFACT"
    )

