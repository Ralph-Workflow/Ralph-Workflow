"""Stack-guided review guidelines for the Python port."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.workspace.protocol import Workspace

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

BASE_QUALITY_CHECKS = [
    "Code follows consistent style and formatting",
    "Functions have single responsibility",
    "Error handling is comprehensive",
    "No dead code or unused imports",
]
BASE_SECURITY_CHECKS = [
    "No hardcoded secrets or credentials",
    "Input validation on external data",
    "Proper authentication/authorization checks",
]
BASE_PERFORMANCE_CHECKS = [
    "No obvious performance bottlenecks",
    "Efficient data structures used",
]
BASE_TESTING_CHECKS = [
    "Tests cover main functionality",
    "Edge cases are tested",
]
BASE_DOCUMENTATION_CHECKS = [
    "Public APIs are documented",
    "Complex logic has explanatory comments",
]
BASE_IDIOMS = ["Code follows language conventions"]
BASE_ANTI_PATTERNS = ["Avoid code duplication"]
BASE_CONCURRENCY_CHECKS = [
    "Shared mutable state is properly synchronized",
    "No potential deadlocks (lock ordering)",
]
BASE_RESOURCE_CHECKS = [
    "Resources are properly closed/released",
    "No resource leaks in error paths",
]
BASE_OBSERVABILITY_CHECKS = [
    "Errors are logged with context",
    "Critical operations have appropriate logging",
]
BASE_SECRETS_CHECKS = [
    "Secrets loaded from environment/config, not hardcoded",
    "Sensitive data not logged or exposed in errors",
]
BASE_API_DESIGN_CHECKS = [
    "API follows consistent naming conventions",
    "Breaking changes are clearly documented",
]

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


@dataclass
class DetectedStack:
    primary_language: str = "Unknown"
    secondary_languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)


def _load_guideline_class(module_name: str, class_name: str) -> object:
    module = import_module(module_name)
    return getattr(module, class_name)

def _frameworks_for_language(language: str, frameworks: Iterable[str]) -> list[str]:
    allowed = LANGUAGE_FRAMEWORK_MAP.get(language, ())
    allowed_lower = {fw.casefold() for fw in allowed}
    return [fw for fw in frameworks if fw.casefold() in allowed_lower]

def _language_group(language: str) -> str:
    if language == "Kotlin":
        return "Java"
    return language

def _rust_handler(frameworks: Iterable[str], _: bool) -> object:
    rust_guidelines = cast(
        "Callable[..., object]",
        _load_guideline_class("ralph.guidelines.rust", "RustGuidelines"),
    )
    return rust_guidelines()

def _python_handler(frameworks: Iterable[str], _: bool) -> object:
    python_guidelines = cast(
        "Callable[..., object]",
        _load_guideline_class("ralph.guidelines.python", "PythonGuidelines"),
    )
    return python_guidelines(frameworks=_frameworks_for_language("Python", frameworks))

def _javascript_handler(frameworks: Iterable[str], typescript: bool) -> object:
    javascript_guidelines = cast(
        "Callable[..., object]",
        _load_guideline_class(
            "ralph.guidelines.javascript",
            "JavaScriptGuidelines",
        ),
    )
    return javascript_guidelines(
        frameworks=_frameworks_for_language("JavaScript", frameworks),
        typescript=typescript,
    )

def _go_handler(frameworks: Iterable[str], _: bool) -> object:
    go_guidelines = cast(
        "Callable[..., object]",
        _load_guideline_class("ralph.guidelines.go", "GoGuidelines"),
    )
    return go_guidelines(frameworks=_frameworks_for_language("Go", frameworks))

def _java_handler(frameworks: Iterable[str], _: bool) -> object:
    java_guidelines = cast(
        "Callable[..., object]",
        _load_guideline_class("ralph.guidelines.java", "JavaGuidelines"),
    )
    return java_guidelines(frameworks=_frameworks_for_language("Java", frameworks))

def _php_handler(frameworks: Iterable[str], _: bool) -> object:
    php_guidelines = cast(
        "Callable[..., object]",
        _load_guideline_class("ralph.guidelines.php", "PHPGuidelines"),
    )
    return php_guidelines(frameworks=_frameworks_for_language("PHP", frameworks))

def _ruby_handler(frameworks: Iterable[str], _: bool) -> object:
    ruby_guidelines = cast(
        "Callable[..., object]",
        _load_guideline_class("ralph.guidelines.ruby", "RubyGuidelines"),
    )
    return ruby_guidelines(frameworks=_frameworks_for_language("Ruby", frameworks))

LANGUAGE_HANDLERS: dict[str, Callable[[Iterable[str], bool], object]] = {
    "Rust": _rust_handler,
    "Python": _python_handler,
    "JavaScript": _javascript_handler,
    "Go": _go_handler,
    "Java": _java_handler,
    "Kotlin": _java_handler,
    "PHP": _php_handler,
    "Ruby": _ruby_handler,
}


@dataclass
class StackGuidelines:
    quality_checks: list[str] = field(default_factory=lambda: list(BASE_QUALITY_CHECKS))
    security_checks: list[str] = field(default_factory=lambda: list(BASE_SECURITY_CHECKS))
    performance_checks: list[str] = field(default_factory=lambda: list(BASE_PERFORMANCE_CHECKS))
    testing_checks: list[str] = field(default_factory=lambda: list(BASE_TESTING_CHECKS))
    documentation_checks: list[str] = field(default_factory=lambda: list(BASE_DOCUMENTATION_CHECKS))
    idioms: list[str] = field(default_factory=lambda: list(BASE_IDIOMS))
    anti_patterns: list[str] = field(default_factory=lambda: list(BASE_ANTI_PATTERNS))
    concurrency_checks: list[str] = field(default_factory=lambda: list(BASE_CONCURRENCY_CHECKS))
    resource_checks: list[str] = field(default_factory=lambda: list(BASE_RESOURCE_CHECKS))
    observability_checks: list[str] = field(default_factory=lambda: list(BASE_OBSERVABILITY_CHECKS))
    secrets_checks: list[str] = field(default_factory=lambda: list(BASE_SECRETS_CHECKS))
    api_design_checks: list[str] = field(default_factory=lambda: list(BASE_API_DESIGN_CHECKS))

    def __post_init__(self) -> None:
        self._seen: dict[str, set[str]] = {
            category: set(getattr(self, category)) for category in CATEGORY_FIELDS
        }

    def merge_from(self, other: object) -> None:
        for category in CATEGORY_FIELDS:
            items = getattr(other, category, None)
            if not items:
                continue
            target = getattr(self, category)
            seen = self._seen[category]
            for item in items:
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
        return sum(len(getattr(self, category)) for category in CATEGORY_FIELDS)


def get_stack_guidelines(workspace: Workspace, root: str = "") -> StackGuidelines:
    stack = _detect_stack_with_workspace(workspace, root)
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
        guidelines.merge_from(handler(frameworks, typescript_enabled))
        processed.add(key)

    return guidelines


def _detect_stack_with_workspace(workspace: Workspace, root: str) -> DetectedStack:
    try:
        module = import_module("ralph.language_detector")
    except ImportError:
        return _fallback_detect_stack(workspace)

    detector = getattr(module, "detect_stack_with_workspace", None)
    if detector is None:
        return _fallback_detect_stack(workspace)

    stack = detector(workspace, root)
    return DetectedStack(
        primary_language=getattr(stack, "primary_language", "Unknown"),
        secondary_languages=list(getattr(stack, "secondary_languages", [])),
        frameworks=list(getattr(stack, "frameworks", [])),
    )


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

    unique_languages = list(dict.fromkeys(languages))
    return DetectedStack(
        primary_language=unique_languages[0],
        secondary_languages=unique_languages[1:],
        frameworks=list(dict.fromkeys(frameworks)),
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
