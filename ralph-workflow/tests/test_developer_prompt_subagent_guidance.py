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


def test_developer_prompt_never_references_phantom_coordinate_command() -> None:
    """``ralph coordinate`` does not exist in the Python CLI; prompts must not
    mention it, even as a prohibition — agents should instead be told that no
    coordination command exists at all."""
    source = _read_developer_template()
    assert "ralph coordinate" not in source
    assert "no coordination command" in source


def test_developer_prompt_section_tells_executor_to_dispatch_subagents() -> None:
    source = _read_developer_template()
    assert "responsible for dispatching your own sub-agents" in source
    assert "If your agent runtime does not support sub-agents" in source
    assert "execute the same plan sequentially" in source


def test_developer_prompt_encourages_proactive_subagent_use() -> None:
    """Sub-agents are not limited to declared work units: the developer may
    parallelize information gathering and naturally concurrent steps."""
    source = _read_developer_template()
    assert "not limited to declared work units" in source
    assert "Information gathering" in source
    assert "Naturally concurrent steps" in source
    assert "When to stay sequential" in source
    assert "Never let two sub-agents edit the same file" in source


def test_developer_prompt_keeps_development_result_block_intact() -> None:
    """Sanity check: the surrounding prompt still includes the
    DEVELOPMENT RESULT ARTIFACT block (we must not have removed it
    when inserting the PARALLEL EXECUTION block).
    """
    source = _read_developer_template()
    assert "## DEVELOPMENT RESULT ARTIFACT" in source
    assert "## PARALLEL EXECUTION" in source
    assert source.index("## PARALLEL EXECUTION") < source.index("## DEVELOPMENT RESULT ARTIFACT")
