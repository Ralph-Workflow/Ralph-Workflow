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


class TestHandoffsHasNoCanonicalPhaseNames:
    """handoffs.py routing is fully generic — no canonical phase name literals allowed."""

    @pytest.fixture(scope="class")
    def handoffs_source(self) -> str:
        return (RALPH_ROOT / "pipeline" / "handoffs.py").read_text(encoding="utf-8")

    def test_resolve_next_phase_has_no_canonical_phase_literals(self, handoffs_source: str) -> None:
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

    def test_handoffs_module_has_no_canonical_phase_literals(self, handoffs_source: str) -> None:
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

    def test_register_role_handlers_has_no_canonical_phase_literals(self, init_source: str) -> None:
        literals = _string_literals_in_function(init_source, "register_role_handlers")
        violations = CANONICAL_PHASE_NAMES & literals
        assert not violations, (
            f"register_role_handlers contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Handler registration must be driven by role values only."
        )


class TestRunnerArtifactHandoffIsGeneric:
    """_render_phase_artifact_handoff must not hardcode canonical phase name literals."""

    @pytest.fixture(scope="class")
    def runner_source(self) -> str:
        return (RALPH_ROOT / "pipeline" / "runner.py").read_text(encoding="utf-8")

    def test_render_phase_artifact_handoff_has_no_canonical_phase_literals(
        self, runner_source: str
    ) -> None:
        literals = _string_literals_in_function(runner_source, "_render_phase_artifact_handoff")
        violations = RUNNER_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"_render_phase_artifact_handoff contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Artifact handoff rendering must use role/artifact-type dispatch, not phase names."
        )


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


# Names that are EXCLUSIVELY canonical phase names and can never appear as
# legitimate role values, decision outcomes, or other non-phase strings in
# the display layer. "complete" and "failed" are excluded here because they
# also appear as decision outcome keys in _DECISION_LABELS — the guard targets
# only phase-name-based routing/styling fallbacks.
DISPLAY_BANNED_PHASE_NAMES = frozenset(
    {
        "planning",
        "development",
        "development_analysis",
        "development_commit",
        "review_analysis",
        "review_commit",
        "failed_terminal",
    }
)


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
