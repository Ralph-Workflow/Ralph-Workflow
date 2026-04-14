"""Java-specific review guideline categories.

Ported from the canonical Rust implementation in
``ralph-workflow/src/guidelines/java.rs`` and adapted for the Python port.
The module exposes a lightweight data container with optional Spring-specific
extensions for Java codebases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass
class JavaGuidelines:
    """Java language-specific review checks.

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
    concurrency_checks: list[str] = field(init=False)
    resource_checks: list[str] = field(init=False)
    observability_checks: list[str] = field(init=False)
    api_design_checks: list[str] = field(init=False)

    def __init__(self, frameworks: Iterable[str] = ()) -> None:
        normalized_frameworks = tuple(frameworks)
        self.frameworks = normalized_frameworks
        self.quality_checks = [
            "Follow Java naming conventions.",
            "Use Optional instead of null returns where it clarifies absence.",
            "Prefer composition over inheritance.",
            "Use try-with-resources for AutoCloseable resources.",
        ]
        self.security_checks = [
            "Use PreparedStatement or parameterized queries for SQL access.",
            "Validate deserialized objects before use.",
            "Check for path traversal in file operations.",
        ]
        self.performance_checks = [
            "Avoid unnecessary object allocation in hot paths.",
            "Choose data structures and stream usage based on measured costs.",
            "Be explicit about blocking I/O and expensive database access.",
        ]
        self.testing_checks = [
            "Cover service, repository, and controller behavior with focused tests.",
            "Test exception handling and validation failures, not only happy paths.",
            "Keep mocks at network, filesystem, and process boundaries.",
        ]
        self.documentation_checks = [
            "Document public APIs and non-obvious business rules.",
            "Keep Javadoc aligned with actual parameters, return values, and exceptions.",
            "Explain transactional, threading, and framework-specific assumptions.",
        ]
        self.idioms = [
            "Use interfaces and records where they simplify API boundaries and DTOs.",
            "Prefer immutable value objects when mutability is not required.",
            "Use standard library and framework conventions before custom abstractions.",
        ]
        self.anti_patterns = [
            "Avoid catching Exception or Throwable.",
            "Do not use raw types with generics.",
            "Avoid public fields.",
        ]
        self.concurrency_checks = [
            "Shared mutable state is synchronized consistently.",
            "Executor, CompletableFuture, and thread-pool usage has clear lifecycle management.",
            "Concurrent code does not block event loops or request threads unexpectedly.",
        ]
        self.resource_checks = [
            "Connections, streams, and files are closed on success and failure paths.",
            "Database transactions, locks, and other managed resources have clear ownership.",
            "Long-lived caches and buffers have bounds and eviction strategy where needed.",
        ]
        self.observability_checks = [
            "Errors are logged with enough context to diagnose failures.",
            "Security-relevant and state-changing operations have appropriate audit visibility.",
            "Logs avoid exposing secrets and personally identifiable information.",
        ]
        self.api_design_checks = [
            "API naming is consistent across services, DTOs, and controller endpoints.",
            "Method contracts are explicit about nullability, exceptions, and side effects.",
            "Dependency injection boundaries stay clear and constructor-based where possible.",
        ]

        for framework in normalized_frameworks:
            self._apply_framework(framework)

    def _apply_framework(self, framework: str) -> None:
        """Add framework-specific guideline extensions."""
        if framework.casefold() != "spring":
            return

        self.quality_checks.extend(
            [
                "Use constructor injection.",
                "Follow Spring Boot conventions.",
                "Use proper transaction management.",
            ]
        )
        self.security_checks.extend(
            [
                "Configure Spring Security properly.",
                "Use @Valid for input validation.",
            ]
        )
        self.testing_checks.extend(
            [
                "Prefer focused slice tests before broad @SpringBootTest coverage.",
                "Verify controller validation, serialization, and security behavior.",
            ]
        )
        self.api_design_checks.append(
            "Keep Spring annotations focused and avoid leaking framework concerns across layers."
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
                len(self.api_design_checks),
            ]
        )


__all__ = ["JavaGuidelines"]
