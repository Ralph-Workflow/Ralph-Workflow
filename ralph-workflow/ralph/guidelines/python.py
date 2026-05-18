"""Python-specific review guidelines.

Port of the Rust review guidance for Python projects, with Python-native structure.
Includes core Python checks plus framework-specific additions for Django, FastAPI,
and Flask projects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    class ReviewGuidelines(Protocol):
        """Protocol for language-specific review guideline collections."""

        quality_checks: list[str]
        security_checks: list[str]
        performance_checks: list[str]
        testing_checks: list[str]
        documentation_checks: list[str]
        idioms: list[str]
        anti_patterns: list[str]

        def summary(self) -> str: ...
        def total_checks(self) -> int: ...


@dataclass
class PythonGuidelines:
    """Review guidelines for Python codebases.

    Args:
        frameworks: Optional framework names used to add stack-specific guidance.
    """

    frameworks: tuple[str, ...] = ()
    quality_checks: list[str] = field(init=False)
    security_checks: list[str] = field(init=False)
    performance_checks: list[str] = field(init=False)
    testing_checks: list[str] = field(init=False)
    documentation_checks: list[str] = field(init=False)
    idioms: list[str] = field(init=False)
    anti_patterns: list[str] = field(init=False)

    def __init__(self, frameworks: Iterable[str] = ()) -> None:
        normalized_frameworks = tuple(frameworks)
        self.frameworks = normalized_frameworks
        self.quality_checks = [
            "Follow PEP 8 style guidance and keep imports organized.",
            "Use type hints for public function signatures and important internal boundaries.",
            "Prefer f-strings over str.format() for readable string interpolation.",
            "Use context managers for files, locks, and other managed resources.",
        ]
        self.security_checks = [
            "Do not use eval() or exec() with untrusted input.",
            "Use parameterized queries for database access.",
            "Validate and normalize file paths to prevent path traversal.",
            "Avoid unsafe deserialization such as pickle or yaml.load on untrusted data.",
        ]
        self.performance_checks = [
            "Use generators and iterators for large data processing paths.",
            "Prefer comprehensions when they improve clarity and avoid unnecessary temporary"
            " loops.",
            "Profile before optimizing and justify non-obvious performance trade-offs.",
        ]
        self.testing_checks = [
            "Use pytest fixtures to keep test setup explicit and reusable.",
            "Mock external dependencies at process and network boundaries.",
            "Test exception handling and failure paths, not only success cases.",
        ]
        self.documentation_checks = [
            "Document public modules, classes, and functions with clear docstrings.",
            "Keep docstrings aligned with actual behavior, parameters, and return values.",
            "Explain non-obvious business rules, side effects, and framework conventions.",
        ]
        self.idioms = [
            "Prefer Pythonic idioms such as EAFP where they improve clarity.",
            "Leverage the standard library before adding custom utility code.",
            "Use pathlib, collections, itertools, and typing features where they simplify intent.",
        ]
        self.anti_patterns = [
            "Avoid mutable default arguments.",
            "Do not use bare except clauses.",
            "Avoid global mutable state and hidden side effects.",
        ]

        for framework in normalized_frameworks:
            self._apply_framework(framework)

    def _apply_framework(self, framework: str) -> None:
        """Add framework-specific guideline extensions."""
        framework_name = framework.casefold()

        if framework_name == "django":
            self.quality_checks.extend(
                [
                    "Use Django ORM features intentionally and avoid ad hoc SQL where ORM fits.",
                    "Follow Django project conventions for apps, settings, and model organization.",
                    "Use class-based views only when they simplify reuse and composition.",
                ]
            )
            self.security_checks.extend(
                [
                    "Keep Django CSRF protection enabled for state-changing requests.",
                    "Validate forms and serializers instead of trusting request payloads.",
                    "Use Django authentication and permission mechanisms consistently.",
                ]
            )
            return

        if framework_name == "fastapi":
            self.quality_checks.extend(
                [
                    "Use Pydantic models for request and response validation.",
                    "Define response models explicitly to keep API contracts stable.",
                    "Use dependency injection for shared concerns such as auth, config, and"
                    " clients.",
                ]
            )
            self.security_checks.extend(
                [
                    "Implement OAuth2 or JWT handling with explicit validation and expiry checks.",
                    "Use HTTPS redirect and trusted proxy configuration where deployment"
                    " requires it.",
                ]
            )
            return

        if framework_name == "flask":
            self.quality_checks.extend(
                [
                    "Use Blueprints to keep route registration and application structure modular.",
                    "Use Flask-SQLAlchemy and application factories consistently when the"
                    " project adopts them.",
                ]
            )
            self.security_checks.extend(
                [
                    "Store Flask SECRET_KEY securely and never commit it.",
                    "Use security headers and production-safe session configuration.",
                ]
            )

    def summary(self) -> str:
        """Return a short human-readable summary."""
        return (
            f"{len(self.quality_checks)} quality checks, "
            f"{len(self.security_checks)} security checks, "
            f"{len(self.anti_patterns)} anti-patterns"
        )

    def total_checks(self) -> int:
        """Return the total number of configured checks."""
        return sum(
            [
                len(self.quality_checks),
                len(self.security_checks),
                len(self.performance_checks),
                len(self.testing_checks),
                len(self.documentation_checks),
                len(self.idioms),
                len(self.anti_patterns),
            ]
        )
