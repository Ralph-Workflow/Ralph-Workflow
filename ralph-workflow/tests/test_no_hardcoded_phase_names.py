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
CANONICAL_PHASE_NAMES = frozenset({
    "development_commit",
    "review_commit",
    "development_analysis",
    "review_analysis",
    "planning",
    "complete",
    "failed",
})

# Broader set used to guard runner.py and other runtime infrastructure.
# Includes common canonical execution-role phase names whose special-casing
# would signal hidden workflow semantics in the runtime.
RUNNER_BANNED_PHASE_NAMES = CANONICAL_PHASE_NAMES | frozenset({
    "development",
    "review",
    "fix",
})

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


class TestHandoffsHasNoCanonicalPhaseNames:
    """handoffs.py routing is fully generic — no canonical phase name literals allowed."""

    @pytest.fixture(scope="class")
    def handoffs_source(self) -> str:
        return (RALPH_ROOT / "pipeline" / "handoffs.py").read_text(encoding="utf-8")

    def test_resolve_next_phase_has_no_canonical_phase_literals(
        self, handoffs_source: str
    ) -> None:
        literals = _string_literals_in_function(handoffs_source, "resolve_next_phase")
        violations = CANONICAL_PHASE_NAMES & literals
        assert not violations, (
            f"resolve_next_phase contains canonical phase name literal(s): {sorted(violations)}. "
            "Phase routing must be driven by policy-declared names only."
        )

    def test_resolve_post_commit_phase_has_no_canonical_phase_literals(
        self, handoffs_source: str
    ) -> None:
        literals = _string_literals_in_function(handoffs_source, "resolve_post_commit_phase")
        violations = CANONICAL_PHASE_NAMES & literals
        assert not violations, (
            f"resolve_post_commit_phase contains canonical phase name literal(s): "
            f"{sorted(violations)}. Commit routing must be generic."
        )

    def test_handoffs_module_has_no_canonical_phase_literals(
        self, handoffs_source: str
    ) -> None:
        literals = _string_literals_in_source(handoffs_source)
        violations = CANONICAL_PHASE_NAMES & literals
        assert not violations, (
            f"handoffs.py module contains canonical phase name literal(s): {sorted(violations)}. "
            "The routing module must treat all phase names as opaque policy identifiers."
        )


class TestRunnerHasNoCanonicalPhaseNames:
    """runner.py drain-candidate logic must not hardcode canonical phase name aliases."""

    @pytest.fixture(scope="class")
    def runner_source(self) -> str:
        return (RALPH_ROOT / "pipeline" / "runner.py").read_text(encoding="utf-8")

    def test_config_drain_candidates_has_no_suffix_alias_literals(
        self, runner_source: str
    ) -> None:
        """_config_drain_candidates must not expand phase names by suffix."""
        literals = _string_literals_in_function(runner_source, "_config_drain_candidates")
        violations = RUNNER_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"_config_drain_candidates contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Drain resolution must not expand phase names by suffix — "
            "use the explicit policy drain and phase name only."
        )

    def test_runner_module_has_no_endswith_analysis_or_commit(
        self, runner_source: str
    ) -> None:
        """runner.py must not use endswith('_analysis') or endswith('_commit') for drain routing."""
        assert "endswith(\"_analysis\")" not in runner_source, (
            "runner.py contains suffix-based drain alias logic for '_analysis'. "
            "Remove this compat shim — drain resolution must be policy-only."
        )
        assert "endswith(\"_commit\")" not in runner_source, (
            "runner.py contains suffix-based drain alias logic for '_commit'. "
            "Remove this compat shim — drain resolution must be policy-only."
        )


class TestMaterializeHasNoCanonicalPhaseNames:
    """materialize.py prompt dispatch must not hardcode canonical phase names."""

    @pytest.fixture(scope="class")
    def materialize_source(self) -> str:
        return (RALPH_ROOT / "prompts" / "materialize.py").read_text(encoding="utf-8")

    def test_materialize_module_has_no_canonical_phase_literals(
        self, materialize_source: str
    ) -> None:
        literals = _string_literals_in_source(materialize_source)
        # Use CANONICAL_PHASE_NAMES (not RUNNER_BANNED_PHASE_NAMES) because
        # "review" is also a valid role value used in phase_role checks.
        violations = CANONICAL_PHASE_NAMES & literals
        assert not violations, (
            f"materialize.py contains canonical phase name literal(s): {sorted(violations)}. "
            "Prompt dispatch must be driven by role and artifact-type from policy only."
        )


class TestRegisterRoleHandlersIsGeneric:
    """register_role_handlers must not check for canonical phase names."""

    @pytest.fixture(scope="class")
    def init_source(self) -> str:
        return (RALPH_ROOT / "phases" / "__init__.py").read_text(encoding="utf-8")

    def test_register_role_handlers_has_no_canonical_phase_literals(
        self, init_source: str
    ) -> None:
        literals = _string_literals_in_function(init_source, "register_role_handlers")
        violations = CANONICAL_PHASE_NAMES & literals
        assert not violations, (
            f"register_role_handlers contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Handler registration must be driven by role values only."
        )
