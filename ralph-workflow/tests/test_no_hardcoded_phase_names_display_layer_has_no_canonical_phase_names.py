"""Guard: routing infrastructure must not hardcode canonical phase names.

Canonical phase names (planning, development_analysis, review_commit, etc.) are
the default names used in the bundled policy. Runtime routing modules must treat
every phase name as an opaque policy-declared identifier so that users can rename
phases without touching any runtime code.

These tests use source-level AST inspection to enforce this invariant.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# Default canonical phase names that MUST NOT appear as special-cased string
# literals in routing infrastructure. Role values ("commit", "analysis") and
# budget-state labels ("remaining", "exhausted") are intentional and allowed.
CANONICAL_PHASE_NAMES = frozenset(
    {
        "development_commit",
        "review_commit",
        "development_analysis",
        "review_analysis",
        "planning",
        "complete",
        "failed",
    }
)

# Broader set used to guard runner.py and other runtime infrastructure.
# Includes common canonical execution-role phase names whose special-casing
# would signal hidden workflow semantics in the runtime.
RUNNER_BANNED_PHASE_NAMES = CANONICAL_PHASE_NAMES | frozenset(
    {
        "development",
        "review",
        "fix",
    }
)

DISPLAY_BANNED_PHASE_NAMES = CANONICAL_PHASE_NAMES

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"


def _string_literals_in_source(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _string_literals_in_function(source: str, function_name: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return {
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
    return set()


class TestDisplayLayerHasNoCanonicalPhaseNames:
    """Display modules must not embed canonical phase name literals as routing keys."""

    @pytest.fixture(scope="class")
    def plain_renderer_source(self) -> str:
        return (RALPH_ROOT / "display" / "plain_renderer.py").read_text(encoding="utf-8")

    def test_plain_renderer_levels_dict_has_no_canonical_phase_keys(
        self, plain_renderer_source: str
    ) -> None:
        literals = _string_literals_in_source(plain_renderer_source)
        violations = DISPLAY_BANNED_PHASE_NAMES & literals
        msg = (
            f"plain_renderer.py contains canonical phase name literal(s): {sorted(violations)}."
            " LEVELS and other display constants must use role keys only."
        )
        assert not violations, msg

    @pytest.fixture(scope="class")
    def phase_banner_source(self) -> str:
        return (RALPH_ROOT / "display" / "phase_banner.py").read_text(encoding="utf-8")

    def test_phase_banner_has_no_canonical_phase_pair_table(self, phase_banner_source: str) -> None:
        literals = _string_literals_in_source(phase_banner_source)
        violations = DISPLAY_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"phase_banner.py contains canonical phase name literal(s): {sorted(violations)}. "
            "_PHASE_STYLES and transition tables must use role keys only. "
            "See docs/sphinx/policy-driven-overhaul-migration.md for migration details."
        )

    @pytest.fixture(scope="class")
    def completion_summary_source(self) -> str:
        return (RALPH_ROOT / "display" / "completion_summary.py").read_text(encoding="utf-8")

    def test_completion_summary_has_no_canonical_phase_fallback_literals(
        self, completion_summary_source: str
    ) -> None:
        literals = _string_literals_in_source(completion_summary_source)
        violations = DISPLAY_BANNED_PHASE_NAMES & literals
        msg = (
            f"completion_summary.py contains canonical phase name literal(s): {sorted(violations)}."
            " _style_for_role and _style_for_terminal_failure must use role keys only."
        )
        assert not violations, msg

    @pytest.fixture(scope="class")
    def run_command_source(self) -> str:
        return (RALPH_ROOT / "cli" / "commands" / "run.py").read_text(encoding="utf-8")

    def test_run_command_dry_run_has_no_canonical_phase_fallback(
        self, run_command_source: str
    ) -> None:
        literals = _string_literals_in_function(run_command_source, "_print_dry_run")
        violations = DISPLAY_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"_print_dry_run contains canonical phase name literal(s): {sorted(violations)}. "
            "Use policy_bundle.pipeline.entry_phase instead of a hardcoded phase name. "
            "See docs/sphinx/policy-driven-overhaul-migration.md for migration details."
        )
