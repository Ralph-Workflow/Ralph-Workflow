"""Language detection helpers for the Python port."""

from __future__ import annotations

from pathlib import Path

from ralph.workspace.fs import FsWorkspace
from ralph.workspace.protocol import Workspace

from .extensions import extension_to_language, is_non_primary_language
from .models import MAX_SECONDARY_LANGUAGES, ProjectStack
from .scanner import count_extensions, detect_tests
from .signatures import detect_signature_files

WorkspaceLike = Workspace | str | Path


def _language_count_sort_key(item: tuple[str, int]) -> tuple[int, str]:
    return (-item[1], item[0])


def _coerce_workspace(workspace_or_root: WorkspaceLike) -> tuple[Workspace, str]:
    if isinstance(workspace_or_root, Workspace):
        return workspace_or_root, ""
    return FsWorkspace(workspace_or_root), ""


def _sorted_language_counts(workspace: Workspace, root: str) -> list[tuple[str, int]]:
    extension_counts = count_extensions(workspace, root)
    language_totals: dict[str, int] = {}

    for extension, count in extension_counts.items():
        language = extension_to_language(extension)
        if language is not None:
            language_totals[language] = language_totals.get(language, 0) + count

    return sorted(language_totals.items(), key=_language_count_sort_key)


def _prioritize_languages(language_counts: list[tuple[str, int]]) -> list[str]:
    if not language_counts:
        return []

    primary = next(
        (language for language, _ in language_counts if not is_non_primary_language(language)),
        language_counts[0][0],
    )
    secondary = [language for language, _ in language_counts if language != primary]
    return [primary, *secondary]


def detect_languages(workspace_or_root: WorkspaceLike, root: str = "") -> list[str]:
    workspace, coerced_root = _coerce_workspace(workspace_or_root)
    effective_root = root or coerced_root
    return _prioritize_languages(_sorted_language_counts(workspace, effective_root))


def get_project_stack(workspace_or_root: WorkspaceLike, root: str = "") -> ProjectStack:
    workspace, coerced_root = _coerce_workspace(workspace_or_root)
    effective_root = root or coerced_root

    language_counts = _sorted_language_counts(workspace, effective_root)
    prioritized = _prioritize_languages(language_counts)
    primary = prioritized[0] if prioritized else "Unknown"
    secondary = prioritized[1 : MAX_SECONDARY_LANGUAGES + 1]

    frameworks, test_framework, package_manager = detect_signature_files(workspace, effective_root)
    has_tests = bool(test_framework) or detect_tests(workspace, effective_root, primary)

    return ProjectStack(
        primary_language=primary,
        secondary_languages=secondary,
        frameworks=frameworks,
        has_tests=has_tests,
        test_framework=test_framework,
        package_manager=package_manager,
    )


__all__ = ["ProjectStack", "detect_languages", "get_project_stack"]
