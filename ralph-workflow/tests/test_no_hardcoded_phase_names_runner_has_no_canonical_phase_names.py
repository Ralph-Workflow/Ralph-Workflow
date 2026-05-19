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


class TestRunnerHasNoCanonicalPhaseNames:
    """runner.py drain-candidate logic must not hardcode canonical phase name aliases."""

    @pytest.fixture(scope="class")
    def runner_source(self) -> str:
        return (RALPH_ROOT / "pipeline" / "runner.py").read_text(encoding="utf-8")

    def test_config_drain_candidates_has_no_suffix_alias_literals(self, runner_source: str) -> None:
        """_config_drain_candidates must not expand phase names by suffix."""
        literals = _string_literals_in_function(runner_source, "_config_drain_candidates")
        violations = RUNNER_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"_config_drain_candidates contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Drain resolution must not expand phase names by suffix — "
            "use the explicit policy drain and phase name only."
        )

    def test_runner_module_has_no_endswith_analysis_or_commit(self, runner_source: str) -> None:
        """runner.py must not use endswith('_analysis') or endswith('_commit') for drain routing."""
        assert 'endswith("_analysis")' not in runner_source, (
            "runner.py contains suffix-based drain alias logic for '_analysis'. "
            "Remove this compat shim — drain resolution must be policy-only."
        )
        assert 'endswith("_commit")' not in runner_source, (
            "runner.py contains suffix-based drain alias logic for '_commit'. "
            "Remove this compat shim — drain resolution must be policy-only."
        )
