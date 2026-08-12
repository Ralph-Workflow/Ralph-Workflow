"""Acceptance-criteria matrix for the workspace-awareness product criteria.

Renders the canonical AC-1 .. AC-12 mapping from
``.agent/PRODUCT_CRITERIA.md`` to the maintained test tree, writes the
matrix into the deterministic ``tmp_path`` boundary (never the durable
``.agent/evidence/`` path -- the evidence recorder promotes a copy), and
asserts:

1. every AC-1 through AC-12 row is present;
2. every ``Test: tests/...`` reference resolves to a real file under
   ``ralph-workflow/tests/`` (so a typo or an arbitrary string fails);
3. every ``Status:`` token is drawn from the closed vocabulary
   ``{COVERED, GAP, DELIVERED}``;
4. every row carries a ``Delivery: S-N`` step and a ``Verify:`` command
   so the matrix records both the delivery work and the externally
   observable verification method per AC.

The reference table below is the canonical AC-to-test mapping; any
future change must update the table so the matrix cannot lie about
acceptance coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_TESTS_ROOT = _PACKAGE_ROOT / "tests"

_STATUS_VOCABULARY = frozenset({"COVERED", "GAP", "DELIVERED"})

#: Canonical AC-to-test mapping. Each row is
#: ``(criterion, summary, status, delivery_work, verification_method,
#:   (test references relative to the package root))``.
#: ``delivery_work`` names the S-N plan step that closes the gap or
#: maintains coverage. ``verification_method`` names the test or
#: ``make verify`` target. A ``GAP`` row carries an empty reference
#: tuple by definition.
_ACCEPTANCE_ROWS: tuple[
    tuple[str, str, str, str, str, tuple[str, ...]], ...
] = (
    (
        "AC-1",
        "watch use stays bounded: one shared recursive root watch per workspace",
        "COVERED",
        "S-10",
        "tests/agents/test_workspace_cross_process_coordination.py",
        (
            "tests/agents/test_workspace_watch_scoping.py",
            "tests/agents/test_workspace_cross_process_coordination.py",
        ),
    ),
    (
        "AC-2",
        "constrained watch capacity reports an observable live fallback",
        "COVERED",
        "S-10",
        "tests/agents/test_workspace_cross_process_coordination.py",
        (
            "tests/agents/test_workspace_watch_scoping.py",
            "tests/agents/test_workspace_cross_process_coordination.py",
            "tests/test_explore_handlers.py",
        ),
    ),
    (
        "AC-3",
        "an unchanged settled workflow performs no recurring scan, refresh, "
        "rewrite, duplicate observation, or retention re-sweep",
        "COVERED",
        "S-5",
        "tests/test_explore_dirty_paths.py",
        (
            "tests/test_filesystem_activity_baseline.py",
            "tests/test_explore_pipeline.py",
            "tests/test_explore_bench_gates.py",
            "tests/test_explore_dirty_paths.py",
        ),
    ),
    (
        "AC-4",
        "a localized change refreshes only the affected knowledge; a no-change "
        "refresh reprocesses no project content",
        "DELIVERED",
        "S-4",
        "tests/test_explore_localized_change_invariant.py",
        (
            "tests/test_explore_pipeline.py",
            "tests/test_explore_bench_gates.py",
            "tests/test_explore_localized_change_invariant.py",
        ),
    ),
    (
        "AC-5",
        "agents search files, content, symbols, relationships, impact, and "
        "tests with ranked, evidence-backed results",
        "COVERED",
        "S-7",
        "tests/test_explore_rebuild_equivalence.py",
        (
            "tests/test_explore_handlers.py",
            "tests/test_explore_pipeline.py",
            "tests/test_explore_rebuild_equivalence.py",
        ),
    ),
    (
        "AC-6",
        "changed code becomes searchable promptly or the result visibly uses "
        "the correct fallback",
        "COVERED",
        "S-5",
        "tests/test_explore_dirty_paths.py",
        (
            "tests/test_explore_lifecycle.py",
            "tests/test_explore_pipeline.py",
            "tests/test_explore_dirty_paths.py",
        ),
    ),
    (
        "AC-7",
        "search results are deterministic and disclose staleness, coverage "
        "gaps, inference, truncation, and fallback",
        "COVERED",
        "S-7",
        "tests/test_explore_rebuild_equivalence.py",
        (
            "tests/test_explore_handlers.py",
            "tests/test_explore_pipeline.py",
            "tests/test_explore_rebuild_equivalence.py",
        ),
    ),
    (
        "AC-8",
        "deleting and rebuilding derived knowledge produces equivalent results "
        "without harming project content or workflow records",
        "DELIVERED",
        "S-7",
        "tests/test_explore_rebuild_equivalence.py",
        (
            "tests/test_explore_pipeline.py",
            "tests/test_explore_lifecycle.py",
            "tests/test_explore_rebuild_equivalence.py",
        ),
    ),
    (
        "AC-9",
        "concurrent workflows, interruption, cancellation, and restart "
        "preserve ownership, recoverable state, and safe cleanup",
        "DELIVERED",
        "S-10",
        "tests/agents/test_workspace_cross_process_coordination.py",
        (
            "tests/unit/test_agent_dir_retention.py",
            "tests/test_filesystem_activity_baseline.py",
            "tests/agents/test_workspace_cross_process_coordination.py",
        ),
    ),
    (
        "AC-10",
        "every storage category reaches a bounded steady state and cleanup "
        "reclaims eligible data safely",
        "DELIVERED",
        "S-11",
        "tests/unit/test_storage_lifecycle.py",
        (
            "tests/unit/test_storage_lifecycle.py",
            "tests/unit/test_agent_dir_retention.py",
        ),
    ),
    (
        "AC-11",
        "workspace health, storage use, watch pressure, freshness, active "
        "maintenance, degraded state, and cleanup eligibility are visible "
        "without inspecting internal files",
        "COVERED",
        "S-12",
        "tests/test_cli_workspace_health.py",
        (
            "tests/test_cli_workspace_health.py",
            "tests/test_filesystem_scenario_baseline.py",
        ),
    ),
    (
        "AC-12",
        "representative long-running workloads do not cause sustained "
        "host-resource pressure attributable to avoidable filesystem activity",
        "DELIVERED",
        "S-12",
        "tests/test_filesystem_scenario_baseline.py",
        (
            "tests/test_filesystem_activity_baseline.py",
            "tests/test_explore_bench_gates.py",
            "tests/test_filesystem_scenario_baseline.py",
        ),
    ),
)

_MATRIX_HEADER = (
    "# Acceptance criteria matrix\n"
    "\n"
    "Derived from `.agent/PRODUCT_CRITERIA.md` by "
    "`ralph-workflow/tests/test_acceptance_criteria_matrix.py`.\n"
)


def _render_row(
    criterion: str,
    summary: str,
    status: str,
    delivery_work: str,
    verification_method: str,
    references: tuple[str, ...],
) -> str:
    lines = [
        f"## {criterion}: {summary}",
        f"Status: {status}",
        f"Delivery: {delivery_work}",
        f"Verify: {verification_method}",
    ]
    lines.extend(f"Test: {reference}" for reference in references)
    return "\n".join(lines)


def render_acceptance_matrix() -> str:
    """Render the full 12-row acceptance matrix as a markdown string."""
    body = "\n\n".join(
        _render_row(criterion, summary, status, delivery_work, verification_method, references)
        for criterion, summary, status, delivery_work, verification_method, references in _ACCEPTANCE_ROWS
    )
    return f"{_MATRIX_HEADER}\n{body}\n"


def test_acceptance_matrix_renders_inside_tmp_path(tmp_path: Path) -> None:
    """Every AC row is present, references real tests, and uses the closed vocabulary."""
    matrix = render_acceptance_matrix()
    destination = tmp_path / "acceptance-criteria-matrix.md"
    destination.write_text(matrix, encoding="utf-8")

    rendered = destination.read_text(encoding="utf-8")
    for index in range(1, 13):
        assert f"AC-{index}:" in rendered, f"AC-{index} row missing from the matrix"

    status_tokens = re.findall(r"^Status: (\w+)$", rendered, flags=re.MULTILINE)
    assert len(status_tokens) == 12
    assert set(status_tokens) <= _STATUS_VOCABULARY

    delivery_tokens = re.findall(r"^Delivery: (S-\d+)$", rendered, flags=re.MULTILINE)
    assert len(delivery_tokens) == 12, (
        f"expected 12 Delivery lines, found {len(delivery_tokens)}"
    )

    verify_tokens = re.findall(r"^Verify: (.+)$", rendered, flags=re.MULTILINE)
    assert len(verify_tokens) == 12, (
        f"expected 12 Verify lines, found {len(verify_tokens)}"
    )

    references = re.findall(r"^Test: (tests/\S+)$", rendered, flags=re.MULTILINE)
    assert references, "the matrix must name at least one test reference"
    for reference in references:
        assert (_PACKAGE_ROOT / reference).is_file(), (
            f"matrix references a missing test file: {reference}"
        )

    # The deterministic boundary: this test only writes inside tmp_path;
    # the durable evidence copy is produced solely by S-7's recorder.
    assert str(destination).startswith(str(tmp_path))


def test_matrix_table_is_canonical() -> None:
    """The shipped table covers exactly AC-1 .. AC-12 with the closed vocabulary."""
    assert [row[0] for row in _ACCEPTANCE_ROWS] == [f"AC-{index}" for index in range(1, 13)]
    for (
        _criterion,
        _summary,
        status,
        delivery_work,
        _verification_method,
        references,
    ) in _ACCEPTANCE_ROWS:
        assert status in _STATUS_VOCABULARY
        assert re.match(r"^S-\d+$", delivery_work), (
            f"delivery_work must match S-\\d+ pattern, got {delivery_work!r}"
        )
        for reference in references:
            assert reference.startswith("tests/")
            assert (_TESTS_ROOT / reference[len("tests/") :]).is_file()
