"""JavaScript-specific review guideline categories.

Ported from the canonical Rust implementation in
``ralph-workflow/src/guidelines/javascript.rs`` and adapted for the Python port.
The module models core JavaScript guidance plus optional framework-specific
extensions for React, Vue, Angular, Node backends, SSR stacks, and TypeScript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class JavaScriptGuidelines:
    """JavaScript and TypeScript review checks.

    Args:
        frameworks: Optional framework names used to add stack-specific guidance.
        typescript: When true, include the TypeScript-specific checks from the
            Rust implementation alongside the JavaScript baseline.
    """

    __slots__ = (
        "anti_patterns",
        "documentation_checks",
        "frameworks",
        "idioms",
        "performance_checks",
        "quality_checks",
        "security_checks",
        "testing_checks",
        "typescript",
    )

    frameworks: tuple[str, ...]
    typescript: bool
    quality_checks: list[str]
    security_checks: list[str]
    performance_checks: list[str]
    testing_checks: list[str]
    documentation_checks: list[str]
    idioms: list[str]
    anti_patterns: list[str]

    def __init__(self, frameworks: Iterable[str] = (), typescript: bool = False) -> None:
        normalized_frameworks = tuple(frameworks)
        self.frameworks = normalized_frameworks
        self.typescript = typescript
        self.quality_checks = [
            "Use const/let, never var.",
            "Handle Promise rejections explicitly.",
            "Use async/await over raw Promises when it improves readability.",
            "Avoid deeply nested callbacks.",
        ]
        self.security_checks = [
            "Sanitize user input before DOM insertion.",
            "Use Content Security Policy headers.",
            "Validate data from external APIs.",
            "Check for prototype pollution vulnerabilities.",
        ]
        self.performance_checks = [
            "Debounce or throttle frequent event handlers.",
            "Use appropriate data structures for lookup and iteration patterns.",
            "Minimize DOM manipulation.",
        ]
        self.testing_checks = [
            "Test async control flow, Promise rejection paths, and error boundaries.",
            "Cover user-driven UI events and state transitions with behavior-focused tests.",
            (
                "Exercise API validation, middleware, and serialization boundaries "
                "in integration tests."
            ),
        ]
        self.documentation_checks = [
            "Document public modules, exported APIs, and non-obvious framework conventions.",
            "Keep README and setup docs aligned with the runtime, build, and framework stack.",
            (
                "Explain security-sensitive data flow, hydration assumptions, "
                "and state ownership rules."
            ),
        ]
        self.idioms = [
            "Prefer immutable updates and small pure helpers where practical.",
            "Use language and framework conventions instead of custom abstractions.",
            "Keep modules focused and exports intentional.",
        ]
        self.anti_patterns = [
            "Avoid == for comparisons (use ===).",
            "Do not mutate function arguments.",
            "Avoid synchronous I/O in Node.js request or worker paths.",
        ]

        if any(framework.casefold() in {"react", "vue"} for framework in normalized_frameworks):
            self._apply_frontend_guidelines()

        for framework in normalized_frameworks:
            self._apply_framework(framework)

        if typescript:
            self._apply_typescript_guidelines()

    def _apply_typescript_guidelines(self) -> None:
        """Add the TypeScript-specific checks from the Rust implementation."""
        self.quality_checks.extend(
            [
                "Use strict TypeScript mode.",
                "Prefer interfaces over type aliases for object shapes.",
                "Use explicit return types for public functions.",
                "Avoid the any type; use unknown when narrowing is required.",
            ]
        )
        self.idioms.extend(
            [
                "Use union types for discriminated unions.",
                "Leverage type inference where intent stays clear.",
                "Use generics appropriately.",
            ]
        )
        self.anti_patterns.extend(
            [
                "Do not use as casts to bypass type checking.",
                "Avoid non-null assertions (!) without justification.",
            ]
        )

    def _apply_frontend_guidelines(self) -> None:
        """Add shared frontend guidance for React and Vue stacks."""
        self.quality_checks.extend(
            [
                "Components are properly modularized.",
                "State management is predictable.",
                "Accessibility (a11y) is considered.",
            ]
        )
        self.performance_checks.extend(
            [
                "Avoid unnecessary re-renders.",
                "Use lazy loading for large components.",
                "Optimize bundle size.",
            ]
        )

    def _apply_framework(self, framework: str) -> None:
        """Add framework-specific guideline extensions."""
        framework_name = framework.casefold()

        if framework_name == "react":
            self.quality_checks.extend(
                [
                    "Use hooks correctly (rules of hooks).",
                    "Properly manage component lifecycle.",
                    "Use React.memo for expensive renders.",
                ]
            )
            self.anti_patterns.extend(
                [
                    "Avoid prop drilling when context or state management fits better.",
                    "Do not mutate state directly.",
                    "Avoid inline functions in render when they create avoidable churn.",
                ]
            )
            return

        if framework_name == "vue":
            self.quality_checks.extend(
                [
                    "Use the Composition API for complex logic.",
                    "Follow the Vue style guide.",
                    "Use computed properties appropriately.",
                ]
            )
            self.anti_patterns.extend(
                [
                    "Avoid watchers when computed properties express the same intent.",
                    "Do not directly mutate props.",
                ]
            )
            return

        if framework_name == "angular":
            self.quality_checks.extend(
                [
                    "Use OnPush change detection where possible.",
                    "Follow the Angular style guide.",
                    "Use RxJS operators effectively.",
                ]
            )
            self.security_checks.append("Use Angular's built-in sanitization.")
            self.anti_patterns.extend(
                [
                    "Avoid subscribing without unsubscribing.",
                    "Do not use the any type.",
                ]
            )
            return

        if framework_name in {"express", "fastify", "nestjs"}:
            self.quality_checks.extend(
                [
                    "Use middleware patterns effectively.",
                    "Handle errors in middleware.",
                    "Use environment variables for configuration.",
                ]
            )
            self.security_checks.extend(
                [
                    "Use helmet or equivalent security headers.",
                    "Implement rate limiting.",
                    "Validate request body schemas.",
                ]
            )
            return

        if framework_name in {"next.js", "nuxt"}:
            self.quality_checks.extend(
                [
                    "Use the appropriate rendering strategy (SSR, SSG, or ISR).",
                    "Handle hydration correctly.",
                    "Optimize for Core Web Vitals.",
                ]
            )
            self.performance_checks.extend(
                [
                    "Minimize client-side JavaScript.",
                    "Use image optimization features.",
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


__all__ = ["JavaScriptGuidelines"]
