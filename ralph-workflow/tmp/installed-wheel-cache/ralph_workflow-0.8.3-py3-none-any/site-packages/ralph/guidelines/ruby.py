"""Ruby-specific review guideline categories.

Ported from the canonical Rust implementation in
``ralph-workflow/src/guidelines/ruby.rs`` and adapted for the Python port.
The module models core Ruby guidance plus optional Rails and Sinatra
extensions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    class ReviewGuidelines(Protocol):
        """Protocol for guideline-like bundles used by the review prompts."""

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
class RubyGuidelines:
    """Ruby review checks.

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
            "Follow the Ruby style guide and keep RuboCop issues addressed.",
            "Use meaningful variable and method names.",
            "Keep methods short and focused when possible.",
            "Prefer symbols over strings for stable hash keys.",
        ]
        self.security_checks = [
            "Use parameterized queries and avoid SQL string interpolation.",
            "Escape output rendered into templates and views.",
            "Validate and sanitize user-controlled input before use.",
        ]
        self.performance_checks = [
            "Avoid N+1 query patterns and eager load associations when appropriate.",
            "Use enumerators and collection helpers intentionally to avoid needless"
            " intermediate work.",
            "Profile hot paths before introducing non-obvious optimizations.",
        ]
        self.testing_checks = [
            "Cover service objects, business rules, and error handling with focused specs.",
            "Exercise framework request, controller, or route behavior with"
            " integration-style tests.",
            "Test authorization, validation, and persistence edge cases explicitly.",
        ]
        self.documentation_checks = [
            "Document public APIs, rake tasks, and non-obvious callbacks or metaprogramming.",
            "Keep README, setup steps, and framework conventions aligned with"
            " the actual app structure.",
            "Explain cross-model side effects, background jobs, and lifecycle"
            " hooks that are not obvious from names alone.",
        ]
        self.idioms = [
            "Prefer expressive Ruby collection helpers and blocks when they improve clarity.",
            "Use small POROs, modules, and concerns only when responsibilities stay clear.",
            "Lean on framework conventions before introducing custom abstractions.",
        ]
        self.anti_patterns = [
            "Avoid monkey patching core classes.",
            "Do not use eval with user-controlled input.",
            "Avoid deeply nested conditionals when guard clauses or object"
            " extraction would simplify flow.",
        ]

        for framework in normalized_frameworks:
            self._apply_framework(framework)

    def _apply_framework(self, framework: str) -> None:
        """Add framework-specific guideline extensions."""
        framework_name = framework.casefold()

        if framework_name == "rails":
            self.quality_checks.extend(
                [
                    "Follow Rails conventions and favor the conventional project structure.",
                    "Use Active Record validations for domain constraints that"
                    " belong at the model layer.",
                    "Keep controllers thin by pushing business logic into models,"
                    " services, or query objects.",
                ]
            )
            self.security_checks.extend(
                [
                    "Use strong parameters consistently.",
                    "Protect against mass assignment and avoid permissive parameter whitelists.",
                    "Keep Rails CSRF protection enabled for state-changing requests.",
                ]
            )
            self.performance_checks.extend(
                [
                    "Review Active Record queries for unnecessary eager"
                    " loading, callbacks, and repeated database access.",
                    "Use background jobs for slow external I/O and long-running"
                    " work outside the request cycle.",
                ]
            )
            return

        if framework_name == "sinatra":
            self.quality_checks.extend(
                [
                    "Use modular Sinatra style for larger applications.",
                    "Organize routes logically and keep route handlers small.",
                ]
            )
            self.security_checks.extend(
                [
                    "Enable rack-protection or equivalent request hardening middleware.",
                    "Set the session secret securely and keep it out of source control.",
                ]
            )
            self.documentation_checks.append(
                "Document middleware, extensions, and route organization when"
                " structure is not obvious from the app file."
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

    def as_review_guidelines(self) -> ReviewGuidelines:
        """Return this instance typed as a review guidelines bundle."""

        return self


__all__ = ["RubyGuidelines"]
