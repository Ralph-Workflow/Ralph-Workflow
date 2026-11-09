"""Moved-rule preservation guard for the slimmed root ``AGENTS.md``.

The root ``AGENTS.md`` keeps the ABSOLUTE rules (fabrication guard, test
budget, no unrelated-failure exemption, trunk-based development, zero dead
code, commit rule) as short statements under their original headings, and
delegates the DETAIL to the owning documents:

* ``docs/agents/verification.md`` — the import-time invariant table, the
  lint / typecheck / MCP-timeout / resource-accumulator non-circumvention
  lists, and the slow-test remedies.
* ``docs/agents/fabrication-guard.md`` — the mandatory guard protocol.

This test proves the move did not DROP a rule: every moved rule still exists
in its new home, and the detail is not silently duplicated back into
``AGENTS.md``. It reads the shipped repository documents because their literal
contents are the regression contract — a fixture copy would assert against the
fixture, not the shipped files.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_MD = _REPO_ROOT / "AGENTS.md"
_VERIFICATION_MD = _REPO_ROOT / "docs" / "agents" / "verification.md"
_FABRICATION_GUARD_MD = _REPO_ROOT / "docs" / "agents" / "fabrication-guard.md"

#: The whole-file ceiling for a project ``AGENTS.md``. Judgment-based decided
#: value (evidence tag ``none``), advisory: it is not a hard gate, but the
#: slimmed root file must stay under it so the absolute rules remain the
#: load-bearing content.
_PROJECT_AGENTS_MD_MAX_LINES = 200

#: Absolute headings that must survive the slimming: they are the anchors an
#: agent scans for when it needs the rule that matters.
_REQUIRED_ABSOLUTE_HEADINGS: tuple[str, ...] = (
    "### ═══ NO UNRELATED-FAILURE EXEMPTION — YOU FIND IT, YOU FIX IT ═══",
    "### ═══ FABRICATION GUARD (ABSOLUTE — 3 levels, zero bypass) ═══",
    "### ═══ TEST BUDGET — 60s wall clock net of host contention, 700 CPU core-seconds, PINNED ═══",
    "### ═══ TRUNK-BASED DEVELOPMENT ONLY — EVERYTHING GOES TO MAIN ═══",
    "## Absolutely Zero Dead code",
    "## Commit rule",
)

#: The absolute statements that stay in ``AGENTS.md`` in short form.
_REQUIRED_ABSOLUTE_STATEMENTS: tuple[str, ...] = (
    "There is NO exemption for a failure your",
    "A red gate is a red gate.",
    "Fabrication is NEVER acceptable anywhere. No file is out of scope for truth.",
    "be paired with a (source, date) pair.",
    "never use `--no-verify` to bypass it",
    "COMBINED TOTAL wall-clock budget for ALL test suites",
    "budget failure is a test design defect",
    "Zero tolerance for any type of dead code.",
    "use `ralph --generate-commit` ONLY and nothing else",
)

#: Every moved rule, as the substance that must survive in its new home.
#: ``(destination, required substrings)`` — the destination is a path relative
#: to the repository root.
_MOVED_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "docs/agents/verification.md",
        (
            # each row carries its invariant, its error message and its purpose
            "`_TOTAL_TEST_BUDGET_SECONDS > 0`",
            "`must be 60.0`",
            "`must be 700.0`",
            "`must be 3 x _TOTAL_TEST_BUDGET_SECONDS`",
            "`indices must be valid`",
            "`must have a positive timeout`",
            "`must not be empty`",
            "`must contain 'make test'`",
            "`must be at least 5.0`",
            # budget non-circumvention list
            "`_KNOWN_TEST_STEP_LABELS`",
            "`_BUDGET_TRACKED_STEPS`",
            "`PYTEST_SUITE_TIMEOUT_SECONDS`",
            "`RALPH_PYTEST_TEST_TIMEOUT_SECONDS`",
            "`SIGPROF`",
            # lint / typecheck non-circumvention list
            "`per-file-ignores`",
            "`ignore_missing_imports`",
            "`# type: " + "ignore`",
            "`docs/agents/type-ignore-policy.md`",
            "`ralph/testing/audit_lint_bypass.py`",
            "`ralph/testing/audit_typecheck_bypass.py`",
            "`ralph/testing/audit_test_policy.py`",
            # MCP timeout contract
            "`ralph/testing/audit_mcp_timeout.py`",
            "`# mcp-timeout-ok: <reason>`",
            # resource accumulators
            "`ralph/testing/audit_resource_lifecycle.py`",
            "`# bounded-accumulator-ok:`",
            # slow-test remedies
            "## How to fix a slow test",
            "`MemoryWorkspace`",
            "`MockProcessExecutor`",
            "inject a clock abstraction",
            "`tmp_path`",
        ),
    ),
    (
        "docs/agents/fabrication-guard.md",
        (
            "## Mandatory protocol",
            "./scripts/fabrication_guard.py --level 1 <file>",
            "./scripts/fabrication_guard.py --level 2 <file>",
            "do **not** weaken the",
            "do **not** use `--no-verify` to bypass it",
        ),
    ),
)

#: Detail that must NOT have been left behind in ``AGENTS.md``. These are the
#: verbatim fragments of the moved tables; their absence proves the slimming
#: moved the detail instead of copying it.
_DETAIL_MUST_BE_GONE_FROM_AGENTS_MD: tuple[str, ...] = (
    "must be 60.0",
    "must be 700.0",
    "must be 3 x _TOTAL_TEST_BUDGET_SECONDS",
    "indices must be valid",
    "must contain 'make test'",
    "per-file-ignores",
    "ignore_missing_imports",
    "bounded-accumulator-ok",
    "mcp-timeout-ok",
)


def _read(path: Path) -> str:
    """Return the text of a shipped repository document."""
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_text(encoding="utf-8")


def test_root_agents_md_keeps_its_absolute_headings() -> None:
    """The absolute rules keep their headings so they stay scannable."""
    text = _read(_AGENTS_MD)
    missing = [heading for heading in _REQUIRED_ABSOLUTE_HEADINGS if heading not in text]
    assert not missing, f"AGENTS.md lost absolute headings: {missing}"


def test_root_agents_md_keeps_its_absolute_statements() -> None:
    """The absolute rules keep their substance in short form."""
    text = _read(_AGENTS_MD)
    missing = [statement for statement in _REQUIRED_ABSOLUTE_STATEMENTS if statement not in text]
    assert not missing, f"AGENTS.md lost absolute statements: {missing}"


def test_root_agents_md_stays_under_the_project_ceiling() -> None:
    """The slimmed file stays under the decided whole-file ceiling."""
    line_count = len(_read(_AGENTS_MD).splitlines())
    assert line_count <= _PROJECT_AGENTS_MD_MAX_LINES, (
        f"AGENTS.md has {line_count} lines, over the {_PROJECT_AGENTS_MD_MAX_LINES}-line ceiling"
    )


def test_moved_detail_is_not_left_in_agents_md() -> None:
    """The detail was MOVED, not duplicated: the tables are gone from AGENTS.md."""
    text = _read(_AGENTS_MD)
    still_present = [
        fragment for fragment in _DETAIL_MUST_BE_GONE_FROM_AGENTS_MD if fragment in text
    ]
    assert not still_present, f"AGENTS.md still carries moved detail: {still_present}"


def test_every_moved_rule_exists_in_its_new_home() -> None:
    """Every moved rule survives verbatim in substance in its new home."""
    for relative_path, required in _MOVED_RULES:
        text = _read(_REPO_ROOT / relative_path)
        missing = [fragment for fragment in required if fragment not in text]
        assert not missing, f"{relative_path} lost moved rule substance: {missing}"


def test_agents_md_points_at_the_owning_documents() -> None:
    """AGENTS.md routes the reader to the owning document for the detail."""
    text = _read(_AGENTS_MD)
    assert "docs/agents/verification.md" in text
    assert "docs/agents/fabrication-guard.md" in text
