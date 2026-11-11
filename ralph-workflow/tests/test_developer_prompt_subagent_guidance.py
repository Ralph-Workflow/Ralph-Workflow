from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.protocol.capability_mapping import Capability
from ralph.prompts._capability_set import CapabilitySet
from ralph.prompts._policy_flag import PolicyFlag
from ralph.prompts._policy_flag_set import PolicyFlagSet
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

_COORDINATOR_INDEPENDENT_READY = "independent ready"
_COORDINATOR_QUEUE_OR_LOCAL = "queue"
_COORDINATOR_EXPOSED_TOOLS = "native orchestration facility"
_COORDINATOR_STOPPED_WRITER = "stopped"

_COORDINATOR_PINS_REQUIRED: tuple[str, ...] = (
    "Parallel execution of independent ready units is required by default",
    "dispatch every ready unit concurrently",
    "Sequential execution requires an explicit plan reason",
    "not a reason to stop, hand back, split the task, or return `partial`",
    "which plan text forced it",
    "## PARALLEL EXECUTION (required by default)",
)

_COORDINATOR_RENDERED_NEGATIVES: tuple[str, ...] = (
    "missing sub-agent tool",
    "bounded sequential",
    "bounded-sequential",
    "fall back to bounded sequential",
    "no native sub-agent",
    "runtime limit",
)

_PARTIAL_SOURCE_NEGATIVES: tuple[str, ...] = (
    "or a missing sub-agent tool",
    "bounded-sequential",
    "no native sub-agent / task tool is exposed",
    "runtime limit",
)

_PROTECTED_ROOTS = (".agent", ".git", ".worktrees")
_OWNERSHIP_FIELDS = ("Paths:", "Directories:", "Files:")


def _render(template_name: str, tmp_path: Path) -> str:
    return " ".join(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement the requested change.",
                plan_content="### [S-1] Implement the assigned change",
            ),
            workspace=MemoryWorkspace(root=str(tmp_path)),
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template_name,
        ).split()
    )


def _render_raw_with_caps(
    template_name: str,
    tmp_path: Path,
    *,
    session_caps: SessionCapabilities,
) -> str:
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement the requested change.",
            plan_content="### [S-1] Implement the assigned change",
        ),
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=session_caps,
        template_name=template_name,
    )


def _render_with_feedback(
    template_name: str,
    tmp_path: Path,
    *,
    analysis_feedback_status: str,
    analysis_feedback_content: str,
) -> str:
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement the requested change.",
            plan_content="### [S-1] Implement the assigned change",
            analysis_feedback_status=analysis_feedback_status,
            analysis_feedback_content=analysis_feedback_content,
        ),
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name=template_name,
    )


def _extract_parallel_section(rendered: str) -> str:
    start = rendered.find("## PARALLEL EXECUTION")
    if start < 0:
        return ""
    tool_guidance = "Use runtime-native orchestration"
    tool_boundary = rendered.find(tool_guidance, start)
    next_heading = rendered.find("\n## ", start + 1)

    candidates = [pos for pos in (next_heading, tool_boundary) if pos > 0]
    if not candidates:
        return rendered[start:]
    return rendered[start : min(candidates)]


@pytest.mark.parametrize(
    "template_name",
    (
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "developer_iteration_fallback.jinja",
    ),
)
def test_rendered_parallel_execution_contracts_match_role(
    tmp_path: Path, template_name: str
) -> None:
    rendered = _render(template_name, tmp_path)
    rendered_lower = rendered.lower()
    assert _COORDINATOR_INDEPENDENT_READY in rendered_lower
    assert _COORDINATOR_QUEUE_OR_LOCAL in rendered_lower
    assert _COORDINATOR_EXPOSED_TOOLS in rendered_lower
    assert _COORDINATOR_STOPPED_WRITER in rendered_lower
    for token in _PROTECTED_ROOTS:
        assert token in rendered, f"protected root {token!r} missing in {template_name}"
    for token in _OWNERSHIP_FIELDS:
        assert token in rendered, f"ownership token {token!r} missing in {template_name}"
    missing = [pin for pin in _COORDINATOR_PINS_REQUIRED if pin not in rendered]
    assert not missing, (
        f"coordinator rendering {template_name!r} missing pinned "
        f"parallel-by-default anchors: {missing!r}"
    )


def test_shared_parallel_partial_keeps_sanitization_and_waves() -> None:
    partial = (
        Path(__file__).resolve().parents[1]
        / "ralph"
        / "prompts"
        / "templates"
        / "shared"
        / "_parallel_execution.jinja"
    )
    source = partial.read_text(encoding="utf-8")

    for token in _PROTECTED_ROOTS:
        assert token in source, f"protected root {token!r} missing in parallel partial"
    for token in _OWNERSHIP_FIELDS:
        assert token in source, f"ownership token {token!r} missing in parallel partial"
    assert "waves" in source
    assert "Serialize conflicting ownership" in source
    assert "Do not broaden file ownership" in source
    assert "Parallel execution of independent ready units is required by default" in source
    assert "Sequential execution requires an explicit plan reason" in source
    assert "not a reason to stop, hand back, split the task, or return `partial`" in source
    assert "queue the remaining ready units" in source
    assert "ownership unresolvable" in source
    assert "malformed" in source
    for forbidden in _PARTIAL_SOURCE_NEGATIVES:
        assert forbidden not in source, (
            f"parallel partial re-introduced removed fallback phrase {forbidden!r}"
        )


def test_native_orchestration_is_authorized_and_missing_support_is_installed() -> None:
    templates = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
    subagents = (templates / "shared" / "_subagents.j2").read_text(encoding="utf-8")
    capabilities = (templates / "shared" / "_session_capabilities.jinja").read_text(
        encoding="utf-8"
    )
    parallel = (templates / "shared" / "_parallel_execution.jinja").read_text(encoding="utf-8")
    combined = "\n".join((subagents, capabilities, parallel))

    assert "native orchestration tools are explicitly authorized" in combined
    assert "install or enable the facility" in combined
    for native_term in (
        "agent",
        "sub-agent",
        "task",
        "delegate",
        "fork",
        "team",
        "spawn",
        "child session",
    ):
        assert native_term in combined
    assert (
        "research its supported package, plugin, extension, configuration, or feature mechanism"
        in parallel
    )
    assert "examples/extensions/subagent/" in parallel
    assert "index.ts" in parallel and "agents.ts" in parallel
    for repair_anchor in (
        "features.multi_agent",
        "agents.enabled",
        "tools.task",
        "permission.task",
        "disabledTools",
        "TaskWait",
        "invoke_subagent",
    ):
        assert repair_anchor in parallel
    assert "must not become main-session implementation" in combined
    assert "Sub-agents use the runtime's native tools directly" in subagents
    assert "not an exclusive or brokered path for sub-agents" in subagents
    assert "never impose a brokered-tools-only rule" in parallel


def test_developer_prompt_preserves_recursive_native_delegation() -> None:
    context = TemplateContext.default()
    workspace = MemoryWorkspace()
    session_caps = SessionCapabilities(
        capabilities=CapabilitySet({Capability.PROCESS_EXEC_BOUNDED}),
        policy_flags=PolicyFlagSet({PolicyFlag.ALLOW_SHELL}),
    )
    inputs = DeveloperPromptInputs(
        prompt_content="test prompt",
        plan_content="test plan",
    )
    for tmpl in ("developer_iteration_continuation.jinja", "developer_iteration_fallback.jinja"):
        rendered = prompt_developer_iteration_xml_with_context(
            context,
            inputs,
            workspace,
            session_caps,
            template_name=tmpl,
        )
        assert "WORKER DO-NOT-DISPATCH" not in rendered
        assert "Workers never dispatch sub-agents" not in rendered
        assert "Native sub-agent" in rendered or "native sub-agent" in rendered


def test_feedback_role_guidance_uses_worker_identity_not_verdict() -> None:
    templates = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
    for filename in (
        "developer_iteration_continuation.jinja",
        "developer_iteration_fallback.jinja",
    ):
        source = (templates / filename).read_text(encoding="utf-8")
        assert "IS_WORKER" not in source
        assert "{% if ANALYSIS_FEEDBACK_STATUS|default('') == 'request_changes' %}" not in source


def test_coverage_check_does_not_authorize_local_unit_implementation() -> None:
    templates = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
    for filename in ("developer_iteration.jinja", "developer_iteration_fallback.jinja"):
        source = (templates / filename).read_text(encoding="utf-8")
        assert "while the main session starts the first ready step" not in source


_COORDINATOR_TEMPLATES: tuple[str, ...] = (
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "developer_iteration_fallback.jinja",
)


def _maximally_different_caps() -> SessionCapabilities:
    return SessionCapabilities(
        capabilities=CapabilitySet(
            (
                Capability.WORKSPACE_READ,
                Capability.ARTIFACT_SUBMIT,
                Capability.ARTIFACT_PLAN_READ,
                Capability.RUN_REPORT_PROGRESS,
                Capability.GIT_STATUS_READ,
            )
        ),
        policy_flags=PolicyFlagSet((PolicyFlag.ALLOW_NETWORK, PolicyFlag.ALLOW_ENV_READ)),
        tool_name_prefix="alternate_runtime_",
    )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_coordinator_parallel_section_is_capability_invariant(
    tmp_path: Path, template_name: str
) -> None:
    default_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    alternate_caps = _maximally_different_caps()

    default_rendered = _render_raw_with_caps(template_name, tmp_path, session_caps=default_caps)
    alternate_rendered = _render_raw_with_caps(template_name, tmp_path, session_caps=alternate_caps)

    default_section = _extract_parallel_section(default_rendered)
    alternate_section = _extract_parallel_section(alternate_rendered)

    assert default_section, f"parallel section missing from {template_name!r} default render"
    assert alternate_section, f"parallel section missing from {template_name!r} alternate render"
    assert default_section == alternate_section, (
        f"parallel section in {template_name!r} changed under alternate "
        f"capability inputs; capability gating is forbidden"
    )

    if template_name == "developer_iteration_continuation.jinja":
        continuation_preamble = (
            "A continuation must keep parallelizing remaining ready work; being a continuation\n"
            "is not a reason to fall back to sequential execution."
        )
        assert continuation_preamble in default_section, (
            "continuation parallel framing missing from extracted section under default caps"
        )
        assert continuation_preamble in alternate_section, (
            "continuation parallel framing missing from extracted section under alternate caps"
        )

    for pin in _COORDINATOR_PINS_REQUIRED:
        assert pin in default_section, (
            f"extracted section missing anchor {pin!r} under default caps in {template_name!r}"
        )
        assert pin in alternate_section, (
            f"extracted section missing anchor {pin!r} under alternate caps in {template_name!r}"
        )
        assert pin in default_rendered, (
            f"full render missing anchor {pin!r} under default caps in {template_name!r}"
        )
        assert pin in alternate_rendered, (
            f"full render missing anchor {pin!r} under alternate caps in {template_name!r}"
        )
    for forbidden in _COORDINATOR_RENDERED_NEGATIVES:
        assert forbidden not in default_section, (
            f"parallel section re-introduced {forbidden!r} under default caps in {template_name!r}"
        )
        assert forbidden not in alternate_section, (
            f"parallel section re-introduced {forbidden!r} under alternate caps in {template_name!r}"
        )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_coordinator_full_render_excludes_removed_fallback_phrases(
    tmp_path: Path, template_name: str
) -> None:
    for caps in (
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        _maximally_different_caps(),
    ):
        rendered = _render_raw_with_caps(template_name, tmp_path, session_caps=caps)
        for forbidden in _COORDINATOR_RENDERED_NEGATIVES:
            assert forbidden not in rendered, (
                f"full {template_name!r} render contains forbidden phrase "
                f"{forbidden!r} under caps {caps!r}"
            )


_FEEDBACK_PATH_FOLLOW_PLAN_SENTENCE = (
    "Developer follow-plan: when the analysis above or the authoritative plan "
    "identifies independent units, you MUST follow that division and dispatch "
    "every ready unit in parallel \u2014 never handle them sequentially in this "
    "session or hand them back one at a time as a separate `partial` result. "
    "Sequential execution requires an explicit plan reason (declared coupling or "
    "`Depends on:` chains)."
)
_FEEDBACK_PATH_MUST_ANCHOR = (
    "you MUST follow that division and dispatch every ready unit in parallel"
)
_FEEDBACK_PATH_EXPLICIT_REASON_ANCHOR = (
    "Sequential execution requires an explicit plan reason (declared coupling or "
    "`Depends on:` chains)"
)
_FEEDBACK_PATH_FALSE_CLAIM = "the analysis above splits"
_FEEDBACK_PATH_OPTIONAL_MAY_SPLIT = "may split the remaining work"
_FEEDBACK_PATH_WHEN_IT_DOES = "When it does, dispatch each unit in parallel"
_FEEDBACK_PATH_BROKEN_BACKTICK = "`partial``"


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_feedback_path_mandatory_wording_in_coordinator_renders(
    tmp_path: Path, template_name: str
) -> None:
    rendered = _render_with_feedback(
        template_name,
        tmp_path,
        analysis_feedback_status="request_changes",
        analysis_feedback_content=(
            "Add a unit-aware guard to the S-2 and S-3 follow-plan branch "
            "so workers do not receive the orchestration sentence."
        ),
    )

    assert _FEEDBACK_PATH_FOLLOW_PLAN_SENTENCE in rendered, (
        f"coordinator {template_name!r} missing the mandatory follow-plan "
        f"sentence under request_changes feedback"
    )
    assert _FEEDBACK_PATH_MUST_ANCHOR in rendered, (
        f"coordinator {template_name!r} missing the MUST follow-plan anchor"
    )
    assert _FEEDBACK_PATH_EXPLICIT_REASON_ANCHOR in rendered, (
        f"coordinator {template_name!r} missing the explicit-plan-reason anchor"
    )
    assert _FEEDBACK_PATH_FALSE_CLAIM not in rendered, (
        f"coordinator {template_name!r} still asserts an unconditional "
        f"'the analysis above splits' claim that is not true for arbitrary "
        f"request_changes feedback"
    )
    assert _FEEDBACK_PATH_OPTIONAL_MAY_SPLIT not in rendered, (
        f"coordinator {template_name!r} kept the optional 'may split' framing"
    )
    assert _FEEDBACK_PATH_WHEN_IT_DOES not in rendered, (
        f"coordinator {template_name!r} kept the optional 'When it does' framing"
    )
    assert _FEEDBACK_PATH_BROKEN_BACKTICK not in rendered, (
        f"coordinator {template_name!r} render still has the broken nested "
        f"backtick sequence from the S-1 fix"
    )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_feedback_path_renders_accurately_when_feedback_omits_units(
    tmp_path: Path, template_name: str
) -> None:
    rendered = _render_with_feedback(
        template_name,
        tmp_path,
        analysis_feedback_status="request_changes",
        analysis_feedback_content=(
            "Tighten the typed contract on the render helper. The current "
            "shape leaks Optional[Workspace] in two places; switch to a "
            "narrower protocol and add a regression test."
        ),
    )

    assert "or the authoritative plan" in rendered, (
        f"coordinator {template_name!r} dropped the conditional "
        f"'or the authoritative plan' accuracy anchor"
    )
    assert _FEEDBACK_PATH_MUST_ANCHOR in rendered
    assert _FEEDBACK_PATH_EXPLICIT_REASON_ANCHOR in rendered
    assert _FEEDBACK_PATH_FALSE_CLAIM not in rendered
    assert _FEEDBACK_PATH_OPTIONAL_MAY_SPLIT not in rendered
    assert _FEEDBACK_PATH_WHEN_IT_DOES not in rendered
