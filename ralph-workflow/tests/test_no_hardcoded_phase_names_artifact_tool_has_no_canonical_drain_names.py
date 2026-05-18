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


class TestArtifactToolHasNoCanonicalDrainNames:
    """_analysis_decision_artifact_type must not hardcode canonical drain name literals."""

    @pytest.fixture(scope="class")
    def artifact_source(self) -> str:
        return (RALPH_ROOT / "mcp" / "tools" / "artifact.py").read_text(encoding="utf-8")

    def test_analysis_decision_artifact_type_has_no_canonical_drain_literals(
        self, artifact_source: str
    ) -> None:
        literals = _string_literals_in_function(artifact_source, "_analysis_decision_artifact_type")
        # "development_analysis" and "review_analysis" must not appear as literals
        # since the function now uses suffix-based fallback or policy-based derivation
        canonical_drains = frozenset({"development_analysis", "review_analysis"})
        violations = canonical_drains & literals
        assert not violations, (
            f"_analysis_decision_artifact_type contains canonical drain name literal(s): "
            f"{sorted(violations)}. "
            "Analysis drain derivation must use suffix-based fallback or policy lookup, "
            "not hardcoded drain mappings."
        )
