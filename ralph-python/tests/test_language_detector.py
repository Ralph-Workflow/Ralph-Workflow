"""Tests for project language and framework detection helpers."""

from __future__ import annotations

from pathlib import Path

from ralph.language_detector import detect_languages, get_project_stack
from ralph.language_detector.extensions import extension_to_language, is_non_primary_language
from ralph.language_detector.models import MAX_SECONDARY_LANGUAGES, ProjectStack
from ralph.language_detector.scanner import (
    collect_signature_files,
    count_extensions,
    detect_tests,
    is_test_file_name,
)
from ralph.language_detector.signatures import DetectionResults, detect_signature_files
from ralph.workspace.memory import MemoryWorkspace


def test_extension_mapping_and_non_primary_language_flags() -> None:
    assert extension_to_language("TSX") == "TypeScript"
    assert extension_to_language("unknown") is None
    assert is_non_primary_language("JSON") is True
    assert is_non_primary_language("Python") is False


def test_detect_languages_prioritizes_non_support_language_after_primary() -> None:
    workspace = MemoryWorkspace()
    workspace.write("repo/src/config.json", "{}")
    workspace.write("repo/src/theme.css", "body {}")
    workspace.write("repo/src/app.py", "print('hi')")
    workspace.write("repo/src/helpers.py", "def helper():\n    return 1\n")

    detected = detect_languages(workspace, root="repo")

    assert detected == ["Python", "CSS", "JSON"]


def test_detect_languages_accepts_path_inputs_via_filesystem_workspace(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.go").write_text("package main", encoding="utf-8")
    (tmp_path / "src" / "helper.py").write_text("print('x')", encoding="utf-8")

    assert detect_languages(tmp_path) == ["Go", "Python"]


def test_get_project_stack_collects_frameworks_package_manager_and_tests() -> None:
    workspace = MemoryWorkspace()
    workspace.write("repo/pyproject.toml", "[tool.poetry]\nfastapi='*'\npytest='*'")
    workspace.write("repo/src/main.py", "print('hello')")
    workspace.write("repo/tests/test_main.py", "def test_main():\n    assert True\n")

    stack = get_project_stack(workspace, root="repo")

    assert stack.primary_language == "Python"
    assert stack.secondary_languages == []
    assert stack.frameworks == ["FastAPI"]
    assert stack.has_tests is True
    assert stack.test_framework == "pytest"
    assert stack.package_manager == "Poetry/pip"


def test_get_project_stack_limits_secondary_languages_to_configured_maximum() -> None:
    workspace = MemoryWorkspace()
    workspace.write("repo/src/app.py", "print('hi')")
    workspace.write("repo/src/app.rs", "fn main() {}")
    workspace.write("repo/src/app.js", "console.log('hi')")
    workspace.write("repo/src/app.ts", "export {}")
    workspace.write("repo/src/app.go", "package main")
    workspace.write("repo/src/App.java", "class App {}")
    workspace.write("repo/src/app.rb", "puts 'hi'")
    workspace.write("repo/src/app.php", "<?php echo 'hi';")

    stack = get_project_stack(workspace, root="repo")

    assert stack.primary_language == "Go"
    assert len(stack.secondary_languages) == MAX_SECONDARY_LANGUAGES
    assert stack.secondary_languages == [
        "Java",
        "JavaScript",
        "PHP",
        "Python",
        "Ruby",
        "Rust",
    ]


def test_project_stack_helpers_and_summary_cover_detected_state() -> None:
    stack = ProjectStack(
        primary_language="TypeScript",
        secondary_languages=["Python", "Rust"],
        frameworks=["React", "FastAPI"],
        has_tests=True,
        test_framework="Vitest",
        package_manager="npm",
    )

    assert stack.is_rust() is True
    assert stack.is_python() is True
    assert stack.is_javascript_or_typescript() is True
    assert stack.is_go() is False
    assert stack.summary() == "TypeScript (+Python, Rust) [React, FastAPI] tests:Vitest"


def test_detection_results_deduplicate_and_combine_multiple_entries() -> None:
    results = DetectionResults()
    results.with_framework("React").with_framework("React")
    results.with_test_framework("Jest").with_test_framework("Vitest")
    results.with_package_manager("npm").with_package_manager("pnpm")

    assert results.finish() == (
        ["React"],
        "Jest + Vitest",
        "npm + pnpm",
    )


def test_detect_signature_files_combines_frameworks_test_tools_and_package_managers() -> None:
    workspace = MemoryWorkspace()
    workspace.write("repo/frontend/package.json", '{"dependencies": {"react": "18", "next": "14"}, "devDependencies": {"vitest": "1"}}')
    workspace.write("repo/frontend/pnpm-lock.yaml", "lockfileVersion: '9'")
    workspace.write("repo/backend/go.mod", "require github.com/gin-gonic/gin v1.9.0")
    workspace.write("repo/composer.json", '{"require": {"laravel/framework": "10"}, "require-dev": {"phpunit/phpunit": "10"}}')

    frameworks, test_framework, package_manager = detect_signature_files(workspace, root="repo")

    assert frameworks == ["React", "Next.js", "Gin", "Laravel"]
    assert test_framework == "Vitest + go test + PHPUnit"
    assert package_manager == "pnpm + Go Modules + Composer"


def test_count_extensions_and_collect_signature_files_skip_ignored_paths_and_excess_depth() -> None:
    workspace = MemoryWorkspace()
    workspace.write("repo/src/app.py", "print('hi')")
    workspace.write("repo/vendor/ignored.php", "<?php")
    workspace.write("repo/node_modules/ignored.js", "console.log('x')")
    workspace.write("repo/a/b/c/d/e/f/g/package.json", "{}")
    workspace.write("repo/services/api/package.json", "{}")
    workspace.write("repo/services/api/pom.xml", "<project />")

    assert count_extensions(workspace, root="repo") == {"json": 2, "py": 1, "xml": 1}
    assert collect_signature_files(workspace, root="repo") == {
        "package.json": ["repo/services/api/package.json"],
        "pom.xml": ["repo/services/api/pom.xml"],
    }


def test_is_test_file_name_supports_language_specific_patterns() -> None:
    assert is_test_file_name("test_app.py", "Python", ["tests", "test_app.py"]) is True
    assert is_test_file_name("component.spec.tsx", "TypeScript", ["src", "component.spec.tsx"]) is True
    assert is_test_file_name("service_test.go", "Go", ["pkg", "service_test.go"]) is True
    assert is_test_file_name("UserServiceTest.java", "Java", ["src", "test", "java", "UserServiceTest.java"]) is True
    assert is_test_file_name("orders_spec.rb", "Ruby", ["spec", "orders_spec.rb"]) is True
    assert is_test_file_name("InvoiceTest.php", "PHP", ["tests", "InvoiceTest.php"]) is True
    assert is_test_file_name("tests.rs", "Rust", ["src", "tests.rs"]) is True
    assert is_test_file_name("spec-notes.txt", "Unknown", ["notes", "spec-notes.txt"]) is True


def test_detect_tests_finds_real_test_locations_but_skips_ignored_directories() -> None:
    workspace = MemoryWorkspace()
    workspace.write("repo/node_modules/tests/ignored.test.js", "")
    workspace.write("repo/src/app.py", "print('hi')")
    workspace.write("repo/pkg/module_test.go", "package pkg")

    assert detect_tests(workspace, root="repo", primary_language="Go") is True

    workspace.remove("repo/pkg/module_test.go")

    assert detect_tests(workspace, root="repo", primary_language="JavaScript") is False
