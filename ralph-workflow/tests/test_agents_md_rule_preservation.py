from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_MD = _REPO_ROOT / "AGENTS.md"
_VERIFICATION_MD = _REPO_ROOT / "docs" / "agents" / "verification.md"
_FABRICATION_GUARD_MD = _REPO_ROOT / "docs" / "agents" / "fabrication-guard.md"

_PROJECT_AGENTS_MD_MAX_LINES = 200

_REQUIRED_ABSOLUTE_HEADINGS: tuple[str, ...] = (
    "### ═══ NO UNRELATED-FAILURE EXEMPTION — YOU FIND IT, YOU FIX IT ═══",
    "### ═══ FABRICATION GUARD (ABSOLUTE — 3 levels, zero bypass) ═══",
    "### ═══ TEST BUDGET — 60s wall clock net of host contention, 700 CPU core-seconds, PINNED ═══",
    "### ═══ TRUNK-BASED DEVELOPMENT ONLY — EVERYTHING GOES TO MAIN ═══",
    "## Absolutely Zero Dead code",
    "## Commit rule",
)

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

_MOVED_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "docs/agents/verification.md",
        (
            "`_TOTAL_TEST_BUDGET_SECONDS > 0`",
            "`must be positive`",
            "`abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9`",
            "`must be 60.0`",
            "`abs(_TOTAL_TEST_CPU_BUDGET_SECONDS - 700.0) < 1e-9`",
            "`must be 700.0`",
            "`_TOTAL_TEST_HANG_CAP_SECONDS == 3 * _TOTAL_TEST_BUDGET_SECONDS`",
            "`must be 3 x _TOTAL_TEST_BUDGET_SECONDS`",
            "`_BUDGET_TRACKED_STEPS` indices are valid indices into `_VERIFY_STEPS`",
            "`indices must be valid`",
            "Every budget-tracked step has a positive timeout",
            "`must have a positive timeout`",
            "`_VERIFY_STEP_TIMEOUT_SECONDS > 0`",
            "`_VERIFY_STEP_TIMEOUT_SECONDS >= 5.0`",
            "`must be at least 5.0`",
            "`_KNOWN_TEST_STEP_LABELS` non-empty",
            "`must not be empty`",
            "`_BUDGET_TRACKED_STEPS` non-empty",
            "`'make test'` present in `_KNOWN_TEST_STEP_LABELS`",
            "`must contain 'make test'`",
            "Labels/steps sync enforced",
            "`must be` / `must NOT be`",
            "Every label in `_KNOWN_TEST_STEP_LABELS` is tracked",
            "`_KNOWN_TEST_STEP_LABELS`",
            "`_BUDGET_TRACKED_STEPS`",
            "`PYTEST_SUITE_TIMEOUT_SECONDS`",
            "`RALPH_PYTEST_TEST_TIMEOUT_SECONDS`",
            "`SIGPROF`",
            "`per-file-ignores`",
            "`ignore_missing_imports`",
            "`docs/agents/type-ignore-policy.md`",
            "`ralph/testing/audit_lint_bypass.py`",
            "`ralph/testing/audit_typecheck_bypass.py`",
            "`ralph/testing/audit_test_policy.py`",
            "`ralph/testing/audit_mcp_timeout.py`",
            "`ralph/testing/audit_resource_lifecycle.py`",
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
            "Before editing",
            "./scripts/fabrication_guard.py --level 1 <file>",
            "Adding NEW external references",
            "./scripts/fabrication_guard.py --level 2 <file>",
            "After editing",
            "re-run Level 1",
            "If ANY level fails",
            "do **not** weaken the",
            "do **not** use `--no-verify` to bypass it",
        ),
    ),
)

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
    "./scripts/fabrication_guard.py --level 1 <file>",
    "--level 2",
    "Mandatory protocol",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_text(encoding="utf-8")


def test_root_agents_md_keeps_its_absolute_headings() -> None:
    text = _read(_AGENTS_MD)
    missing = [heading for heading in _REQUIRED_ABSOLUTE_HEADINGS if heading not in text]
    assert not missing, f"AGENTS.md lost absolute headings: {missing}"


def test_root_agents_md_keeps_its_absolute_statements() -> None:
    text = _read(_AGENTS_MD)
    missing = [statement for statement in _REQUIRED_ABSOLUTE_STATEMENTS if statement not in text]
    assert not missing, f"AGENTS.md lost absolute statements: {missing}"


def test_root_agents_md_stays_under_the_project_ceiling() -> None:
    line_count = len(_read(_AGENTS_MD).splitlines())
    assert line_count <= _PROJECT_AGENTS_MD_MAX_LINES, (
        f"AGENTS.md has {line_count} lines, over the {_PROJECT_AGENTS_MD_MAX_LINES}-line ceiling"
    )


def test_moved_detail_is_not_left_in_agents_md() -> None:
    text = _read(_AGENTS_MD)
    still_present = [
        fragment for fragment in _DETAIL_MUST_BE_GONE_FROM_AGENTS_MD if fragment in text
    ]
    assert not still_present, f"AGENTS.md still carries moved detail: {still_present}"


def test_every_moved_rule_exists_in_its_new_home() -> None:
    for relative_path, required in _MOVED_RULES:
        text = _read(_REPO_ROOT / relative_path)
        missing = [fragment for fragment in required if fragment not in text]
        assert not missing, f"{relative_path} lost moved rule substance: {missing}"


def test_agents_md_points_at_the_owning_documents() -> None:
    text = _read(_AGENTS_MD)
    assert "docs/agents/verification.md" in text
    assert "docs/agents/fabrication-guard.md" in text


def test_agents_md_managed_block_contains_routing() -> None:
    text = _read(_AGENTS_MD)
    assert "execution-kernel.md" in text
    assert "role slice" in text
    assert "load policies in this order" in text
