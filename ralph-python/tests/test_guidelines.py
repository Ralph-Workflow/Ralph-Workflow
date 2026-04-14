"""Tests for language review guidelines and stack aggregation helpers."""

from __future__ import annotations

from types import SimpleNamespace

from ralph.guidelines.go import GoGuidelines
from ralph.guidelines.java import JavaGuidelines
from ralph.guidelines.javascript import JavaScriptGuidelines
from ralph.guidelines.php import PHPGuidelines
from ralph.guidelines.python import PythonGuidelines
from ralph.guidelines.ruby import RubyGuidelines
from ralph.guidelines.rust import RustGuidelines
from ralph.guidelines import stack
from ralph.guidelines.stack import DetectedStack, StackGuidelines
from ralph.workspace.memory import MemoryWorkspace


def _total_from_categories(guidelines: object, categories: tuple[str, ...]) -> int:
    return sum(len(getattr(guidelines, category)) for category in categories)


def test_rust_guidelines_summary_and_total_checks_cover_all_categories() -> None:
    guidelines = RustGuidelines()

    assert any(
        "No unwrap/expect in production paths" in item
        for item in guidelines.quality_checks
    )
    assert "Shared mutable state is properly synchronized" in guidelines.concurrency_checks
    assert (
        guidelines.summary()
        == f"{len(guidelines.quality_checks)} quality checks, "
        f"{len(guidelines.security_checks)} security checks, "
        f"{len(guidelines.anti_patterns)} anti-patterns"
    )
    assert guidelines.total_checks() == _total_from_categories(
        guidelines,
        (
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
        ),
    )


def test_python_guidelines_apply_all_supported_framework_extensions() -> None:
    guidelines = PythonGuidelines(["Django", "FastAPI", "Flask"])

    assert guidelines.frameworks == ("Django", "FastAPI", "Flask")
    assert "Use Django ORM features intentionally and avoid ad hoc SQL where ORM fits." in guidelines.quality_checks
    assert "Implement OAuth2 or JWT handling with explicit validation and expiry checks." in guidelines.security_checks
    assert "Use Blueprints to keep route registration and application structure modular." in guidelines.quality_checks
    assert guidelines.total_checks() == _total_from_categories(
        guidelines,
        (
            "quality_checks",
            "security_checks",
            "performance_checks",
            "testing_checks",
            "documentation_checks",
            "idioms",
            "anti_patterns",
        ),
    )


def test_javascript_guidelines_apply_frontend_backend_ssr_and_typescript_extensions() -> None:
    guidelines = JavaScriptGuidelines(
        frameworks=["React", "Angular", "Express", "Next.js"],
        typescript=True,
    )

    assert "Components are properly modularized." in guidelines.quality_checks
    assert "Use hooks correctly (rules of hooks)." in guidelines.quality_checks
    assert "Use OnPush change detection where possible." in guidelines.quality_checks
    assert "Use middleware patterns effectively." in guidelines.quality_checks
    assert "Use the appropriate rendering strategy (SSR, SSG, or ISR)." in guidelines.quality_checks
    assert "Use strict TypeScript mode." in guidelines.quality_checks
    assert "Do not use as casts to bypass type checking." in guidelines.anti_patterns
    assert guidelines.total_checks() == _total_from_categories(
        guidelines,
        (
            "quality_checks",
            "security_checks",
            "performance_checks",
            "testing_checks",
            "documentation_checks",
            "idioms",
            "anti_patterns",
        ),
    )


def test_go_guidelines_apply_framework_extensions() -> None:
    guidelines = GoGuidelines(["Gin", "Chi"])

    assert "Use proper error handling in handlers." in guidelines.quality_checks
    assert "Set proper CORS headers." in guidelines.security_checks
    assert guidelines.total_checks() == _total_from_categories(
        guidelines,
        (
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
        ),
    )


def test_java_php_and_ruby_guidelines_apply_framework_extensions() -> None:
    java_guidelines = JavaGuidelines(["Spring"])
    php_guidelines = PHPGuidelines(["Laravel", "Symfony"])
    ruby_guidelines = RubyGuidelines(["Rails", "Sinatra"])

    assert "Use constructor injection." in java_guidelines.quality_checks
    assert "Verify controller validation, serialization, and security behavior." in java_guidelines.testing_checks
    assert "Use Gates, Policies, and Form Requests for authorization and input sanitization." in php_guidelines.security_checks
    assert "Document service wiring, bundles, and configuration conventions when defaults are overridden." in php_guidelines.documentation_checks
    assert "Keep Rails CSRF protection enabled for state-changing requests." in ruby_guidelines.security_checks
    assert "Document middleware, extensions, and route organization when structure is not obvious from the app file." in ruby_guidelines.documentation_checks
    assert ruby_guidelines.as_review_guidelines() is ruby_guidelines


def test_stack_guidelines_merge_from_deduplicates_items_per_category() -> None:
    merged = StackGuidelines()
    source = SimpleNamespace(
        quality_checks=[
            "Code follows consistent style and formatting",
            "New quality rule",
        ],
        security_checks=["No hardcoded secrets or credentials", "Extra security rule"],
        performance_checks=[],
        testing_checks=None,
        documentation_checks=[],
        idioms=["Code follows language conventions", "Extra idiom"],
        anti_patterns=["Avoid code duplication", "Extra anti-pattern"],
        concurrency_checks=[],
        resource_checks=[],
        observability_checks=[],
        secrets_checks=[],
        api_design_checks=[],
    )

    merged.merge_from(source)

    assert merged.quality_checks.count("Code follows consistent style and formatting") == 1
    assert merged.quality_checks[-1] == "New quality rule"
    assert merged.security_checks[-1] == "Extra security rule"
    assert merged.idioms[-1] == "Extra idiom"
    assert merged.anti_patterns[-1] == "Extra anti-pattern"


def test_framework_helpers_filter_case_insensitively_and_group_kotlin() -> None:
    frameworks = stack._frameworks_for_language("JavaScript", ["react", "ANGULAR", "Rails"])

    assert frameworks == ["react", "ANGULAR"]
    assert stack._language_group("Kotlin") == "Java"
    assert stack._language_group("Ruby") == "Ruby"


def test_fallback_detect_stack_reads_signatures_and_frameworks() -> None:
    workspace = MemoryWorkspace()
    workspace.write("package.json", '{"dependencies": {"react": "18"}}')
    workspace.write("tsconfig.json", "{}")
    workspace.write("pyproject.toml", "[tool.poetry]\nname='demo'\ndjango='*'")

    detected = stack._fallback_detect_stack(workspace)

    assert detected == DetectedStack(
        primary_language="Python",
        secondary_languages=["JavaScript", "TypeScript"],
        frameworks=["Django", "React"],
    )


def test_detect_stack_with_workspace_uses_fallback_when_language_detector_is_unavailable(monkeypatch) -> None:
    workspace = MemoryWorkspace()
    workspace.write("Cargo.toml", "[package]\nname='demo'")

    real_import_module = stack.import_module

    def fake_import_module(module_name: str):
        if module_name == "ralph.language_detector":
            raise ImportError("boom")
        return real_import_module(module_name)

    monkeypatch.setattr(stack, "import_module", fake_import_module)

    detected = stack._detect_stack_with_workspace(workspace, "")

    assert detected.primary_language == "Rust"
    assert detected.secondary_languages == []


def test_detect_stack_with_workspace_uses_detector_result_when_available(monkeypatch) -> None:
    workspace = MemoryWorkspace()

    detector_module = SimpleNamespace(
        detect_stack_with_workspace=lambda _workspace, _root: SimpleNamespace(
            primary_language="Go",
            secondary_languages=["Python"],
            frameworks=["Gin"],
        )
    )
    monkeypatch.setattr(stack, "import_module", lambda _name: detector_module)

    detected = stack._detect_stack_with_workspace(workspace, "services")

    assert detected == DetectedStack(
        primary_language="Go",
        secondary_languages=["Python"],
        frameworks=["Gin"],
    )


def test_get_stack_guidelines_merges_detected_languages_once_and_enables_typescript(monkeypatch) -> None:
    monkeypatch.setattr(
        stack,
        "_detect_stack_with_workspace",
        lambda _workspace, _root: DetectedStack(
            primary_language="JavaScript",
            secondary_languages=["TypeScript", "Kotlin", "Python", "JavaScript"],
            frameworks=["React", "Spring", "Django", "Ignored"],
        ),
    )

    guidelines = stack.get_stack_guidelines(MemoryWorkspace())

    assert "Use strict TypeScript mode." in guidelines.quality_checks
    assert "Use hooks correctly (rules of hooks)." in guidelines.quality_checks
    assert "Use Django ORM features intentionally and avoid ad hoc SQL where ORM fits." in guidelines.quality_checks
    assert "Use constructor injection." in guidelines.quality_checks
    assert guidelines.quality_checks.count("Use hooks correctly (rules of hooks).") == 1
    assert guidelines.summary() == (
        f"{len(guidelines.quality_checks)} quality checks, "
        f"{len(guidelines.security_checks)} security checks, "
        f"{len(guidelines.anti_patterns)} anti-patterns"
    )
    assert guidelines.total_checks() == _total_from_categories(guidelines, stack.CATEGORY_FIELDS)
