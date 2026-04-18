"""Go-specific review guidelines.

Ported from the canonical Rust implementation in
``ralph-workflow/src/guidelines/go.rs`` and adapted for the Python port.
Includes core Go checks plus framework-specific additions for Gin, Chi,
Fiber, and Echo projects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class GoGuidelines:
    """Review guidelines for Go codebases.

    Args:
        frameworks: Optional framework names used to add stack-specific guidance.
    """

    __slots__ = (
        "anti_patterns",
        "api_design_checks",
        "concurrency_checks",
        "documentation_checks",
        "frameworks",
        "idioms",
        "observability_checks",
        "performance_checks",
        "quality_checks",
        "resource_checks",
        "secrets_checks",
        "security_checks",
        "testing_checks",
    )

    frameworks: tuple[str, ...]
    quality_checks: list[str]
    security_checks: list[str]
    performance_checks: list[str]
    testing_checks: list[str]
    documentation_checks: list[str]
    idioms: list[str]
    anti_patterns: list[str]
    concurrency_checks: list[str]
    resource_checks: list[str]
    observability_checks: list[str]
    secrets_checks: list[str]
    api_design_checks: list[str]

    def __init__(self, frameworks: Iterable[str] = ()) -> None:
        normalized_frameworks = tuple(frameworks)
        self.frameworks = normalized_frameworks
        self.quality_checks = [
            "Run go fmt and golint.",
            "Check all error returns.",
            "Use defer for cleanup.",
            "Keep functions short and focused.",
        ]
        self.security_checks = [
            "Validate input bounds before slice operations.",
            "Use crypto/rand for security-sensitive random numbers.",
            "Check for SQL injection in database queries.",
        ]
        self.performance_checks = [
            "Pre-allocate slices when size is known.",
            "Use sync.Pool for frequently allocated objects.",
            "Consider goroutine leaks.",
        ]
        self.testing_checks = [
            "Use table-driven tests.",
            "Test error paths explicitly.",
            "Use testify or similar for assertions when it improves clarity.",
        ]
        self.documentation_checks = [
            "Document exported types, functions, and packages with Go-style comments.",
            "Explain concurrency expectations, ownership, and side effects in non-obvious code.",
            "Keep README and API examples aligned with actual package behavior.",
        ]
        self.idioms = [
            "Accept interfaces, return structs.",
            "Make the zero value useful.",
            "Don't communicate by sharing memory.",
        ]
        self.anti_patterns = [
            "Don't ignore returned errors.",
            "Avoid init() when possible.",
            "Don't use panic for normal error handling.",
        ]
        self.concurrency_checks = [
            "Use context propagation and cancellation for goroutines and I/O boundaries.",
            "Protect shared state with clear synchronization strategy and avoid race-prone access.",
            "Ensure goroutines terminate cleanly on shutdown, timeout, or caller cancellation.",
        ]
        self.resource_checks = [
            "Close files, response bodies, and other resources on every path.",
            "Use defer near acquisition sites so cleanup is obvious and reliable.",
            "Check cleanup-related errors when they can change user-visible behavior.",
        ]
        self.observability_checks = [
            "Log errors with enough context to diagnose request, job, or goroutine failures.",
            "Avoid noisy logs inside hot paths and tight retry loops.",
            "Expose meaningful metrics or traces for latency-critical and concurrent operations.",
        ]
        self.secrets_checks = [
            "Load secrets from environment or configuration, not source code.",
            "Do not log credentials, tokens, or sensitive request data.",
            "Review default configuration values for accidental secret exposure.",
        ]
        self.api_design_checks = [
            "Keep package APIs small, consistent, and idiomatic for Go consumers.",
            "Return errors as the final result value and make failure modes explicit.",
            "Pass context.Context explicitly at request and I/O boundaries.",
        ]

        for framework in normalized_frameworks:
            self._apply_framework(framework)

    def _apply_framework(self, framework: str) -> None:
        """Add framework-specific guideline extensions."""
        framework_name = framework.casefold()

        if framework_name in {"gin", "chi", "fiber", "echo"}:
            self.quality_checks.extend(
                [
                    "Use proper error handling in handlers.",
                    "Use context for cancellation.",
                    "Structure handlers and middleware properly.",
                ]
            )
            self.security_checks.extend(
                [
                    "Set proper CORS headers.",
                    "Validate input in handlers.",
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
                len(self.concurrency_checks),
                len(self.resource_checks),
                len(self.observability_checks),
                len(self.secrets_checks),
                len(self.api_design_checks),
            ]
        )


__all__ = ["GoGuidelines"]
