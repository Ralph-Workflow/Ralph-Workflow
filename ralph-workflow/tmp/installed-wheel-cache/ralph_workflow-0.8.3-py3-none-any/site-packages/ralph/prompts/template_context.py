"""Template registry/ context for prompt generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.prompts.template_registry import (
    TemplateRegistry,
    default_template_dirs,
    load_partial_templates,
    packaged_template_root,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass(frozen=True)
class TemplateContext:
    """Bundled registry and partials for prompt template rendering."""

    registry: TemplateRegistry
    partials: Mapping[str, str]

    @classmethod
    def default(cls, workspace_root: Path | None = None) -> TemplateContext:
        template_dirs = (
            default_template_dirs(workspace_root)
            if workspace_root
            else (packaged_template_root(), packaged_template_root() / "shared")
        )
        registry = TemplateRegistry(template_dirs=template_dirs)
        partials = load_partial_templates(template_dirs)
        return cls(registry=registry, partials=partials)
