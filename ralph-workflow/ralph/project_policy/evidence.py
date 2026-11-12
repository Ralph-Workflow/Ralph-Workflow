from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

from ralph.project_policy import markers
from ralph.project_policy.models import EvidenceEntry, MigrationCandidate

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

_MANIFEST_FILES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
)

_NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    {"JSON", "YAML", "TOML", "HTML", "CSS", "SCSS", "Sass", "Less", "Markdown", "Plain Text"}
)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def required_languages(stack: ProjectStack) -> set[str]:
    if stack.primary_language == "Unknown":
        return set()
    languages: set[str] = {stack.primary_language}
    for language in stack.secondary_languages:
        if language not in _NON_CODE_LANGUAGES:
            languages.add(language)
    return languages


def _exists(workspace: Workspace, path: str) -> bool:
    return bool(workspace.exists(path))


def _read(workspace: Workspace, path: str) -> str:
    if not workspace.exists(path):
        return ""
    return workspace.read(path)


def _consulted_signal_files(workspace: Workspace, paths: tuple[str, ...]) -> list[str]:
    return [p for p in paths if workspace.exists(p) or workspace.is_dir(p)]


def _directory_signature(workspace: Workspace, path: str) -> str:
    try:
        children = workspace.list_dir(path)
    except (FileNotFoundError, NotADirectoryError):
        children = []
    sentinel = "\n".join(sorted(children))
    return _sha256_hex(f"dir:{path}\n{sentinel}")


def _manifest_contains_any(workspace: Workspace, substrings: tuple[str, ...]) -> bool:
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content and any(sub in content for sub in substrings):
            return True
    return False


def design_system_required(workspace: Workspace, stack: ProjectStack) -> tuple[bool, list[str]]:
    triggered: list[str] = []
    ui_hit = stack.frameworks and set(stack.frameworks) & markers.UI_FRAMEWORK_SIGNALS
    if ui_hit:
        triggered.extend(sorted(ui_hit))
    css_hit = set(stack.secondary_languages) & markers.CSS_LANGUAGE_SIGNALS
    if css_hit:
        triggered.extend(sorted(css_hit))
    ux_triggered, ux_consulted = ux_required(workspace, stack)
    if ux_triggered:
        triggered.append("ux_implies_design_system")
        triggered.extend(f"ux:{signal}" for signal in ux_consulted)
    if triggered:
        triggered.extend(_consulted_signal_files(workspace, ("package.json",)))
    return (bool(triggered), triggered)


def ux_required(workspace: Workspace, stack: ProjectStack) -> tuple[bool, list[str]]:
    triggered: list[str] = []
    app_hit = set(stack.frameworks) & markers.UX_APP_FRAMEWORKS
    if app_hit:
        triggered.extend(sorted(app_hit))
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content:
            matches = [
                f"{manifest}:{sub}" for sub in markers.UX_ROUTER_DEP_SIGNALS if sub in content
            ]
            triggered.extend(matches)
    if triggered:
        triggered.extend(_consulted_signal_files(workspace, _MANIFEST_FILES))
    return (bool(triggered), triggered)


def performance_required(workspace: Workspace, stack: ProjectStack) -> tuple[bool, list[str]]:
    triggered: list[str] = []
    triggered.extend(_consulted_signal_files(workspace, markers.PERF_SIGNAL_PATHS))
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content:
            triggered.extend(
                f"{manifest}:{sub}" for sub in markers.PERF_DEP_SIGNALS if sub in content
            )
    return (bool(triggered), triggered)


def memory_required(workspace: Workspace, stack: ProjectStack) -> tuple[bool, list[str]]:
    triggered: list[str] = []
    triggered.extend(_consulted_signal_files(workspace, markers.MEMORY_SIGNAL_PATHS))
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content:
            triggered.extend(
                f"{manifest}:{sub}" for sub in markers.MEMORY_DEP_SIGNALS if sub in content
            )
    return (bool(triggered), triggered)


def conditional_domain_requirements(
    workspace: Workspace, stack: ProjectStack
) -> dict[str, tuple[bool, list[str]]]:
    design_system = design_system_required(workspace, stack)
    path_domains = {
        domain: (
            bool(paths := _consulted_signal_files(workspace, signal_paths)),
            paths,
        )
        for domain, signal_paths in markers.CONDITIONAL_SIGNAL_PATHS.items()
    }
    requirements = {
        "design-system": design_system,
        "ux": ux_required(workspace, stack),
        "performance": performance_required(workspace, stack),
        "memory-usage": memory_required(workspace, stack),
        "accessibility": design_system,
        **path_domains,
    }
    if workspace.exists(markers.APPLICABILITY_OVERRIDES_PATH):
        for line in workspace.read(markers.APPLICABILITY_OVERRIDES_PATH).splitlines():
            match = re.fullmatch(
                r'([a-z][a-z-]+)\s*=\s*"(required|inactive); reason: ([^;]+); review trigger: ([^"]+)"',
                line.strip(),
            )
            if match is None:
                continue
            domain, decision = str(match.group(1)), str(match.group(2))
            if domain not in requirements:
                continue
            if not str(match.group(3)).strip() or not str(match.group(4)).strip():
                continue
            if decision == "required":
                requirements[domain] = (True, [f"explicit:{domain}"])
            else:
                requirements[domain] = (False, [f"explicit-inactive:{domain}"])
    return requirements


_HEADING_NORMALIZE_RE = re.compile(r"^\s*#{1,2}\s*")


def _normalize_heading(line: str) -> str:
    stripped = _HEADING_NORMALIZE_RE.sub("", line)
    return stripped.strip().lower()


def _doc_recognized(content: str, path: str = "") -> str | None:
    conventional: dict[str, frozenset[str]] = {
        "TESTING.md": frozenset({"testing", "test policy", "testing policy"}),
        "SECURITY.md": frozenset({"security", "security considerations", "security policy"}),
        ".github/SECURITY.md": frozenset(
            {"security", "security considerations", "security policy"}
        ),
    }
    for line in content.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        normalized = _normalize_heading(line)
        if normalized in conventional.get(path, frozenset()):
            return normalized
        for phrase in markers.MIGRATION_HEADING_RECOGNIZERS:
            if phrase == normalized:
                return phrase
    return None


def _doc_has_migrated_marker(content: str) -> str | None:
    allowed_targets = set(markers.CORE_POLICY_FILES) | set(
        markers.CONDITIONAL_POLICY_FILES.values()
    )
    expected_markers = {
        markers.MIGRATED_MARKER_TEMPLATE.format(target=target).strip() for target in allowed_targets
    }
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line in expected_markers:
            for target in allowed_targets:
                if markers.MIGRATED_MARKER_TEMPLATE.format(target=target).strip() == line:
                    return target
    return None


def migration_candidates(workspace: Workspace) -> list[MigrationCandidate]:
    candidates: list[MigrationCandidate] = []
    seen_paths: set[str] = set()

    for path in markers.MIGRATION_CANDIDATE_PATHS:
        seen_paths.add(path)
        content = _read(workspace, path)
        if not content:
            continue
        phrase = _doc_recognized(content, path)
        if phrase is None:
            continue
        resolved = _is_migration_resolved(workspace, path, content)
        candidates.append(
            MigrationCandidate(path=path, recognized_heading=phrase, resolved=resolved)
        )

    try:
        entries = workspace.list_dir(markers.MIGRATION_DOCS_GLOB_DIR)
    except (FileNotFoundError, NotADirectoryError):
        entries = []
    for entry in entries:
        if not entry.endswith(".md"):
            continue
        path = f"{markers.MIGRATION_DOCS_GLOB_DIR}/{entry}"
        if path.startswith(markers.CANONICAL_DIR):
            continue
        if path in seen_paths:
            continue
        content = _read(workspace, path)
        if not content:
            continue
        phrase = _doc_recognized(content, path)
        if phrase is None:
            continue
        resolved = _is_migration_resolved(workspace, path, content)
        candidates.append(
            MigrationCandidate(path=path, recognized_heading=phrase, resolved=resolved)
        )

    return candidates


def _is_migration_resolved(workspace: Workspace, path: str, content: str) -> bool:
    target = _doc_has_migrated_marker(content)
    if target and _exists(workspace, f"{markers.CANONICAL_DIR}{target}"):
        return True
    return _doc_recognized(content, path) is None


def readiness_evidence(workspace: Workspace, stack: ProjectStack) -> list[EvidenceEntry]:
    paths: list[str] = [
        markers.AGENTS_MD,
        markers.CLAUDE_MD,
        markers.APPLICABILITY_OVERRIDES_PATH,
        markers.PORTFOLIO_PATH,
    ]
    paths.extend(f"{markers.CANONICAL_DIR}{filename}" for filename in markers.CORE_POLICY_FILES)
    paths.extend(
        f"{markers.CANONICAL_DIR}{filename}"
        for filename in markers.CONDITIONAL_POLICY_FILES.values()
    )
    paths.extend(markers.PERF_SIGNAL_PATHS)
    paths.extend(markers.MEMORY_SIGNAL_PATHS)
    for signal_paths in markers.CONDITIONAL_SIGNAL_PATHS.values():
        paths.extend(signal_paths)
    paths.extend(_MANIFEST_FILES)
    paths.extend(candidate.path for candidate in migration_candidates(workspace))

    seen: set[str] = set()
    entries: list[EvidenceEntry] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        is_directory = workspace.is_dir(path)
        if not workspace.exists(path) and not is_directory:
            entries.append(EvidenceEntry(rel_path=path, exists=False, content_sha256=None))
            continue
        if is_directory:
            entries.append(
                EvidenceEntry(
                    rel_path=path,
                    exists=True,
                    content_sha256=_directory_signature(workspace, path),
                )
            )
            continue
        content = workspace.read(path)
        entries.append(
            EvidenceEntry(
                rel_path=path,
                exists=True,
                content_sha256=_sha256_hex(content),
            )
        )
    return entries


def _serialize_stack(stack: ProjectStack) -> dict[str, object]:
    return {
        "primary_language": stack.primary_language,
        "secondary_languages": sorted(stack.secondary_languages),
        "frameworks": sorted(stack.frameworks),
        "has_tests": stack.has_tests,
        "test_framework": stack.test_framework,
        "package_manager": stack.package_manager,
    }


def evidence_signature(workspace: Workspace, stack: ProjectStack) -> str:
    entries = readiness_evidence(workspace, stack)
    payload = {
        "policy_contract_version": markers.POLICY_CONTRACT_VERSION,
        "policy_schema_version": markers.SCHEMA_VERSION,
        "stack": _serialize_stack(stack),
        "evidence": [
            {
                "rel_path": entry.rel_path,
                "exists": entry.exists,
                "content_sha256": entry.content_sha256,
            }
            for entry in entries
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _sha256_hex(canonical)


__all__ = [
    "conditional_domain_requirements",
    "design_system_required",
    "evidence_signature",
    "memory_required",
    "migration_candidates",
    "performance_required",
    "readiness_evidence",
    "required_languages",
    "ux_required",
]
