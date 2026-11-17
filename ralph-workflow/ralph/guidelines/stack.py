from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.guidelines._detected_stack import DetectedStack
from ralph.guidelines.go import GoGuidelines
from ralph.guidelines.java import JavaGuidelines
from ralph.guidelines.javascript import JavaScriptGuidelines
from ralph.guidelines.php import PHPGuidelines
from ralph.guidelines.python import PythonGuidelines
from ralph.guidelines.ruby import RubyGuidelines
from ralph.guidelines.rust import RustGuidelines

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ralph.guidelines._guideline_source import GuidelineSource
    from ralph.workspace.protocol import Workspace

    class _LanguageHandler:
        def __call__(
            self, frameworks: Iterable[str], *, typescript: bool = False
        ) -> GuidelineSource: ...


CATEGORY_FIELDS = (
    "quality_checks",
    "security_checks",
    "performance_checks",
    "testing_checks",
    "documentation_checks",
    "idioms",
    "anti_patterns",
    "concurrency_checks",
    "resource_checks",
    "observability_checks",
    "secrets_checks",
    "api_design_checks",
)

BASE_QUALITY_CHECKS = (
    "Code follows consistent style and formatting",
    "Functions have single responsibility",
    "Error handling is comprehensive",
    "No dead code or unused imports",
)
BASE_SECURITY_CHECKS = (
    "No hardcoded secrets or credentials",
    "Input validation on external data",
    "Proper authentication/authorization checks",
)
BASE_PERFORMANCE_CHECKS = (
    "No obvious performance bottlenecks",
    "Efficient data structures used",
)
BASE_TESTING_CHECKS = (
    "Tests cover main functionality",
    "Edge cases are tested",
)
BASE_DOCUMENTATION_CHECKS = (
    "Public APIs are documented",
    "Complex logic is expressed through self-documenting names, types, and structure",
)
BASE_IDIOMS = ("Code follows language conventions",)
BASE_ANTI_PATTERNS = ("Avoid code duplication",)
BASE_CONCURRENCY_CHECKS = (
    "Shared mutable state is properly synchronized",
    "No potential deadlocks (lock ordering)",
)
BASE_RESOURCE_CHECKS = (
    "Resources are properly closed/released",
    "No resource leaks in error paths",
)
BASE_OBSERVABILITY_CHECKS = (
    "Errors are logged with context",
    "Critical operations have appropriate logging",
)
BASE_SECRETS_CHECKS = (
    "Secrets loaded from environment/config, not hardcoded",
    "Sensitive data not logged or exposed in errors",
)
BASE_API_DESIGN_CHECKS = (
    "API follows consistent naming conventions",
    "Breaking changes are clearly documented",
)

LANGUAGE_FRAMEWORK_MAP: dict[str, tuple[str, ...]] = {
    "Python": ("Django", "FastAPI", "Flask"),
    "JavaScript": (
        "React",
        "Vue",
        "Angular",
        "Svelte",
        "Next.js",
        "Nuxt",
        "Express",
        "Fastify",
        "NestJS",
        "Gatsby",
    ),
    "Go": ("Gin", "Echo", "Fiber", "Gorilla", "Chi"),
    "Java": ("Spring",),
    "PHP": ("Laravel", "Symfony"),
    "Ruby": ("Rails", "Sinatra"),
}

SIGNATURE_LANGUAGE_MAP: dict[str, str] = {
    "setup.py": "Python",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "Pipfile": "Python",
    "Cargo.toml": "Rust",
    "package.json": "JavaScript",
    "tsconfig.json": "TypeScript",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "build.gradle.kts": "Kotlin",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
}


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _as_str_sequence(value: object) -> Sequence[str]:
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return value
    return ()


def _guideline_categories(source: GuidelineSource) -> tuple[tuple[str, Sequence[str]], ...]:
    return (
        ("quality_checks", _as_str_sequence(source.quality_checks)),
        ("security_checks", _as_str_sequence(source.security_checks)),
        ("performance_checks", _as_str_sequence(source.performance_checks)),
        ("testing_checks", _as_str_sequence(source.testing_checks)),
        ("documentation_checks", _as_str_sequence(source.documentation_checks)),
        ("idioms", _as_str_sequence(source.idioms)),
        ("anti_patterns", _as_str_sequence(source.anti_patterns)),
        ("concurrency_checks", _as_str_sequence(source.concurrency_checks)),
        ("resource_checks", _as_str_sequence(source.resource_checks)),
        ("observability_checks", _as_str_sequence(source.observability_checks)),
        ("secrets_checks", _as_str_sequence(source.secrets_checks)),
        ("api_design_checks", _as_str_sequence(source.api_design_checks)),
    )


def _frameworks_for_language(language: str, frameworks: Iterable[str]) -> list[str]:
    allowed = LANGUAGE_FRAMEWORK_MAP.get(language, ())
    allowed_lower = {fw.casefold() for fw in allowed}
    return [fw for fw in frameworks if fw.casefold() in allowed_lower]


def _language_group(language: str) -> str:
    if language == "Kotlin":
        return "Java"
    return language


def _rust_handler(frameworks: Iterable[str], *, typescript: bool = False) -> GuidelineSource:
    del frameworks, typescript
    return RustGuidelines()


def _python_handler(frameworks: Iterable[str], *, typescript: bool = False) -> GuidelineSource:
    del typescript
    return PythonGuidelines(frameworks=_frameworks_for_language("Python", frameworks))


def _javascript_handler(frameworks: Iterable[str], *, typescript: bool = False) -> GuidelineSource:
    return JavaScriptGuidelines(
        frameworks=_frameworks_for_language("JavaScript", frameworks),
        typescript=typescript,
    )


def _go_handler(frameworks: Iterable[str], *, typescript: bool = False) -> GuidelineSource:
    del typescript
    return GoGuidelines(frameworks=_frameworks_for_language("Go", frameworks))


def _java_handler(frameworks: Iterable[str], *, typescript: bool = False) -> GuidelineSource:
    del typescript
    return JavaGuidelines(frameworks=_frameworks_for_language("Java", frameworks))


def _php_handler(frameworks: Iterable[str], *, typescript: bool = False) -> GuidelineSource:
    del typescript
    return PHPGuidelines(frameworks=_frameworks_for_language("PHP", frameworks))


def _ruby_handler(frameworks: Iterable[str], *, typescript: bool = False) -> GuidelineSource:
    del typescript
    return RubyGuidelines(frameworks=_frameworks_for_language("Ruby", frameworks))


LANGUAGE_HANDLERS: dict[str, _LanguageHandler] = {
    "Rust": _rust_handler,
    "Python": _python_handler,
    "JavaScript": _javascript_handler,
    "Go": _go_handler,
    "Java": _java_handler,
    "Kotlin": _java_handler,
    "PHP": _php_handler,
    "Ruby": _ruby_handler,
}


class StackGuidelines:
    __slots__ = (
        "_seen",
        "anti_patterns",
        "api_design_checks",
        "concurrency_checks",
        "documentation_checks",
        "idioms",
        "observability_checks",
        "performance_checks",
        "quality_checks",
        "resource_checks",
        "secrets_checks",
        "security_checks",
        "testing_checks",
    )

    def __init__(
        self,
        quality_checks: Sequence[str] | None = None,
        security_checks: Sequence[str] | None = None,
        performance_checks: Sequence[str] | None = None,
        testing_checks: Sequence[str] | None = None,
        documentation_checks: Sequence[str] | None = None,
        idioms: Sequence[str] | None = None,
        anti_patterns: Sequence[str] | None = None,
        concurrency_checks: Sequence[str] | None = None,
        resource_checks: Sequence[str] | None = None,
        observability_checks: Sequence[str] | None = None,
        secrets_checks: Sequence[str] | None = None,
        api_design_checks: Sequence[str] | None = None,
    ) -> None:
        self.quality_checks = list(
            BASE_QUALITY_CHECKS if quality_checks is None else quality_checks
        )
        self.security_checks = list(
            BASE_SECURITY_CHECKS if security_checks is None else security_checks
        )
        self.performance_checks = list(
            BASE_PERFORMANCE_CHECKS if performance_checks is None else performance_checks
        )
        self.testing_checks = list(
            BASE_TESTING_CHECKS if testing_checks is None else testing_checks
        )
        self.documentation_checks = list(
            BASE_DOCUMENTATION_CHECKS if documentation_checks is None else documentation_checks
        )
        self.idioms = list(BASE_IDIOMS if idioms is None else idioms)
        self.anti_patterns = list(BASE_ANTI_PATTERNS if anti_patterns is None else anti_patterns)
        self.concurrency_checks = list(
            BASE_CONCURRENCY_CHECKS if concurrency_checks is None else concurrency_checks
        )
        self.resource_checks = list(
            BASE_RESOURCE_CHECKS if resource_checks is None else resource_checks
        )
        self.observability_checks = list(
            BASE_OBSERVABILITY_CHECKS if observability_checks is None else observability_checks
        )
        self.secrets_checks = list(
            BASE_SECRETS_CHECKS if secrets_checks is None else secrets_checks
        )
        self.api_design_checks = list(
            BASE_API_DESIGN_CHECKS if api_design_checks is None else api_design_checks
        )
        self._seen = {category: set(values) for category, values in _guideline_categories(self)}

    def merge_from(self, other: GuidelineSource) -> None:
        pairs: tuple[tuple[list[str], Sequence[str], str], ...] = (
            (self.quality_checks, other.quality_checks, "quality_checks"),
            (self.security_checks, other.security_checks, "security_checks"),
            (self.performance_checks, other.performance_checks, "performance_checks"),
            (self.testing_checks, other.testing_checks, "testing_checks"),
            (self.documentation_checks, other.documentation_checks, "documentation_checks"),
            (self.idioms, other.idioms, "idioms"),
            (self.anti_patterns, other.anti_patterns, "anti_patterns"),
            (self.concurrency_checks, other.concurrency_checks, "concurrency_checks"),
            (self.resource_checks, other.resource_checks, "resource_checks"),
            (self.observability_checks, other.observability_checks, "observability_checks"),
            (self.secrets_checks, other.secrets_checks, "secrets_checks"),
            (self.api_design_checks, other.api_design_checks, "api_design_checks"),
        )
        for target, items, category in pairs:
            sequence = _as_str_sequence(items)
            if not sequence:
                continue
            seen = self._seen[category]
            for item in sequence:
                if item not in seen:
                    seen.add(item)
                    target.append(item)

    def summary(self) -> str:
        return (
            f"{len(self.quality_checks)} quality checks, "
            f"{len(self.security_checks)} security checks, "
            f"{len(self.anti_patterns)} anti-patterns"
        )

    def total_checks(self) -> int:
        return sum(len(items) for _, items in _guideline_categories(self))


def get_stack_guidelines(workspace: Workspace, root: str = "") -> StackGuidelines:
    del root
    stack = _fallback_detect_stack(workspace)
    languages = [stack.primary_language, *stack.secondary_languages]
    normalized = [lang for lang in languages if lang]
    typescript_enabled = any(lang == "TypeScript" for lang in normalized)
    normalized = [lang for lang in normalized if lang != "TypeScript"]

    guidelines = StackGuidelines()
    processed: set[str] = set()

    for language in normalized:
        key = _language_group(language)
        if key in processed:
            continue
        handler = LANGUAGE_HANDLERS.get(key)
        if not handler:
            continue
        frameworks = _frameworks_for_language(key, stack.frameworks)
        guidelines.merge_from(handler(frameworks, typescript=typescript_enabled))
        processed.add(key)

    return guidelines


def _fallback_detect_stack(workspace: Workspace) -> DetectedStack:
    languages: list[str] = []
    frameworks: list[str] = []

    for signature, language in SIGNATURE_LANGUAGE_MAP.items():
        if not workspace.exists(signature):
            continue
        languages.append(language)
        frameworks.extend(_frameworks_for_signature(workspace, signature, language))

    if not languages:
        return DetectedStack()

    unique_languages = _dedupe_strings(languages)
    return DetectedStack(
        primary_language=unique_languages[0],
        secondary_languages=unique_languages[1:],
        frameworks=_dedupe_strings(frameworks),
    )


def _frameworks_for_signature(workspace: Workspace, signature: str, language: str) -> list[str]:
    try:
        content = workspace.read(signature).casefold()
    except FileNotFoundError:
        return []

    return [
        framework
        for framework in LANGUAGE_FRAMEWORK_MAP.get(_language_group(language), ())
        if framework.casefold() in content
    ]
