"""TemplateNotFoundError raised when a requested template is missing."""

from __future__ import annotations


class TemplateNotFoundError(Exception):
    """Raised when a requested template is missing."""

    def __init__(self, template_name: str) -> None:
        super().__init__(f"template '{template_name}' not found")
        self.template_name = template_name
