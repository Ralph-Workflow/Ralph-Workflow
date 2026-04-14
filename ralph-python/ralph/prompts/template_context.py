"""Template registry/ context for prompt generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ralph.prompts.policy_templates import (
    DEVELOPER_ITERATION_TEMPLATE,
    PLANNING_TEMPLATE,
)


class TemplateRegistry:
    """Simple registry of canonical templates."""

    def __init__(self) -> None:
        self._templates: Dict[str, str] = {}

    def register_template(self, name: str, content: str) -> None:
        self._templates[name] = content

    def get_template(self, name: str) -> str:
        return self._templates[name]


@dataclass(frozen=True)
class TemplateContext:
    registry: TemplateRegistry

    @classmethod
    def default(cls) -> "TemplateContext":
        registry = TemplateRegistry()
        registry.register_template("planning", PLANNING_TEMPLATE)
        registry.register_template("developer_iteration", DEVELOPER_ITERATION_TEMPLATE)
        return cls(registry=registry)
