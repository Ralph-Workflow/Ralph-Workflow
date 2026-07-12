"""Shared readiness-evidence inventory for the project-policy preflight.

This module is the SINGLE owner of every filesystem input the readiness
decision depends on. The validator and the cache BOTH consume the inventory
returned by :func:`readiness_evidence` and the signature returned by
:func:`evidence_signature`; they cannot diverge because they share the same
input.

The inventory captures (per file): path, existence, and content SHA-256.
``content_sha256`` is ``None`` when the file does not exist so a deletion
invalidates a cached READY just like an edit.

Conditional-domain detection follows the exact signals declared in
:mod:`ralph.project_policy.markers`. The functions return a tuple of
``(required, consulted_signal_files)`` so the orchestrator can report which
inputs triggered each domain.

All file reads happen through the injected :class:`~ralph.workspace.protocol.Workspace`
seam. The module never touches raw ``pathlib.Path`` I/O, keeping it
testable with :class:`~ralph.workspace.memory.MemoryWorkspace`.
"""

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

# Manifest dependency files scanned for the UX router / perf / memory signal
# substrings. Each is read via the workspace seam when present.
_MANIFEST_FILES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
)

# Languages that are NOT considered "code" for typecheck/lint coverage.
# JSON, YAML, HTML, CSS family, etc. are markup/config and don't need
# per-language typecheck/lint coverage.
_NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    {"JSON", "YAML", "TOML", "HTML", "CSS", "SCSS", "Sass", "Less", "Markdown", "Plain Text"}
)


def _sha256_hex(text: str) -> str:
    """Return the lowercase hex SHA-256 of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def required_languages(stack: ProjectStack) -> set[str]:
    """Compute the set of code languages that must be covered by typecheck/lint.

    Returns the primary language plus every secondary language that is not
    a non-code language (JSON, YAML, HTML, CSS family, ...). Empty stack
    yields an empty set; the validator handles the empty case as "no
    languages required".
    """
    if stack.primary_language == "Unknown":
        return set()
    languages: set[str] = {stack.primary_language}
    for language in stack.secondary_languages:
        if language not in _NON_CODE_LANGUAGES:
            languages.add(language)
    return languages


def _exists(workspace: Workspace, path: str) -> bool:
    """Return True when ``path`` exists on the workspace seam."""
    return bool(workspace.exists(path))


def _read(workspace: Workspace, path: str) -> str:
    """Read ``path`` via the workspace seam; return "" when missing."""
    if not workspace.exists(path):
        return ""
    return workspace.read(path)


def _consulted_signal_files(workspace: Workspace, paths: tuple[str, ...]) -> list[str]:
    """Return the subset of signal paths that exist on the workspace."""
    return [p for p in paths if _exists(workspace, p)]


def _manifest_contains_any(workspace: Workspace, substrings: tuple[str, ...]) -> bool:
    """Return True when any manifest file contains any of ``substrings``."""
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content and any(sub in content for sub in substrings):
            return True
    return False


def design_system_required(workspace: Workspace, stack: ProjectStack) -> tuple[bool, list[str]]:
    """Determine whether a design-system policy is required.

    Required when ANY of these conditions holds:

    * ``stack.frameworks`` intersects ``markers.UI_FRAMEWORK_SIGNALS``, OR
    * ``stack.secondary_languages`` intersects ``markers.CSS_LANGUAGE_SIGNALS``, OR
    * ``ux_required(workspace, stack)`` returns True (a UX-substantial project
      also needs design-system coverage; the requirement is symmetric — UX
      implies both design-system AND ux, while design-system can stand on
      its own).

    The third condition is computed by calling :func:`ux_required` so the
    signal set is shared with the UX detector — there is no separate,
    hand-maintained signal list that could drift. The consultation list
    reports every signal file/framework that triggered the requirement so
    the orchestrator can produce a transparent report.
    """
    triggered: list[str] = []
    ui_hit = stack.frameworks and set(stack.frameworks) & markers.UI_FRAMEWORK_SIGNALS
    if ui_hit:
        triggered.extend(sorted(ui_hit))
    css_hit = set(stack.secondary_languages) & markers.CSS_LANGUAGE_SIGNALS
    if css_hit:
        triggered.extend(sorted(css_hit))
    # UX always implies design-system. Compute the UX signal list separately
    # so we can fold its triggers into the design-system report without
    # reporting a UX policy here (the orchestrator decides).
    ux_triggered, ux_consulted = ux_required(workspace, stack)
    if ux_triggered:
        triggered.append("ux_implies_design_system")
        triggered.extend(f"ux:{signal}" for signal in ux_consulted)
    if triggered:
        # Signal files exist only when a CSS/SCSS file is present; we still
        # report UI framework names because they triggered the requirement.
        triggered.extend(_consulted_signal_files(workspace, ("package.json",)))
    return (bool(triggered), triggered)


def ux_required(workspace: Workspace, stack: ProjectStack) -> tuple[bool, list[str]]:
    """Determine whether a UX policy is required (stricter than design-system).

    Required only when:

    * ``stack.frameworks`` intersects ``markers.UX_APP_FRAMEWORKS``, OR
    * any manifest file contains a router-dep substring from
      ``markers.UX_ROUTER_DEP_SIGNALS``.

    UX always implies design-system — a project using react-router needs
    both a design-system policy AND a UX policy.
    """
    triggered: list[str] = []
    app_hit = set(stack.frameworks) & markers.UX_APP_FRAMEWORKS
    if app_hit:
        triggered.extend(sorted(app_hit))
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content:
            matches = [
                f"{manifest}:{sub}"
                for sub in markers.UX_ROUTER_DEP_SIGNALS
                if sub in content
            ]
            triggered.extend(matches)
    if triggered:
        triggered.extend(_consulted_signal_files(workspace, _MANIFEST_FILES))
    return (bool(triggered), triggered)


def performance_required(
    workspace: Workspace, stack: ProjectStack
) -> tuple[bool, list[str]]:
    """Determine whether a performance policy is required.

    Required only when:

    * any of ``markers.PERF_SIGNAL_PATHS`` exists, OR
    * any manifest file contains a benchmarking dep substring from
      ``markers.PERF_DEP_SIGNALS``.

    Absent both signals, no performance policy is required (no invented
    targets, no empty file).
    """
    triggered: list[str] = []
    triggered.extend(_consulted_signal_files(workspace, markers.PERF_SIGNAL_PATHS))
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content:
            triggered.extend(
                f"{manifest}:{sub}"
                for sub in markers.PERF_DEP_SIGNALS
                if sub in content
            )
    return (bool(triggered), triggered)


def memory_required(
    workspace: Workspace, stack: ProjectStack
) -> tuple[bool, list[str]]:
    """Determine whether a memory-usage policy is required.

    Required only when:

    * any of ``markers.MEMORY_SIGNAL_PATHS`` exists, OR
    * any manifest file contains a memory-tooling dep substring from
      ``markers.MEMORY_DEP_SIGNALS``.

    Absent both signals, no memory-usage policy is required.
    """
    triggered: list[str] = []
    triggered.extend(_consulted_signal_files(workspace, markers.MEMORY_SIGNAL_PATHS))
    for manifest in _MANIFEST_FILES:
        content = _read(workspace, manifest)
        if content:
            triggered.extend(
                f"{manifest}:{sub}"
                for sub in markers.MEMORY_DEP_SIGNALS
                if sub in content
            )
    return (bool(triggered), triggered)


# Pre-compile the heading normalizer once at import time.
_HEADING_NORMALIZE_RE = re.compile(r"^\s*#{1,2}\s*")


def _normalize_heading(line: str) -> str:
    """Normalize a heading line for migration recognition.

    Strips the leading H1/H2 hashes and surrounding whitespace; lowercases
    the result. The recognizers operate on the normalized form.
    """
    stripped = _HEADING_NORMALIZE_RE.sub("", line)
    return stripped.strip().lower()


def _doc_recognized(content: str) -> str | None:
    """Return the first recognized heading phrase in ``content`` or None."""
    for line in content.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        normalized = _normalize_heading(line)
        for phrase in markers.MIGRATION_HEADING_RECOGNIZERS:
            if phrase in normalized:
                return phrase
    return None


def _doc_has_migrated_marker(content: str) -> str | None:
    """Return the migration target filename iff the doc carries an EXACT migrated marker.

    Closure rule (exact marker contract):

    * The doc must contain a stripped line that is BYTE-EQUAL to one of the
      allowed ``markers.MIGRATED_MARKER_TEMPLATE.format(target=T)`` strings
      where ``T`` is one of the declared canonical filenames (core or
      conditional). No partial matches, no arbitrary targets, no extra text
      inside the marker, no malformed prefix.
    * Malformed markers (extra text, wrong target, missing closing ``-->``)
      are NEVER accepted. They do NOT silence the unresolved-migration
      finding; the doc keeps generating one until a valid marker exists.

    Returns the canonical target filename on success, ``None`` otherwise.
    """
    allowed_targets = set(markers.CORE_POLICY_FILES) | set(
        markers.CONDITIONAL_POLICY_FILES.values()
    )
    expected_markers = {
        markers.MIGRATED_MARKER_TEMPLATE.format(target=target).strip()
        for target in allowed_targets
    }
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line in expected_markers:
            for target in allowed_targets:
                if (
                    markers.MIGRATED_MARKER_TEMPLATE.format(target=target).strip()
                    == line
                ):
                    return target
    return None


def migration_candidates(workspace: Workspace) -> list[MigrationCandidate]:
    """Return every migration candidate the detector can recognize.

    A candidate is a file that:

    1. Is listed in :data:`markers.MIGRATION_CANDIDATE_PATHS` OR exists as a
       top-level ``*.md`` under :data:`markers.MIGRATION_DOCS_GLOB_DIR` (but
       NOT already inside the canonical policy directory), AND
    2. Contains a recognized heading phrase.

    A candidate is RESOLVED when it carries the exact migrated marker
    pointing at a canonical file that exists, OR when its recognized
    headings have all been removed.

    Unrelated mixed-purpose docs (no recognized heading) are NEVER
    candidates, so they never generate findings.
    """
    candidates: list[MigrationCandidate] = []
    seen_paths: set[str] = set()

    # Explicit candidate inventory.
    for path in markers.MIGRATION_CANDIDATE_PATHS:
        seen_paths.add(path)
        content = _read(workspace, path)
        if not content:
            continue
        phrase = _doc_recognized(content)
        if phrase is None:
            continue
        resolved = _is_migration_resolved(workspace, path, content)
        candidates.append(MigrationCandidate(path=path, recognized_heading=phrase, resolved=resolved))

    # Bounded one-level scan of docs/*.md (skip files already in canonical dir).
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
        phrase = _doc_recognized(content)
        if phrase is None:
            continue
        resolved = _is_migration_resolved(workspace, path, content)
        candidates.append(MigrationCandidate(path=path, recognized_heading=phrase, resolved=resolved))

    return candidates


def _is_migration_resolved(workspace: Workspace, path: str, content: str) -> bool:
    """Return True when the candidate file is reconciled against the canonical dir."""
    target = _doc_has_migrated_marker(content)
    if target and _exists(workspace, f"{markers.CANONICAL_DIR}{target}"):
        return True
    # Recognized headings removed -> doc no longer policy-like.
    return _doc_recognized(content) is None


def readiness_evidence(workspace: Workspace, stack: ProjectStack) -> list[EvidenceEntry]:
    """Return the ordered, deduplicated readiness-evidence inventory.

    Every entry the validator reads (every policy file, every signal file,
    every migration candidate, the AGENTS.md and CLAUDE.md files, and the
    opt-out source path) is captured here as an :class:`EvidenceEntry`.

    The cache hashes the SHA-256 signature of this inventory so any edit OR
    deletion of any evidence file invalidates a cached READY.
    """
    paths: list[str] = [
        markers.AGENTS_MD,
        markers.CLAUDE_MD,
    ]
    paths.extend(f"{markers.CANONICAL_DIR}{filename}" for filename in markers.CORE_POLICY_FILES)
    paths.extend(
        f"{markers.CANONICAL_DIR}{filename}"
        for filename in markers.CONDITIONAL_POLICY_FILES.values()
    )
    # Conditional-domain signal files.
    paths.extend(markers.PERF_SIGNAL_PATHS)
    paths.extend(markers.MEMORY_SIGNAL_PATHS)
    # Manifests scanned for dep substrings.
    paths.extend(_MANIFEST_FILES)
    # Migration candidates (paths are already workspace-relative).
    paths.extend(candidate.path for candidate in migration_candidates(workspace))

    seen: set[str] = set()
    entries: list[EvidenceEntry] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        if workspace.exists(path):
            content = workspace.read(path)
            entries.append(
                EvidenceEntry(
                    rel_path=path,
                    exists=True,
                    content_sha256=_sha256_hex(content),
                )
            )
        else:
            entries.append(EvidenceEntry(rel_path=path, exists=False, content_sha256=None))
    return entries


def _serialize_stack(stack: ProjectStack) -> dict[str, object]:
    """Return a canonical JSON-serializable projection of the project stack.

    Sorting every collection makes the signature stable across runs and
    Python versions.
    """
    return {
        "primary_language": stack.primary_language,
        "secondary_languages": sorted(stack.secondary_languages),
        "frameworks": sorted(stack.frameworks),
        "has_tests": stack.has_tests,
        "test_framework": stack.test_framework,
        "package_manager": stack.package_manager,
    }


def evidence_signature(workspace: Workspace, stack: ProjectStack) -> str:
    """Return a stable signature covering the full readiness-evidence inventory.

    The signature is a SHA-256 of the canonical-JSON serialization of the
    sorted :class:`EvidenceEntry` tuples plus the serialized
    :class:`ProjectStack`. Any change to any input — file edit, file
    deletion, framework added to the stack, language removed — produces a
    different signature, so a cached READY cannot stay valid across such a
    change.
    """
    entries = readiness_evidence(workspace, stack)
    payload = {
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
    "design_system_required",
    "evidence_signature",
    "memory_required",
    "migration_candidates",
    "performance_required",
    "readiness_evidence",
    "required_languages",
    "ux_required",
]
