"""Template registry/ context for prompt generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.prompts.policy_templates import (
    DEVELOPER_ITERATION_TEMPLATE,
    PLANNING_TEMPLATE,
)
from ralph.prompts.template_registry import TemplateRegistry, default_template_dirs

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class TemplateContext:
    registry: TemplateRegistry

    @classmethod
    def default(cls, workspace_root: Path | None = None) -> TemplateContext:
        template_dirs = default_template_dirs(workspace_root) if workspace_root else ()
        registry = TemplateRegistry(template_dirs=template_dirs)
        registry.register_template("planning", PLANNING_TEMPLATE)
        registry.register_template("developer_iteration", DEVELOPER_ITERATION_TEMPLATE)
        return cls(registry=registry)
