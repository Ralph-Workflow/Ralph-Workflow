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


class TestRunnerArtifactHandoffIsGeneric:
    """render_phase_artifact_handoff must not hardcode canonical phase name literals."""

    @pytest.fixture(scope="class")
    def activity_stream_source(self) -> str:
        return (RALPH_ROOT / "pipeline" / "activity_stream.py").read_text(encoding="utf-8")

    def test_render_phase_artifact_handoff_has_no_canonical_phase_literals(
        self, activity_stream_source: str
    ) -> None:
        literals = _string_literals_in_function(
            activity_stream_source, "render_phase_artifact_handoff"
        )
        violations = RUNNER_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"render_phase_artifact_handoff contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Artifact handoff rendering must use role/artifact-type dispatch, not phase names."
        )
