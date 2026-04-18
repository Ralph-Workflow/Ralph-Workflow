"""Stack-guided review guidelines for the Python port."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from types import ModuleType

    from ralph.workspace.protocol import Workspace


class _HandlerCallable(Protocol):
    """Protocol for language handler callables."""

    def __call__(self, *args: object, **kwargs: object) -> GuidelineSource: ...


@runtime_checkable
class GuidelineSource(Protocol):
    """Structural protocol for guideline objects produced by handlers."""

    quality_checks: Sequence[str]
    security_checks: Sequence[str]
    performance_checks: Sequence[str]
    testing_checks: Sequence[str]
    documentation_checks: Sequence[str]
    idioms: Sequence[str]
    anti_patterns: Sequence[str]
    concurrency_checks: Sequence[str]
    resource_checks: Sequence[str]
    observability_checks: Sequence[str]
    secrets_checks: Sequence[str]
    api_design_checks: Sequence[str]


@runtime_checkable
class _DetectedStackLike(Protocol):
    """Structural protocol for language detector results."""

    primary_language: str
    secondary_languages: Sequence[str]
    frameworks: Sequence[str]


class _StackDetector(Protocol):
    """Protocol for detect_stack_with_workspace callables."""

    def __call__(self, workspace: Workspace, root: str) -> object: ...


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


def _load_guideline_class(module_name: str, class_name: str) -> _HandlerCallable:
    module = import_module(module_name)
    return cast("_HandlerCallable", _module_attr(module, class_name))


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _guideline_categories(source: GuidelineSource) -> tuple[tuple[str, Sequence[str]], ...]:
    def _category(name: str) -> Sequence[str]:
        value: object = getattr(source, name, ())
        if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
            return cast("Sequence[str]", value)
        return ()

    return (
        ("quality_checks", _category("quality_checks")),
        ("security_checks", _category("security_checks")),
        ("performance_checks", _category("performance_checks")),
        ("testing_checks", _category("testing_checks")),
        ("documentation_checks", _category("documentation_checks")),
        ("idioms", _category("idioms")),
        ("anti_patterns", _category("anti_patterns")),
        ("concurrency_checks", _category("concurrency_checks")),
        ("resource_checks", _category("resource_checks")),
        ("observability_checks", _category("observability_checks")),
        ("secrets_checks", _category("secrets_checks")),
        ("api_design_checks", _category("api_design_checks")),
    )


def _stack_from_detected(source: _DetectedStackLike) -> DetectedStack:
    return DetectedStack(
        primary_language=source.primary_language,
        secondary_languages=list(source.secondary_languages),
        frameworks=list(source.frameworks),
    )


def _frameworks_for_language(language: str, frameworks: Iterable[str]) -> list[str]:
    allowed = LANGUAGE_FRAMEWORK_MAP.get(language, ())
    allowed_lower = {fw.casefold() for fw in allowed}
    return [fw for fw in frameworks if fw.casefold() in allowed_lower]


def _language_group(language: str) -> str:
    if language == "Kotlin":
        return "Java"
    return language


def _rust_handler(frameworks: Iterable[str], _: bool) -> GuidelineSource:
    rust_guidelines = _load_guideline_class("ralph.guidelines.rust", "RustGuidelines")
    return rust_guidelines()


def _python_handler(frameworks: Iterable[str], _: bool) -> GuidelineSource:
    python_guidelines = _load_guideline_class("ralph.guidelines.python", "PythonGuidelines")
    return python_guidelines(frameworks=_frameworks_for_language("Python", frameworks))


def _javascript_handler(frameworks: Iterable[str], typescript: bool) -> GuidelineSource:
    javascript_guidelines = _load_guideline_class(
        "ralph.guidelines.javascript",
        "JavaScriptGuidelines",
    )
    return javascript_guidelines(
        frameworks=_frameworks_for_language("JavaScript", frameworks),
        typescript=typescript,
    )


def _go_handler(frameworks: Iterable[str], _: bool) -> GuidelineSource:
    go_guidelines = _load_guideline_class("ralph.guidelines.go", "GoGuidelines")
    return go_guidelines(frameworks=_frameworks_for_language("Go", frameworks))


def _java_handler(frameworks: Iterable[str], _: bool) -> GuidelineSource:
    java_guidelines = _load_guideline_class("ralph.guidelines.java", "JavaGuidelines")
    return java_guidelines(frameworks=_frameworks_for_language("Java", frameworks))


def _php_handler(frameworks: Iterable[str], _: bool) -> GuidelineSource:
    php_guidelines = _load_guideline_class("ralph.guidelines.php", "PHPGuidelines")
    return php_guidelines(frameworks=_frameworks_for_language("PHP", frameworks))


def _ruby_handler(frameworks: Iterable[str], _: bool) -> GuidelineSource:
    ruby_guidelines = _load_guideline_class("ralph.guidelines.ruby", "RubyGuidelines")
    return ruby_guidelines(frameworks=_frameworks_for_language("Ruby", frameworks))


LANGUAGE_HANDLERS: dict[str, Callable[[Iterable[str], bool], GuidelineSource]] = {
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
    quality_checks: Sequence[str] = field(default_factory=lambda: list(BASE_QUALITY_CHECKS))
    security_checks: Sequence[str] = field(default_factory=lambda: list(BASE_SECURITY_CHECKS))
    performance_checks: Sequence[str] = field(default_factory=lambda: list(BASE_PERFORMANCE_CHECKS))
    testing_checks: Sequence[str] = field(default_factory=lambda: list(BASE_TESTING_CHECKS))
    documentation_checks: Sequence[str] = field(
        default_factory=lambda: list(BASE_DOCUMENTATION_CHECKS)
    )
    idioms: Sequence[str] = field(default_factory=lambda: list(BASE_IDIOMS))
    anti_patterns: Sequence[str] = field(default_factory=lambda: list(BASE_ANTI_PATTERNS))
    concurrency_checks: Sequence[str] = field(default_factory=lambda: list(BASE_CONCURRENCY_CHECKS))
    resource_checks: Sequence[str] = field(default_factory=lambda: list(BASE_RESOURCE_CHECKS))
    observability_checks: Sequence[str] = field(
        default_factory=lambda: list(BASE_OBSERVABILITY_CHECKS)
    )
    secrets_checks: Sequence[str] = field(default_factory=lambda: list(BASE_SECRETS_CHECKS))
    api_design_checks: Sequence[str] = field(default_factory=lambda: list(BASE_API_DESIGN_CHECKS))

    def __post_init__(self) -> None:
        self._seen: dict[str, set[str]] = {
            category: set(values) for category, values in _guideline_categories(self)
        }

    def merge_from(self, other: GuidelineSource) -> None:
        source_items = dict(_guideline_categories(other))
        for category, target in _guideline_categories(self):
            items = source_items[category]
            if not items:
                continue
            mutable_target = cast("list[str]", target)
            seen = self._seen[category]
            for item in items:
                if item not in seen:
                    seen.add(item)
                    mutable_target.append(item)

    def summary(self) -> str:
        return (
            f"{len(self.quality_checks)} quality checks, "
            f"{len(self.security_checks)} security checks, "
            f"{len(self.anti_patterns)} anti-patterns"
        )

    def total_checks(self) -> int:
        return sum(len(items) for _, items in _guideline_categories(self))


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

    module_dict: dict[str, object | None] = module.__dict__
    detector_candidate = module_dict.get("detect_stack_with_workspace")
    if detector_candidate is None or not callable(detector_candidate):
        return _fallback_detect_stack(workspace)

    detector = cast("_StackDetector", detector_candidate)
    stack_candidate = detector(workspace, root)
    if not isinstance(stack_candidate, _DetectedStackLike):
        return _fallback_detect_stack(workspace)
    return _stack_from_detected(stack_candidate)


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
