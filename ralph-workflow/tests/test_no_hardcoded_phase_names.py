from __future__ import annotations

import ast
import pathlib
import tokenize
from io import StringIO

import pytest

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
    return {
        ast.literal_eval(token.string)
        for token in tokenize.generate_tokens(StringIO(source).readline)
        if token.type == tokenize.STRING
    }


def test_string_literal_scan_ignores_comments_regression() -> None:
    assert _string_literals_in_source('# "planning"\n"review"\n') == {"review"}


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


@pytest.fixture(scope="session")
def materialize_literals() -> frozenset[str]:
    source = (RALPH_ROOT / "prompts" / "materialize.py").read_text(encoding="utf-8")
    return frozenset(_string_literals_in_source(source))


class TestArtifactToolHasNoCanonicalDrainNames:
    @pytest.fixture(scope="class")
    def artifact_source(self) -> str:
        return (RALPH_ROOT / "mcp" / "tools" / "artifact.py").read_text(encoding="utf-8")

    def test_analysis_decision_artifact_type_has_no_canonical_drain_literals(
        self, artifact_source: str
    ) -> None:
        literals = _string_literals_in_function(artifact_source, "_analysis_decision_artifact_type")
        canonical_drains = frozenset({"development_analysis", "review_analysis"})
        violations = canonical_drains & literals
        assert not violations, (
            f"_analysis_decision_artifact_type contains canonical drain name literal(s): "
            f"{sorted(violations)}. "
            "Analysis drain derivation must use suffix-based fallback or policy lookup, "
            "not hardcoded drain mappings."
        )


@pytest.mark.timeout_seconds(10)
class TestDisplayLayerHasNoCanonicalPhaseNames:
    @pytest.fixture(scope="class")
    def plain_renderer_source(self) -> str:
        plain_renderer = RALPH_ROOT / "display" / "plain_renderer"
        if plain_renderer.is_dir():
            return "\n".join(
                p.read_text(encoding="utf-8") for p in sorted(plain_renderer.rglob("*.py"))
            )
        if (RALPH_ROOT / "display" / "plain_renderer.py").exists():
            return (RALPH_ROOT / "display" / "plain_renderer.py").read_text(encoding="utf-8")
        return (RALPH_ROOT / "display" / "parallel_display.py").read_text(encoding="utf-8")

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
        phase_banner = RALPH_ROOT / "display" / "phase_banner.py"
        if not phase_banner.exists():
            return (RALPH_ROOT / "display" / "parallel_display.py").read_text(encoding="utf-8")
        return phase_banner.read_text(encoding="utf-8")

    def test_phase_banner_has_no_canonical_phase_pair_table(self, phase_banner_source: str) -> None:
        literals = _string_literals_in_source(phase_banner_source)
        violations = DISPLAY_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"phase display layer contains canonical phase name literal(s): {sorted(violations)}. "
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


class TestHandoffsHasNoCanonicalPhaseNames:
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


class TestMaterializeHasNoCanonicalPhaseNames:
    def test_materialize_module_has_no_canonical_phase_literals(
        self, materialize_literals: frozenset[str]
    ) -> None:
        violations = CANONICAL_PHASE_NAMES & materialize_literals
        assert not violations, (
            f"materialize.py contains canonical phase name literal(s): {sorted(violations)}. "
            "Prompt dispatch must be driven by role and artifact-type from policy only."
        )


class TestRegisterRoleHandlersIsGeneric:
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
    @pytest.fixture(scope="class")
    def artifact_handoff_literals(self) -> set[str]:
        source = (RALPH_ROOT / "pipeline" / "phase_agent_handler.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_render_phase_artifact_handoff"
        )
        return {
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }

    def test_render_phase_artifact_handoff_has_no_canonical_phase_literals(
        self, artifact_handoff_literals: set[str]
    ) -> None:
        literals = artifact_handoff_literals
        violations = RUNNER_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"render_phase_artifact_handoff contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Artifact handoff rendering must use role/artifact-type dispatch, not phase names."
        )


class TestRunnerHasNoCanonicalPhaseNames:
    @pytest.fixture(scope="class")
    def runner_source(self) -> str:
        return (RALPH_ROOT / "pipeline" / "runner.py").read_text(encoding="utf-8")

    @pytest.mark.timeout_seconds(5)
    def test_config_drain_candidates_has_no_suffix_alias_literals(self, runner_source: str) -> None:
        literals = _string_literals_in_function(runner_source, "_config_drain_candidates")
        violations = RUNNER_BANNED_PHASE_NAMES & literals
        assert not violations, (
            f"_config_drain_candidates contains canonical phase name literal(s): "
            f"{sorted(violations)}. "
            "Drain resolution must not expand phase names by suffix — "
            "use the explicit policy drain and phase name only."
        )

    def test_runner_module_has_no_endswith_analysis_or_commit(self, runner_source: str) -> None:
        assert 'endswith("_analysis")' not in runner_source, (
            "runner.py contains suffix-based drain alias logic for '_analysis'. "
            "Remove this compat shim — drain resolution must be policy-only."
        )
        assert 'endswith("_commit")' not in runner_source, (
            "runner.py contains suffix-based drain alias logic for '_commit'. "
            "Remove this compat shim — drain resolution must be policy-only."
        )
