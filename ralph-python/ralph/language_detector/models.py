"""Data models for detected project language stacks."""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_SECONDARY_LANGUAGES = 6


@dataclass
class ProjectStack:
    """Detected project stack summary."""

    primary_language: str = "Unknown"
    secondary_languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    has_tests: bool = False
    test_framework: str | None = None
    package_manager: str | None = None

    def is_rust(self) -> bool:
        return self.primary_language == "Rust" or "Rust" in self.secondary_languages

    def is_python(self) -> bool:
        return self.primary_language == "Python" or "Python" in self.secondary_languages

    def is_javascript_or_typescript(self) -> bool:
        targets = {"JavaScript", "TypeScript"}
        return self.primary_language in targets or any(
            language in targets for language in self.secondary_languages
        )

    def is_go(self) -> bool:
        return self.primary_language == "Go" or "Go" in self.secondary_languages

    def summary(self) -> str:
        parts = [self.primary_language]
        if self.secondary_languages:
            parts.append(f"(+{', '.join(self.secondary_languages)})")
        if self.frameworks:
            parts.append(f"[{', '.join(self.frameworks)}]")
        if self.has_tests:
            parts.append(
                f"tests:{self.test_framework}" if self.test_framework else "tests:yes"
            )
        return " ".join(parts)
