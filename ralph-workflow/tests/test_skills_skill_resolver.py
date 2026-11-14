from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.prompt_prep import (
    materialize_agent_prompt_if_needed,
    materialize_prepared_prompt,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.prompts import template_engine
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_rendering_error import TemplateRenderingError
from ralph.prompts.types import SessionCapabilities
from ralph.skills._agent_paths import AgentSkillRoot
from ralph.skills._ownership import manifest_digest
from ralph.skills._skill_resolver import (
    FROZEN_UNIVERSAL_SKILLS,
    get_inline_skill_content,
    resolve_agent_skill_roots,
    resolve_installed_skills_for_agent,
    skill_template_variables,
    skill_variable_name,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.policy.models import PolicyBundle


def _install_skill_in_root(root: Path, skill_name: str, content: str = "Test skill body") -> None:
    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    files = {"SKILL.md": digest}
    marker = {
        "managed_by": "ralph-universal",
        "installed_sha256": manifest_digest(files),
        "files": files,
    }
    (skill_dir / ".ralph-managed.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )


def _load_test_policy_bundle() -> PolicyBundle:
    defaults_dir = (
        Path(__file__).resolve().parents[1] / "ralph-workflow" / "ralph" / "policy" / "defaults"
    )
    if not defaults_dir.exists():
        defaults_dir = Path(__file__).resolve().parents[0].parent / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


class _TestAgentRegistry:
    def get(self, name: str) -> AgentConfig:
        return AgentConfig(
            cmd=name,
            transport=AgentTransport.CLAUDE,
            subagent_capability=True,
        )


def _setup_two_agent_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    root_a = tmp_path / "claude_skills"
    root_b = tmp_path / "codex_skills"
    root_a.mkdir(parents=True, exist_ok=True)
    root_b.mkdir(parents=True, exist_ok=True)

    _install_skill_in_root(root_a, "test-driven-development")
    _install_skill_in_root(root_a, "verification-before-completion")

    fake_roots = (
        AgentSkillRoot(
            agent="agent_a",
            path_segments=("claude_skills",),
            source_url="https://example.com/a",
            is_canonical=True,
        ),
        AgentSkillRoot(
            agent="agent_b",
            path_segments=("codex_skills",),
            source_url="https://example.com/b",
            is_canonical=False,
        ),
    )
    monkeypatch.setattr("ralph.skills._skill_resolver.agent_skill_roots", lambda: fake_roots)
    monkeypatch.setattr("ralph.skills._agent_paths.agent_skill_roots", lambda: fake_roots)
    roots_by_agent: dict[str, Path] = {"agent_a": root_a, "agent_b": root_b}

    def _mock_resolve(root_obj: AgentSkillRoot) -> Path:
        return roots_by_agent.get(root_obj.agent, root_a)

    monkeypatch.setattr(AgentSkillRoot, "resolve", _mock_resolve)
    return root_a, root_b


def test_returns_empty_when_env_var_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.delenv("RALPH_INLINE_SKILLS_DIR", raising=False)
    assert get_inline_skill_content() == ""


def test_returns_empty_when_dir_not_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path / "missing"))
    assert get_inline_skill_content() == ""


def test_returns_empty_when_dir_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    assert get_inline_skill_content() == ""


def test_returns_empty_when_only_process_dir_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "b.md").write_text("BBB", encoding="utf-8")
    (tmp_path / "a.md").write_text("AAA", encoding="utf-8")
    monkeypatch.setenv("RALPH_SKILLS_PROCESS_DIR", str(tmp_path))
    monkeypatch.delenv("RALPH_INLINE_SKILLS_DIR", raising=False)
    assert get_inline_skill_content() == ""


def test_returns_combined_content_from_md_files_in_sorted_order_when_inline_dir_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "b.md").write_text("BBB", encoding="utf-8")
    (tmp_path / "a.md").write_text("AAA", encoding="utf-8")
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    assert get_inline_skill_content() == "AAA\n\n---\n\nBBB"


def test_ignores_non_md_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "skip.txt").write_text("NOPE", encoding="utf-8")
    (tmp_path / "keep.md").write_text("KEEP", encoding="utf-8")
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    assert get_inline_skill_content() == "KEEP"


def test_frozen_universal_skills_contains_exact_settled_names() -> None:
    assert FROZEN_UNIVERSAL_SKILLS == (
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
        "antislop",
    )


def test_skill_variable_name_transforms_to_upper_snake_case() -> None:
    assert (
        skill_variable_name("test-driven-development") == "SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"
    )
    assert skill_variable_name("systematic-debugging") == "SKILL_INSTALLED_SYSTEMATIC_DEBUGGING"
    assert skill_variable_name("verification-before-completion") == (
        "SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"
    )
    assert skill_variable_name("antislop") == "SKILL_INSTALLED_ANTISLOP"


def test_skill_template_variables_all_empty_when_no_skills_installed() -> None:
    vars_map = skill_template_variables()
    assert vars_map == {
        "SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT": "",
        "SKILL_INSTALLED_SYSTEMATIC_DEBUGGING": "",
        "SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION": "",
        "SKILL_INSTALLED_ANTISLOP": "",
    }


def test_skill_template_variables_sets_true_for_installed_subset() -> None:
    vars_map = skill_template_variables(["test-driven-development", "antislop"])
    assert vars_map == {
        "SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT": "true",
        "SKILL_INSTALLED_SYSTEMATIC_DEBUGGING": "",
        "SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION": "",
        "SKILL_INSTALLED_ANTISLOP": "true",
    }


def test_skill_template_variables_injectable_candidates() -> None:
    candidates = ("custom-skill-one", "custom-skill-two")
    vars_map = skill_template_variables(["custom-skill-one"], candidate_skills=candidates)
    assert vars_map == {
        "SKILL_INSTALLED_CUSTOM_SKILL_ONE": "true",
        "SKILL_INSTALLED_CUSTOM_SKILL_TWO": "",
    }


def test_resolve_agent_skill_roots_empty_for_missing_agent() -> None:
    assert resolve_agent_skill_roots(None) == ()
    assert resolve_agent_skill_roots("") == ()


def test_resolve_installed_skills_for_agent_when_agent_none() -> None:
    assert resolve_installed_skills_for_agent(None) == set()


def test_resolve_installed_skills_valid_marker_and_manifest(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills"
    _install_skill_in_root(skill_root, "test-driven-development")
    _install_skill_in_root(skill_root, "antislop")

    installed = resolve_installed_skills_for_agent(
        "test-agent",
        roots=[skill_root],
    )
    assert installed == {"test-driven-development", "antislop"}


def test_resolve_installed_skills_rejects_corrupted_or_mismatched_marker(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills"
    bad_json = skill_root / "test-driven-development"
    bad_json.mkdir(parents=True)
    (bad_json / "SKILL.md").write_text("Hello", encoding="utf-8")
    (bad_json / ".ralph-managed.json").write_text("invalid json", encoding="utf-8")

    tampered = skill_root / "antislop"
    _install_skill_in_root(skill_root, "antislop", content="original")
    (tampered / "SKILL.md").write_text("tampered content", encoding="utf-8")

    foreign = skill_root / "systematic-debugging"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("other", encoding="utf-8")
    foreign_payload: dict[str, str] = {"managed_by": "untrusted"}
    (foreign / ".ralph-managed.json").write_text(
        json.dumps(foreign_payload),
        encoding="utf-8",
    )

    installed = resolve_installed_skills_for_agent("test-agent", roots=[skill_root])
    assert installed == set()


def test_resolve_installed_skills_handles_symlink_to_canonical(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    sibling = tmp_path / "sibling"
    _install_skill_in_root(canonical, "test-driven-development")

    sibling.mkdir(parents=True)
    (sibling / "test-driven-development").symlink_to(
        canonical / "test-driven-development",
        target_is_directory=True,
    )

    installed = resolve_installed_skills_for_agent("sibling-agent", roots=[sibling])
    assert installed == {"test-driven-development"}


def test_integrated_materialize_prepared_prompt_two_agents_no_leak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "PROMPT.md").write_text("Build the user request", encoding="utf-8")
    (workspace_dir / ".agent").mkdir(parents=True)
    (workspace_dir / ".agent" / "PRODUCT_CRITERIA.md").write_text("Requirements", encoding="utf-8")

    _setup_two_agent_roots(monkeypatch, tmp_path)
    bundle = _load_test_policy_bundle()
    registry = _TestAgentRegistry()

    captured_renders: list[Mapping[str, str]] = []
    original_render_template = template_engine.render_template

    def _render_spy(
        template: str,
        variables: Mapping[str, str],
        partials: Mapping[str, str],
    ) -> str:
        captured_renders.append(dict(variables))
        return original_render_template(template, variables, partials)

    monkeypatch.setattr(template_engine, "render_template", _render_spy)
    monkeypatch.setattr("ralph.prompts.developer.render_template", _render_spy)
    monkeypatch.setattr("ralph.prompts.materialize.render_template", _render_spy)

    def _agent_a_list(*_a: object, **_kw: object) -> list[str]:
        return ["agent_a"]

    def _agent_b_list(*_a: object, **_kw: object) -> list[str]:
        return ["agent_b"]

    state = PipelineState(phase="planning", previous_phase=None)
    test_config = UnifiedConfig.model_construct()

    # 1. Agent A has test-driven-development and verification-before-completion
    captured_renders.clear()
    monkeypatch.setattr("ralph.pipeline.prompt_prep.agents_for_phase", _agent_a_list)
    materialize_prepared_prompt(
        PreparePromptEffect(phase="planning", drain="planning"),
        pipeline_policy=bundle.pipeline,
        artifacts_policy=bundle.artifacts,
        workspace_scope=WorkspaceScope(workspace_dir),
        agents_policy=bundle.agents,
        state=state,
        registry=registry,
        config=test_config,
    )
    assert len(captured_renders) >= 1
    vars_a = captured_renders[-1]
    assert vars_a["SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"] == "true"
    assert vars_a["SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"] == "true"
    assert vars_a["SKILL_INSTALLED_SYSTEMATIC_DEBUGGING"] == ""
    assert vars_a["SKILL_INSTALLED_ANTISLOP"] == ""

    # 2. Agent B has no skills installed
    captured_renders.clear()
    monkeypatch.setattr("ralph.pipeline.prompt_prep.agents_for_phase", _agent_b_list)
    materialize_prepared_prompt(
        PreparePromptEffect(phase="planning", drain="planning"),
        pipeline_policy=bundle.pipeline,
        artifacts_policy=bundle.artifacts,
        workspace_scope=WorkspaceScope(workspace_dir),
        agents_policy=bundle.agents,
        state=state,
        registry=registry,
        config=test_config,
    )
    assert len(captured_renders) >= 1
    vars_b = captured_renders[-1]
    assert vars_b["SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"] == ""
    assert vars_b["SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"] == ""
    assert vars_b["SKILL_INSTALLED_SYSTEMATIC_DEBUGGING"] == ""
    assert vars_b["SKILL_INSTALLED_ANTISLOP"] == ""

    # 3. No leakage back to Agent A
    captured_renders.clear()
    monkeypatch.setattr("ralph.pipeline.prompt_prep.agents_for_phase", _agent_a_list)
    materialize_prepared_prompt(
        PreparePromptEffect(phase="planning", drain="planning"),
        pipeline_policy=bundle.pipeline,
        artifacts_policy=bundle.artifacts,
        workspace_scope=WorkspaceScope(workspace_dir),
        agents_policy=bundle.agents,
        state=state,
        registry=registry,
        config=test_config,
    )
    assert len(captured_renders) >= 1
    vars_a_second = captured_renders[-1]
    assert vars_a_second["SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"] == "true"
    assert vars_a_second["SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"] == "true"


def test_integrated_materialize_agent_prompt_if_needed_two_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "PROMPT.md").write_text("Build the user request", encoding="utf-8")
    (workspace_dir / ".agent").mkdir(parents=True)
    (workspace_dir / ".agent" / "PRODUCT_CRITERIA.md").write_text("Requirements", encoding="utf-8")
    (workspace_dir / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (workspace_dir / ".agent" / "artifacts" / "plan.md").write_text(
        "Plan content", encoding="utf-8"
    )

    _setup_two_agent_roots(monkeypatch, tmp_path)
    bundle = _load_test_policy_bundle()
    registry = _TestAgentRegistry()

    captured_renders: list[Mapping[str, str]] = []
    original_render_template = template_engine.render_template

    def _render_spy(
        template: str,
        variables: Mapping[str, str],
        partials: Mapping[str, str],
    ) -> str:
        captured_renders.append(dict(variables))
        return original_render_template(template, variables, partials)

    monkeypatch.setattr(template_engine, "render_template", _render_spy)
    monkeypatch.setattr("ralph.prompts.developer.render_template", _render_spy)
    monkeypatch.setattr("ralph.prompts.materialize.render_template", _render_spy)

    dev_state = PipelineState(phase="development", previous_phase="planning")

    captured_renders.clear()
    materialize_agent_prompt_if_needed(
        effect=InvokeAgentEffect(
            agent_name="agent_a",
            phase="development",
            prompt_file=str(workspace_dir / ".agent" / "tmp" / "development_prompt.md"),
        ),
        state=dev_state,
        workspace=FsWorkspace(workspace_dir),
        policy_bundle=bundle,
        registry=registry,
    )
    assert len(captured_renders) >= 1
    vars_a_dev = captured_renders[-1]
    assert vars_a_dev["SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"] == "true"
    assert vars_a_dev["SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"] == "true"
    assert vars_a_dev["SKILL_INSTALLED_SYSTEMATIC_DEBUGGING"] == ""

    captured_renders.clear()
    materialize_agent_prompt_if_needed(
        effect=InvokeAgentEffect(
            agent_name="agent_b",
            phase="development",
            prompt_file=str(workspace_dir / ".agent" / "tmp" / "development_prompt.md"),
        ),
        state=dev_state,
        workspace=FsWorkspace(workspace_dir),
        policy_bundle=bundle,
        registry=registry,
    )
    assert len(captured_renders) >= 1
    vars_b_dev = captured_renders[-1]
    assert vars_b_dev["SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"] == ""
    assert vars_b_dev["SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"] == ""


def test_integrated_fallback_renders_carry_skill_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True)
    captured_fallbacks: list[Mapping[str, str]] = []

    def _fallback_spy(
        context: TemplateContext,
        template_name: str,
        variables: Mapping[str, str],
    ) -> str:
        captured_fallbacks.append(dict(variables))
        return "fallback rendered"

    def _failing_render(*args: object, **kwargs: object) -> str:
        msg = "Forced template rendering failure for fallback test"
        raise TemplateRenderingError(msg)

    monkeypatch.setattr(
        "ralph.prompts.developer._render_static_fallback",
        _fallback_spy,
    )
    monkeypatch.setattr(template_engine, "render_template", _failing_render)
    monkeypatch.setattr("ralph.prompts.developer.render_template", _failing_render)

    tmpl_ctx = TemplateContext.default(workspace_dir)
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    prompt_planning_xml_with_context(
        tmpl_ctx,
        PlanningPromptInputs(
            prompt_content="Plan requirements",
            installed_skills=("test-driven-development", "antislop"),
        ),
        FsWorkspace(workspace_dir),
        session_caps,
    )
    assert len(captured_fallbacks) == 1
    planning_fb = captured_fallbacks[-1]
    assert planning_fb["SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"] == "true"
    assert planning_fb["SKILL_INSTALLED_ANTISLOP"] == "true"
    assert planning_fb["SKILL_INSTALLED_SYSTEMATIC_DEBUGGING"] == ""
    assert planning_fb["SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"] == ""

    captured_fallbacks.clear()
    prompt_developer_iteration_xml_with_context(
        tmpl_ctx,
        DeveloperPromptInputs(
            prompt_content="Dev requirements",
            plan_content="Dev plan",
            installed_skills=("verification-before-completion",),
        ),
        FsWorkspace(workspace_dir),
        session_caps,
    )
    assert len(captured_fallbacks) == 1
    dev_fb = captured_fallbacks[-1]
    assert dev_fb["SKILL_INSTALLED_VERIFICATION_BEFORE_COMPLETION"] == "true"
    assert dev_fb["SKILL_INSTALLED_TEST_DRIVEN_DEVELOPMENT"] == ""
    assert dev_fb["SKILL_INSTALLED_SYSTEMATIC_DEBUGGING"] == ""
    assert dev_fb["SKILL_INSTALLED_ANTISLOP"] == ""
