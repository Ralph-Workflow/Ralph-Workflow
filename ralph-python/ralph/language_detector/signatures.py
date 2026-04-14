"""Signature file heuristics for framework and package manager detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .scanner import collect_signature_files

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.workspace.protocol import Workspace


class DetectionResults:
    def __init__(self) -> None:
        self.frameworks: list[str] = []
        self.test_frameworks: list[str] = []
        self.package_managers: list[str] = []

    def with_framework(self, value: str) -> DetectionResults:
        if value not in self.frameworks:
            self.frameworks.append(value)
        return self

    def with_test_framework(self, value: str) -> DetectionResults:
        if value not in self.test_frameworks:
            self.test_frameworks.append(value)
        return self

    def with_package_manager(self, value: str) -> DetectionResults:
        if value not in self.package_managers:
            self.package_managers.append(value)
        return self

    def finish(self) -> tuple[list[str], str | None, str | None]:
        return (
            self.frameworks,
            _combine_unique(self.test_frameworks),
            _combine_unique(self.package_managers),
        )


def _combine_unique(items: list[str]) -> str | None:
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return " + ".join(items)


def _read_signature_contents(
    workspace: Workspace,
    signatures: dict[str, list[str]],
) -> dict[str, str]:
    contents: dict[str, str] = {}
    for paths in signatures.values():
        for path in paths:
            try:
                contents[path] = workspace.read(path).lower()
            except FileNotFoundError:
                continue
    return contents


def detect_signature_files(
    workspace: Workspace, root: str = ""
) -> tuple[list[str], str | None, str | None]:
    signatures = collect_signature_files(workspace, root)
    contents = _read_signature_contents(workspace, signatures)
    results = DetectionResults()
    results = _detect_rust(signatures, contents, results)
    results = _detect_python(signatures, contents, results)
    results = _detect_javascript(signatures, contents, results)
    results = _detect_go(signatures, contents, results)
    results = _detect_ruby(signatures, contents, results)
    results = _detect_java(signatures, contents, results)
    results = _detect_php(signatures, contents, results)
    return results.finish()


def _detect_rust(
    signatures: dict[str, list[str]], contents: dict[str, str], results: DetectionResults
) -> DetectionResults:
    files = signatures.get("cargo.toml")
    if not files:
        return results

    results = results.with_package_manager("Cargo")
    for path in files:
        content = contents.get(path, "")
        if "[dev-dependencies]" in content or "[[test]]" in content:
            results = results.with_test_framework("cargo test")
        for pattern, name in (
            ("actix", "Actix"),
            ("axum", "Axum"),
            ("rocket", "Rocket"),
            ("tokio", "Tokio"),
            ("warp", "Warp"),
            ("tauri", "Tauri"),
            ("leptos", "Leptos"),
            ("yew", "Yew"),
        ):
            if pattern in content:
                results = results.with_framework(name)
    return results


def _detect_python(
    signatures: dict[str, list[str]], contents: dict[str, str], results: DetectionResults
) -> DetectionResults:
    paths = signatures.get("pyproject.toml")
    if paths is not None:
        results = results.with_package_manager("Poetry/pip")
    else:
        paths = signatures.get("requirements.txt")
        if paths is not None:
            results = results.with_package_manager("pip")
        elif "setup.py" in signatures:
            return results.with_package_manager("setuptools")
        elif "pipfile" in signatures:
            return results.with_package_manager("Pipenv")
        else:
            return results

    for path in paths:
        content = contents.get(path)
        if content is None:
            continue
        if "pytest" in content:
            results = results.with_test_framework("pytest")
        for pattern, name in (
            ("django", "Django"),
            ("fastapi", "FastAPI"),
            ("flask", "Flask"),
        ):
            if pattern in content:
                results = results.with_framework(name)
    return results


def _detect_javascript(
    signatures: dict[str, list[str]], contents: dict[str, str], results: DetectionResults
) -> DetectionResults:
    paths = signatures.get("package.json")
    if paths is None:
        return results

    if "pnpm-lock.yaml" in signatures:
        results = results.with_package_manager("pnpm")
    elif "yarn.lock" in signatures:
        results = results.with_package_manager("Yarn")
    elif "bun.lockb" in signatures or "bun.lock" in signatures:
        results = results.with_package_manager("Bun")
    else:
        results = results.with_package_manager("npm")

    for path in paths:
        content = contents.get(path)
        if content is None:
            continue
        for pattern, name in (
            ('"jest"', "Jest"),
            ('"vitest"', "Vitest"),
            ('"mocha"', "Mocha"),
            ('"cypress"', "Cypress"),
            ('"playwright"', "Playwright"),
        ):
            if pattern in content:
                results = results.with_test_framework(name)
        for pattern, name in (
            ('"react"', "React"),
            ('"vue"', "Vue"),
            ('"angular"', "Angular"),
            ('"svelte"', "Svelte"),
            ('"next"', "Next.js"),
            ('"nuxt"', "Nuxt"),
            ('"express"', "Express"),
            ('"fastify"', "Fastify"),
            ('"nestjs"', "NestJS"),
            ('"gatsby"', "Gatsby"),
        ):
            if pattern in content:
                results = results.with_framework(name)
    return results


def _detect_go(
    signatures: dict[str, list[str]], contents: dict[str, str], results: DetectionResults
) -> DetectionResults:
    paths = signatures.get("go.mod")
    if paths is None:
        return results

    results = results.with_package_manager("Go Modules").with_test_framework("go test")
    for path in paths:
        content = contents.get(path)
        if content is None:
            continue
        for pattern, name in (
            ("gin-gonic/gin", "Gin"),
            ("labstack/echo", "Echo"),
            ("gofiber/fiber", "Fiber"),
            ("gorilla/mux", "Gorilla"),
            ("go-chi/chi", "Chi"),
        ):
            if pattern in content:
                results = results.with_framework(name)
    return results


def _detect_ruby(
    signatures: dict[str, list[str]], contents: dict[str, str], results: DetectionResults
) -> DetectionResults:
    paths = signatures.get("gemfile")
    if paths is None:
        return results

    results = results.with_package_manager("Bundler")
    for path in paths:
        content = contents.get(path)
        if content is None:
            continue
        if "rspec" in content:
            results = results.with_test_framework("RSpec")
        elif "minitest" in content:
            results = results.with_test_framework("Minitest")
        if "rails" in content:
            results = results.with_framework("Rails")
        elif "sinatra" in content:
            results = results.with_framework("Sinatra")
    return results


def _detect_java(
    signatures: dict[str, list[str]], contents: dict[str, str], results: DetectionResults
) -> DetectionResults:
    pom_paths = signatures.get("pom.xml")
    if pom_paths is not None:
        results = results.with_package_manager("Maven")
        results = _detect_java_frameworks(contents, pom_paths, results)

    gradle_paths = [*signatures.get("build.gradle", []), *signatures.get("build.gradle.kts", [])]
    if gradle_paths:
        results = results.with_package_manager("Gradle")
        results = _detect_java_frameworks(contents, gradle_paths, results)
    return results


def _detect_java_frameworks(
    contents: dict[str, str], paths: Iterable[str], results: DetectionResults
) -> DetectionResults:
    for path in paths:
        content = contents.get(path)
        if content is None:
            continue
        if "junit" in content:
            results = results.with_test_framework("JUnit")
        if "spring" in content:
            results = results.with_framework("Spring")
    return results


def _detect_php(
    signatures: dict[str, list[str]], contents: dict[str, str], results: DetectionResults
) -> DetectionResults:
    paths = signatures.get("composer.json")
    if paths is None:
        return results

    results = results.with_package_manager("Composer")
    for path in paths:
        content = contents.get(path)
        if content is None:
            continue
        if "phpunit" in content:
            results = results.with_test_framework("PHPUnit")
        for pattern, name in (("laravel", "Laravel"), ("symfony", "Symfony")):
            if pattern in content:
                results = results.with_framework(name)
    return results
