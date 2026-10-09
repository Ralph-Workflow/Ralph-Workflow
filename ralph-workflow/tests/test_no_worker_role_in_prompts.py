"""Guard: no developer-prompt template may reference the removed IS_WORKER flag.

developer prompts have one audience and must not branch on a worker or coordinator role — see docs/agents/prompt-surface-changes.md
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.protocol.capability_mapping import Capability
from ralph.prompts._capability_set import CapabilitySet
from ralph.prompts._policy_flag import PolicyFlag
from ralph.prompts._policy_flag_set import PolicyFlagSet
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.testing.audit_template_render_integrity import (
    _render_targets,
    check_template_source,
)
from ralph.workspace.memory import MemoryWorkspace


def test_no_template_references_is_worker() -> None:
    """Ralph-orchestrated workers are removed; IS_WORKER must not appear in any
    template source (top-level or shared partials).

    developer prompts have one audience and must not branch on a worker or coordinator role — see docs/agents/prompt-surface-changes.md
    """
    context = TemplateContext.default()
    targets = _render_targets(context)
    all_sources = dict(context.partials)
    all_sources.update({target.name: target.source for target in targets})

    for name, source in all_sources.items():
        violations = check_template_source(name, source)
        assert not violations, f"{name}: {'; '.join(violations)}"
        assert "is_worker" not in source.lower(), f"{name} contains 'is_worker'"


def test_developer_render_context_has_no_is_worker() -> None:
    """Developer render context must never contain an IS_WORKER key, and rendered
    prompts must contain ## PARALLEL EXECUTION, sub-agent guidance, and pre-submit review."""
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
        assert "IS_WORKER" not in rendered
        assert "## PARALLEL EXECUTION" in rendered
        assert "sub-agent" in rendered or "subagent" in rendered
        assert "pre-submit" in rendered or "fresh-context review" in rendered


def test_developer_render_context_source_has_no_is_worker_or_work_unit() -> None:
    """The developer module's source must construct the render context without
    any IS_WORKER, work_unit_*, or worker_namespace keys. The single-audience
    contract is enforced at the construction site, not just at the rendered
    surface, so a future regression that re-introduces the role gate fails
    here before the user ever sees the prompt."""
    package_dir = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "developer"
    for source_path in (package_dir / "__init__.py", package_dir / "developer_prompt_inputs.py"):
        source = source_path.read_text(encoding="utf-8")
        lowered = source.lower()
        assert "is_worker" not in lowered, (
            f"{source_path.relative_to(package_dir.parent.parent)} reintroduces IS_WORKER"
        )
        assert "worker_namespace" not in lowered, (
            f"{source_path.relative_to(package_dir.parent.parent)} reintroduces worker_namespace"
        )
        assert "work_unit_id" not in lowered, (
            f"{source_path.relative_to(package_dir.parent.parent)} reintroduces work_unit_id"
        )
