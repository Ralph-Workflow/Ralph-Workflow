"""PHP-specific review guideline categories.

Ported from the canonical Rust implementation in
``ralph-workflow/src/guidelines/php.rs`` and adapted for the Python port.
The module models core PHP guidance plus optional Laravel and Symfony
extensions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    class ReviewGuidelines(Protocol):
        """Protocol for language-specific guideline containers."""

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
class PHPGuidelines:
    """Review guidelines for PHP codebases.

    Args:
        frameworks: Optional framework names that activate stack-specific checks.
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
            "Use PHP 8+ features such as union types, attributes, and match "
            "where they improve clarity.",
            "Follow PSR-1/PSR-12 coding standards and keep PSR-4 autoloading tidy.",
            "Keep public APIs small and expressive with typed request/response signatures.",
            "Favor value objects, DTOs, and named arguments for constructors and factory helpers.",
        ]
        self.security_checks = [
            "Use prepared statements or parameter binding for every database query.",
            "Escape untrusted output with htmlspecialchars() or a trusted templating engine.",
            "Validate uploads, paths, and input metadata before processing.",
            "Use password_hash() and password_verify() for authentication flows.",
        ]
        self.performance_checks = [
            "Profile before optimizing and justify non-obvious performance trade-offs.",
            "Avoid repeated database queries inside loops; batch work and eager load "
            "relationships.",
            "Cache expensive computations and rendered fragments only when "
            "invalidation remains clear.",
        ]
        self.testing_checks = [
            "Cover validation, authorization, and persistence failure paths in automated tests.",
            "Exercise controller, route, or HTTP behavior with integration-style tests "
            "when the framework supports them.",
            "Protect serialization, database side effects, and exception handling "
            "instead of just happy paths.",
        ]
        self.documentation_checks = [
            "Document public classes, functions, and console commands with actual behavior, "
            "arguments, and return values.",
            "Keep setup docs, environment requirements, and deployment notes in sync "
            "with the live PHP stack and Composer configuration.",
            "Explain middleware, listener, and lifecycle behavior that affects "
            "request flow or bootstrapping.",
        ]
        self.idioms = [
            "Prefer Composer ecosystem conventions over custom abstractions.",
            "Keep services, controllers, and domain classes focused on single responsibilities.",
            "Use value objects, enums, and typed collections when they clarify intent.",
        ]
        self.anti_patterns = [
            "Avoid using extract() with user input.",
            "Do not suppress errors with the @ operator.",
            "Resist register_globals-style behavior and global state.",
        ]

        for framework in normalized_frameworks:
            self._apply_framework(framework)

    def _apply_framework(self, framework: str) -> None:
        """Add framework-specific guideline extensions."""
        framework_name = framework.casefold()

        if framework_name == "laravel":
            self.quality_checks.extend(
                [
                    "Use Eloquent relationships intentionally and avoid ad hoc query building "
                    "when eager loading fits.",
                    "Follow Laravel conventions for service providers, middleware, "
                    "and route structure.",
                    "Leverage Laravel's validation system instead of rolling custom checks "
                    "where possible.",
                    "Use middleware for cross-cutting concerns like logging, authentication, "
                    "and feature flags.",
                ]
            )
            self.security_checks.extend(
                [
                    "Keep CSRF protection enabled on state-changing routes.",
                    "Use Gates, Policies, and Form Requests for authorization and input "
                    "sanitization.",
                    "Sanitize input with request validation and escape output in views or APIs.",
                ]
            )
            self.performance_checks.extend(
                [
                    "Review Eloquent queries for N+1 risks and eager load where needed.",
                    "Push slow work to queues or async jobs when it shouldn't run in "
                    "the request cycle.",
                ]
            )
            return

        if framework_name == "symfony":
            self.quality_checks.extend(
                [
                    "Follow Symfony best practices for bundles, services, and controllers.",
                    "Use the DependencyInjection component consistently and keep wiring explicit.",
                    "Model validation with Symfony forms, constraints, or the validator component.",
                ]
            )
            self.security_checks.extend(
                [
                    "Configure Symfony Security with firewalls, encoders, and voters as intended.",
                    "Use voters or access control expressions for authorization.",
                ]
            )
            self.documentation_checks.append(
                "Document service wiring, bundles, and configuration conventions "
                "when defaults are overridden."
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


__all__ = ["PHPGuidelines"]
