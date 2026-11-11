
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.builtin_spec import vision_verdict_agent_spec
from ralph.project_policy.evidence import design_system_required

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.catalog import AgentCatalog
    from ralph.agents.support import AgentSupport
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

VISION_VERDICT_AGENT_NAME: str = "vision-verdict"


@dataclass(frozen=True)
class VisionVerdictDispatch:

    target: str
    intent: str
    before_handles: tuple[str, ...]
    after_handles: tuple[str, ...]
    delegated_agent_id: str
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not all((self.target.strip(), self.intent.strip(), self.delegated_agent_id.strip())):
            raise ValueError(
                "vision dispatch requires target, intent, and delegated agent identity"
            )
        if not self.before_handles or not self.after_handles:
            raise ValueError("vision dispatch requires before and after capture evidence")
        if self.timeout_seconds <= 0:
            raise ValueError("vision dispatch timeout must be positive")


def dispatch_vision_verdict(
    *,
    target: str,
    intent: str,
    before_handles: tuple[str, ...],
    after_handles: tuple[str, ...],
    delegated_agent_id: str,
    invoke: Callable[[VisionVerdictDispatch], str],
    timeout_seconds: float = 5.0,
) -> str:
    request = VisionVerdictDispatch(
        target=target,
        intent=intent,
        before_handles=before_handles,
        after_handles=after_handles,
        delegated_agent_id=delegated_agent_id,
        timeout_seconds=timeout_seconds,
    )
    verdict_id = invoke(request)
    if not verdict_id.strip():
        raise ValueError("vision verdict dispatch returned no submitted verdict identifier")
    return verdict_id


_DESIGN_SYSTEM_POLICY_CITATION: str = (
    "ralph-workflow/ralph/project_policy/evidence.py:132 (design_system_required)"
)


def is_design_system_policy_in_scope(
    workspace: Workspace | None = None,
    stack: ProjectStack | None = None,
) -> bool:
    if workspace is None or stack is None:
        return False
    required, _consulted = design_system_required(workspace, stack)
    return bool(required)


def vision_verdict_agent_support() -> AgentSupport:
    return vision_verdict_agent_spec().to_support(VISION_VERDICT_AGENT_NAME)


def provision_vision_verdict_agent(
    catalog: AgentCatalog,
    *,
    workspace: Workspace | None = None,
    stack: ProjectStack | None = None,
) -> bool:
    if not is_design_system_policy_in_scope(workspace=workspace, stack=stack):
        return False
    if catalog.get(VISION_VERDICT_AGENT_NAME) is not None:
        return True
    support = vision_verdict_agent_support()
    catalog.add(support)
    return True


def is_vision_verdict_agent_registered(catalog: AgentCatalog) -> bool:
    return catalog.get(VISION_VERDICT_AGENT_NAME) is not None


__all__ = [
    "VISION_VERDICT_AGENT_NAME",
    "VisionVerdictDispatch",
    "dispatch_vision_verdict",
    "is_design_system_policy_in_scope",
    "is_vision_verdict_agent_registered",
    "provision_vision_verdict_agent",
    "vision_verdict_agent_support",
]
