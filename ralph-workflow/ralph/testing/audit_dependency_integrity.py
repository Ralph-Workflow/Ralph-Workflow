"""Dependency-integrity audit — a NEW dependency must be verifiable offline.

The gate answers one question: *may this change add a dependency that
nobody can verify?* A hallucinated package name — one that was never
published, or that resolves to an attacker-controlled name — is the
classic supply-chain failure this audit exists to close. The gate is
therefore strict about the two attributes a NEW package must carry:

``resolvable source``
    An index URL / registry reference (or a direct URL / VCS reference)
    that says *where* the package comes from. A bare name in
    ``dependencies = [...]`` resolves against whatever index happens to be
    configured, so it proves nothing.

``publisher``
    An owner / maintainer reference — the human or organisation that
    publishes and maintains the package. Without it, "the package exists"
    is unverifiable, because anybody can publish a package under a name
    that looks legitimate.

Missing either attribute produces a finding that names the package, names
the missing element, and carries the fix hint.

Two further rules come straight from the dependency policy:

``install script``
    A new package that introduces an install-time execution hook
    (``setup.py``/``setup.cfg``, a ``postinstall``-style TOML key, a
    custom build hook, a local ``backend-path``) is flagged: an install
    script runs arbitrary code with the developer's privileges.

``lockfile hash``
    A new lockfile entry with no artifact hash is flagged: without the
    hash the lockfile cannot pin the bytes that will be installed.

``lockfile resolution``
    A new direct dependency must resolve in the current lockfile
    (``uv.lock``). An unpinned direct package, or an absent or malformed
    current lockfile, fails closed with a missing-lock-entry finding.

DIRECT VS TRANSITIVE DEPENDENCIES

Top-level direct requirements declared in ``pyproject.toml`` require
a resolvable source, publisher evidence, and lockfile resolution.
Transitive dependencies resolved into ``uv.lock`` are governed by
lockfile reproducibility and artifact hash pinning rather than direct
publisher declarations, matching ``transitive_dependency_policy`` in the
project dependency policy.

INPUT SEAM

The gate is a pure function over two *content sets* — a mapping of
manifest path to manifest text — one for the BASELINE and one for the
CURRENT tree:

    audit_dependency_change(baseline, current) -> list[DependencyFinding]

The caller supplies both sets (the coordinator wires a git-diff based
baseline). The gate itself never shells out, never reads version control
and never touches the network, so a synthetic input exercises every rule
offline and deterministically.

Usage:
    python -m ralph.testing.audit_dependency_integrity <baseline_root> <current_root>

Exit 0 = clean, 1 = findings, 2 = a root was not found.
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.testing._audit_baseline import head_tree
from ralph.testing._audit_content_set import read_manifests

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "CATEGORY_INSTALL_SCRIPT",
    "CATEGORY_MISSING_HASH",
    "CATEGORY_MISSING_LOCK_ENTRY",
    "CATEGORY_MISSING_PUBLISHER",
    "CATEGORY_MISSING_SOURCE",
    "DependencyFinding",
    "audit_dependency_change",
    "audit_dependency_roots",
    "main",
]

#: The package-manager manifest and its lockfile. Both are TOML, both are
#: parsed with the stdlib TOML reader, and both are the only inputs the
#: gate reads.
PYPROJECT_MANIFEST = "pyproject.toml"
LOCKFILE_MANIFEST = "uv.lock"
MANIFEST_NAMES: frozenset[str] = frozenset({PYPROJECT_MANIFEST, LOCKFILE_MANIFEST})

#: Files whose mere PRESENCE in a change means an install script entered
#: the project: ``setup.py`` executes at install time, ``setup.cfg`` can
#: declare the commands that do.
SETUP_SCRIPT_FILES: frozenset[str] = frozenset({"setup.py", "setup.cfg"})

#: Finding categories. They are stable strings: the coordinator's verify
#: step and any waiver record key on them.
CATEGORY_MISSING_SOURCE = "missing-source"
CATEGORY_MISSING_PUBLISHER = "missing-publisher"
CATEGORY_INSTALL_SCRIPT = "install-script"
CATEGORY_MISSING_HASH = "missing-hash"
CATEGORY_MISSING_LOCK_ENTRY = "missing-lock-entry"

#: Manifest sections that declare dependencies. A section value that is a
#: list is read directly; a section value that is a table is read group by
#: group (``[project.optional-dependencies]``, ``[dependency-groups]``).
DEPENDENCY_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("project", "dependencies"),
    ("project", "optional-dependencies"),
    ("dependency-groups",),
    ("build-system", "requires"),
)

#: TOML keys that declare an install-time or build-time execution hook.
#: Matched case-insensitively against the LAST segment of every key path
#: in the manifest, so ``[tool.uv] post-install`` and
#: ``[tool.hatch.build.hooks.custom]`` are both covered.
INSTALL_HOOK_KEYS: frozenset[str] = frozenset(
    {
        "build-hook",
        "install-hook",
        "install-script",
        "post-build",
        "post-install",
        "postinstall",
        "post_build",
        "pre-install",
        "preinstall",
        "pre_install",
        "setup-hook",
    }
)

#: Manifest tables whose presence means arbitrary commands run at build
#: or install time.
INSTALL_HOOK_TABLES: frozenset[str] = frozenset(
    {
        "tool.hatch.build.hooks.custom",
        "tool.uv.build-hook",
        "tool.uv.scripts",
    }
)

#: ``[build-system] backend-path`` makes the build backend a local file:
#: project code that runs at build time.
BUILD_BACKEND_PATH_KEY = "build-system.backend-path"

#: Evidence table that records the publisher / owner of a dependency.
EVIDENCE_TABLE_PREFIX = "tool.ralph.dependency-evidence"

#: Keys inside the evidence table that carry the publisher reference.
PUBLISHER_EVIDENCE_KEYS: frozenset[str] = frozenset({"publisher", "maintainer", "owner"})

#: Inline comment keys that carry the publisher reference.
PUBLISHER_COMMENT_PATTERN = re.compile(
    r"(?:publisher|maintainer|owner)\s*[:=]\s*(?P<value>[^\s#]+)",
    re.IGNORECASE,
)

#: A PEP 508 requirement: name, optional extras, then the rest (version
#: specifier, direct URL, environment marker).
REQUIREMENT_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"(?P<rest>.*)$",
    re.DOTALL,
)

#: Lockfile ``source`` keys that resolve to a REGISTRY or a remote
#: artifact. Local sources (``editable``, ``path``, ``directory``) do not
#: prove the package came from a published registry.
RESOLVABLE_SOURCE_KEYS: frozenset[str] = frozenset({"registry", "url", "git"})

#: Source-control hosts whose URL path names the owning organisation.
#: A dependency pinned to one of these hosts carries its publisher in the
#: URL itself, so the URL doubles as the publisher reference.
SOURCE_CONTROL_HOSTS: frozenset[str] = frozenset(
    {
        "bitbucket.org",
        "codeberg.org",
        "github.com",
        "gitlab.com",
    }
)

#: Minimum number of ``/``-separated segments a source-control URL must
#: carry before its first segment can be read as the owning organisation.
MIN_SOURCE_CONTROL_URL_SEGMENTS = 2


#: Accepted root-argument count for the CLI: baseline plus current.
REQUIRED_CLI_ROOT_ARGUMENTS = 2


@dataclass(frozen=True)
class DependencyFinding:
    """One dependency-integrity finding."""

    file_path: str
    line: int
    category: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


@dataclass(frozen=True)
class _Requirement:
    """One parsed PEP 508 requirement."""

    name: str
    raw: str
    url: str | None
    extras: tuple[str, ...]


def _regex_group_str(match: re.Match[str], group: str) -> str:
    """Return a named regex group as ``str`` (never ``Any``)."""
    value = match.group(group)
    if not isinstance(value, str):
        msg = "expected regex capture group to be str"
        raise TypeError(msg)
    return value


def _regex_optional_group_str(match: re.Match[str], group: str) -> str | None:
    """Return an optional named regex group, or None when it did not match."""
    value: str | None = match.group(group)
    return value


def _parse_toml(text: str) -> dict[str, object] | None:
    """Parse TOML text into a plain mapping, or return None when invalid."""
    try:
        parsed_obj: object = tomllib.loads(text)
    except (OSError, ValueError):
        return None
    if not isinstance(parsed_obj, dict):
        return None
    return cast("dict[str, object]", parsed_obj)


def _normalize_name(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(raw: str) -> _Requirement | None:
    """Parse one PEP 508 requirement string, or return None when unusable."""
    text = raw.strip()
    if not text:
        return None
    match = REQUIREMENT_PATTERN.match(text)
    if match is None:
        return None
    extras_raw = _regex_optional_group_str(match, "extras") or ""
    rest = _regex_group_str(match, "rest")
    url: str | None = None
    if "@" in rest:
        url = rest.split("@", 1)[1].split(";", 1)[0].strip() or None
    return _Requirement(
        name=_normalize_name(_regex_group_str(match, "name")),
        raw=raw,
        url=url,
        extras=tuple(item.strip() for item in extras_raw.split(",") if item.strip()),
    )


def _nested_table(data: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    """Return the nested mapping at *keys*, or an empty mapping."""
    current: Mapping[str, object] = data
    for key in keys:
        value = current.get(key)
        if not isinstance(value, Mapping):
            return {}
        current = cast("Mapping[str, object]", value)
    return current


def _collect_requirement_strings(data: Mapping[str, object]) -> list[str]:
    """Return every raw requirement string declared by the manifest."""
    requirements: list[str] = []
    for path in DEPENDENCY_SECTIONS:
        node: object = data
        for key in path:
            if not isinstance(node, Mapping):
                node = None
                break
            node = cast("Mapping[str, object]", node).get(key)
        if isinstance(node, list):
            requirements.extend(item for item in node if isinstance(item, str))
        elif isinstance(node, Mapping):
            for value in cast("Mapping[str, object]", node).values():
                if isinstance(value, list):
                    requirements.extend(item for item in value if isinstance(item, str))
    return requirements


def _iter_key_paths(data: Mapping[str, object], prefix: str = "") -> Iterable[tuple[str, str]]:
    """Yield ``(dotted key path, leaf key)`` for every key in the manifest.

    Table paths are yielded too, so a hook declared as a whole table (for
    example ``[tool.hatch.build.hooks.custom]``) is visible to the
    install-script rule and not only its leaf values.
    """
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        yield path, key
        if isinstance(value, Mapping):
            yield from _iter_key_paths(cast("Mapping[str, object]", value), path)


def _find_line(text: str, needle: str) -> int:
    """Return the 1-based line number of the first line containing *needle*."""
    stripped = needle.strip()
    if not stripped:
        return 0
    for index, line in enumerate(text.splitlines(), start=1):
        if stripped in line:
            return index
    return 0


def _comment_segment(line: str) -> str:
    """Return the trailing comment of *line* (everything after the first ``#``)."""
    marker = line.find("#")
    if marker < 0:
        return ""
    return line[marker:]


def _publisher_from_comment(line: str) -> str | None:
    """Return the publisher reference carried by an inline comment, if any."""
    match = PUBLISHER_COMMENT_PATTERN.search(_comment_segment(line))
    if match is None:
        return None
    return _regex_group_str(match, "value")


def _publisher_from_url(url: str) -> str | None:
    """Return the owning organisation named by a source-control URL."""
    without_scheme = url.split("://", 1)[-1]
    host_and_path = without_scheme.split("/", 1)
    host = host_and_path[0].split("@")[-1].split(":")[0].lower()
    if host not in SOURCE_CONTROL_HOSTS:
        return None
    if len(host_and_path) < MIN_SOURCE_CONTROL_URL_SEGMENTS:
        return None
    segments = [segment for segment in host_and_path[1].split("/") if segment]
    if not segments:
        return None
    return segments[0]


def _lock_packages(text: str) -> dict[tuple[str, str], Mapping[str, object]]:
    """Return ``{(name, version): package entry}`` for a uv lockfile."""
    data = _parse_toml(text)
    if data is None:
        return {}
    raw_packages = data.get("package")
    if not isinstance(raw_packages, list):
        return {}
    packages: dict[tuple[str, str], Mapping[str, object]] = {}
    for raw_entry in raw_packages:
        if not isinstance(raw_entry, Mapping):
            continue
        entry = cast("Mapping[str, object]", raw_entry)
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        packages[(name, version)] = entry
    return packages


def _lock_source(entry: Mapping[str, object]) -> str | None:
    """Return the resolvable source of a lockfile entry, or None."""
    source = entry.get("source")
    if not isinstance(source, Mapping):
        return None
    for key in RESOLVABLE_SOURCE_KEYS:
        value = cast("Mapping[str, object]", source).get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}"
    return None


def _lock_entry_has_hash(entry: Mapping[str, object]) -> bool:
    """Return True when a lockfile entry pins at least one artifact hash."""
    sdist = entry.get("sdist")
    if isinstance(sdist, Mapping):
        value = cast("Mapping[str, object]", sdist).get("hash")
        if isinstance(value, str) and value:
            return True
    wheels = entry.get("wheels")
    if isinstance(wheels, list):
        for wheel in wheels:
            if not isinstance(wheel, Mapping):
                continue
            value = cast("Mapping[str, object]", wheel).get("hash")
            if isinstance(value, str) and value:
                return True
    return False


def _lock_entry_is_downloaded(entry: Mapping[str, object]) -> bool:
    """Return True when the entry downloads an artifact (so it needs a hash)."""
    return _lock_source(entry) is not None


def _index_sources(data: Mapping[str, object]) -> dict[str, str]:
    """Return ``{index name: url}`` declared by ``[tool.uv.index]``."""
    indexes: dict[str, str] = {}
    uv_table = _nested_table(data, "tool", "uv")
    raw_index = uv_table.get("index")
    if isinstance(raw_index, list):
        for item in cast("list[object]", raw_index):
            if not isinstance(item, Mapping):
                continue
            entry = cast("Mapping[str, object]", item)
            name = entry.get("name")
            url = entry.get("url")
            if isinstance(name, str) and isinstance(url, str) and name and url:
                indexes[name] = url
    return indexes


def _tool_source(data: Mapping[str, object], package: str) -> str | None:
    """Return the source declared for *package* by ``[tool.uv.sources]``."""
    sources = _nested_table(data, "tool", "uv", "sources")
    entry = sources.get(package)
    if not isinstance(entry, Mapping):
        return None
    for key in (*RESOLVABLE_SOURCE_KEYS, "index"):
        value = cast("Mapping[str, object]", entry).get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}"
    return None


def _evidence_publisher(data: Mapping[str, object], package: str) -> str | None:
    """Return the publisher recorded by the dependency-evidence table."""
    table = _nested_table(data, "tool", "ralph", "dependency-evidence", package)
    for key in sorted(PUBLISHER_EVIDENCE_KEYS):
        value = table.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _evidence_source(data: Mapping[str, object], package: str) -> str | None:
    """Return the source recorded by the dependency-evidence table."""
    table = _nested_table(data, "tool", "ralph", "dependency-evidence", package)
    for key in ("registry", "source", "url"):
        value = table.get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}"
    return None


def _new_requirements(
    baseline_data: Mapping[str, object],
    current_data: Mapping[str, object],
) -> list[_Requirement]:
    """Return the requirements declared now that the baseline did not declare."""
    baseline_names = {
        requirement.name
        for requirement in (
            _parse_requirement(raw) for raw in _collect_requirement_strings(baseline_data)
        )
        if requirement is not None
    }
    new_requirements: list[_Requirement] = []
    seen: set[str] = set()
    for raw in _collect_requirement_strings(current_data):
        requirement = _parse_requirement(raw)
        if requirement is None or requirement.name in baseline_names:
            continue
        if requirement.name in seen:
            continue
        seen.add(requirement.name)
        new_requirements.append(requirement)
    return new_requirements


def _resolve_source(
    requirement: _Requirement,
    current_data: Mapping[str, object],
    lock_sources: Mapping[tuple[str, str], str | None],
    lock_names: Mapping[str, tuple[str, ...]],
) -> str | None:
    """Return the resolvable source of *requirement*, or None when absent."""
    if requirement.url:
        return f"url={requirement.url}"
    source = _tool_source(current_data, requirement.name)
    if source is not None:
        return source
    evidence_source = _evidence_source(current_data, requirement.name)
    if evidence_source is not None:
        return evidence_source
    versions = lock_names.get(requirement.name, ())
    for version in versions:
        lock_source = lock_sources.get((requirement.name, version))
        if lock_source is not None:
            return lock_source
    indexes = _index_sources(current_data)
    for extra in requirement.extras:
        if extra in indexes:
            return f"index={indexes[extra]}"
    return None


def _resolve_publisher(
    requirement: _Requirement,
    current_data: Mapping[str, object],
    manifest_text: str,
) -> str | None:
    """Return the publisher reference of *requirement*, or None when absent."""
    publisher = _evidence_publisher(current_data, requirement.name)
    if publisher is not None:
        return publisher
    if requirement.url:
        owner = _publisher_from_url(requirement.url)
        if owner is not None:
            return owner
    line_number = _find_line(manifest_text, requirement.raw)
    if line_number:
        lines = manifest_text.splitlines()
        comment_publisher = _publisher_from_comment(lines[line_number - 1])
        if comment_publisher is not None:
            return comment_publisher
    return None


def _new_install_signals(
    baseline_data: Mapping[str, object],
    current_data: Mapping[str, object],
    baseline_setup_files: frozenset[str],
    current_setup_files: frozenset[str],
) -> list[str]:
    """Return the install-time execution hooks the change introduces."""
    signals: list[str] = []
    signals.extend(
        f"{name} added (install script)"
        for name in sorted(current_setup_files - baseline_setup_files)
        if name in SETUP_SCRIPT_FILES
    )
    baseline_paths = {path for path, _leaf in _iter_key_paths(baseline_data)}
    current_paths = {path for path, _leaf in _iter_key_paths(current_data)}
    signals.extend(
        f"[{path}] install hook added"
        for path in sorted(current_paths - baseline_paths)
        if path.rsplit(".", 1)[-1].lower() in INSTALL_HOOK_KEYS
    )
    signals.extend(
        f"[{table}] build/install hook added"
        for table in sorted(INSTALL_HOOK_TABLES)
        if table in current_paths and table not in baseline_paths
    )
    if BUILD_BACKEND_PATH_KEY in current_paths and BUILD_BACKEND_PATH_KEY not in baseline_paths:
        signals.append("[build-system] backend-path added (local build backend)")
    return signals


def audit_dependency_change(
    baseline: Mapping[str, str],
    current: Mapping[str, str],
) -> list[DependencyFinding]:
    """Audit a dependency change and return every finding.

    Args:
        baseline: Content set of the BASELINE tree. Only the manifest names
            in :data:`MANIFEST_NAMES` are read.
        current: Content set of the CURRENT tree, same shape.

    Returns:
        The findings, ordered by manifest, then by line, then by category.
        An empty list means the change is clean.
    """
    findings: list[DependencyFinding] = []
    baseline_pyproject = baseline.get(PYPROJECT_MANIFEST)
    current_pyproject = current.get(PYPROJECT_MANIFEST)
    if current_pyproject is None:
        return findings

    current_data = _parse_toml(current_pyproject) or {}
    baseline_data = _parse_toml(baseline_pyproject) if baseline_pyproject else None
    if baseline_data is None:
        baseline_data = {}

    baseline_lock = baseline.get(LOCKFILE_MANIFEST)
    current_lock = current.get(LOCKFILE_MANIFEST)
    baseline_packages = _lock_packages(baseline_lock) if baseline_lock else {}
    current_packages = _lock_packages(current_lock) if current_lock else {}

    lock_sources: dict[tuple[str, str], str | None] = {}
    lock_names: dict[str, tuple[str, ...]] = {}
    for (name, version), entry in current_packages.items():
        lock_sources[(name, version)] = _lock_source(entry)
        normalized = _normalize_name(name)
        lock_names[normalized] = (*lock_names.get(normalized, ()), version)

    new_requirements = _new_requirements(baseline_data, current_data)
    new_names = [requirement.name for requirement in new_requirements]
    package_label = ", ".join(new_names) if new_names else "(no new package)"

    for requirement in new_requirements:
        source = _resolve_source(requirement, current_data, lock_sources, lock_names)
        if source is None:
            findings.append(
                DependencyFinding(
                    file_path=PYPROJECT_MANIFEST,
                    line=_find_line(current_pyproject, requirement.raw),
                    category=CATEGORY_MISSING_SOURCE,
                    detail=(
                        f"new package '{requirement.name}' has no resolvable source: "
                        "the change declares no index/registry reference and no direct URL. "
                        "fix: add an explicit index or registry reference (or a direct URL "
                        "requirement) so the package resolves to a known registry"
                    ),
                )
            )
        publisher = _resolve_publisher(requirement, current_data, current_pyproject)
        if publisher is None:
            findings.append(
                DependencyFinding(
                    file_path=PYPROJECT_MANIFEST,
                    line=_find_line(current_pyproject, requirement.raw),
                    category=CATEGORY_MISSING_PUBLISHER,
                    detail=(
                        f"new package '{requirement.name}' has no publisher reference: "
                        "the change names no owner or maintainer for it. "
                        "fix: record the publisher (inline '# publisher: <name>' comment on the "
                        "requirement, a [tool.ralph.dependency-evidence.<package>] table, or a "
                        "source-control URL that names the owner) so the package can be verified "
                        "against the registry"
                    ),
                )
            )
        # Every newly added direct dependency must resolve in the current
        # lockfile. If uv.lock is absent, malformed, or lacks the package,
        # lock_names will not contain it, failing closed.
        if requirement.name not in lock_names:
            findings.append(
                DependencyFinding(
                    file_path=PYPROJECT_MANIFEST,
                    line=_find_line(current_pyproject, requirement.raw),
                    category=CATEGORY_MISSING_LOCK_ENTRY,
                    detail=(
                        f"new package '{requirement.name}' has no lockfile entry: the "
                        "lockfile does not resolve it, so the registry existence of the "
                        "package is unproven. fix: re-lock the dependency set so the "
                        "lockfile pins the package and its hashes"
                    ),
                )
            )

    baseline_setup_files = frozenset(baseline) & SETUP_SCRIPT_FILES
    current_setup_files = frozenset(current) & SETUP_SCRIPT_FILES
    for signal in _new_install_signals(
        baseline_data, current_data, baseline_setup_files, current_setup_files
    ):
        findings.extend(
            [
                DependencyFinding(
                    file_path=PYPROJECT_MANIFEST,
                    line=0,
                    category=CATEGORY_INSTALL_SCRIPT,
                    detail=(
                        f"the change introduces an install script ({signal}) for "
                        f"{package_label}. fix: remove the install-time execution hook, or "
                        "record a documented waiver naming the hook, its owner and a review date"
                    ),
                )
            ]
        )

    # Check newly added lockfile entries (both direct and transitive).
    # Per transitive_dependency_policy, transitive packages in uv.lock
    # are governed by artifact hash pinning, not by direct publisher declarations.
    for key in sorted(set(current_packages) - set(baseline_packages)):
        entry = current_packages[key]
        if not _lock_entry_is_downloaded(entry):
            continue
        if _lock_entry_has_hash(entry):
            continue
        name, version = key
        findings.append(
            DependencyFinding(
                file_path=LOCKFILE_MANIFEST,
                line=0,
                category=CATEGORY_MISSING_HASH,
                detail=(
                    f"new lockfile entry '{name}=={version}' pins no artifact hash: the "
                    "lockfile cannot guarantee which bytes are installed. fix: re-lock the "
                    "dependency set so every artifact carries its sha256 hash"
                ),
            )
        )

    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def audit_dependency_roots(baseline_root: Path, current_root: Path) -> list[DependencyFinding]:
    """Audit the dependency change between two repository roots."""
    baseline = read_manifests(baseline_root, MANIFEST_NAMES)
    current = read_manifests(current_root, MANIFEST_NAMES)
    baseline_files = read_manifests(baseline_root, SETUP_SCRIPT_FILES)
    current_files = read_manifests(current_root, SETUP_SCRIPT_FILES)
    merged_baseline = {**baseline, **baseline_files}
    merged_current = {**current, **current_files}
    return audit_dependency_change(merged_baseline, merged_current)


def main(argv: list[str] | None = None) -> int:
    """Run the dependency-integrity audit and return an exit code.

    Exit code 0: no findings. Exit code 1: findings. Exit code 2: a root
    was not found.
    """
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        current_root = Path(__file__).resolve().parents[2]
        resolved_baseline = head_tree(current_root)
        if resolved_baseline is None:
            print(
                "Error: no committed baseline is available for this package; "
                "pass <baseline_root> <current_root> explicitly.",
                file=sys.stderr,
            )
            return 2
        args = [str(resolved_baseline), str(current_root)]
    if len(args) != REQUIRED_CLI_ROOT_ARGUMENTS:
        print(
            "Usage: python -m ralph.testing.audit_dependency_integrity "
            "<baseline_root> <current_root>",
            file=sys.stderr,
        )
        return 2
    baseline_root = Path(args[0])
    current_root = Path(args[1])
    for root in (baseline_root, current_root):
        if not root.is_dir():
            print(f"Error: audit root not found: {root}", file=sys.stderr)
            return 2

    print(f"Auditing dependency integrity: baseline={baseline_root} current={current_root}")
    print()

    findings = audit_dependency_roots(baseline_root, current_root)

    if findings:
        print(f"DEPENDENCY INTEGRITY VIOLATIONS: {len(findings)} finding(s)")
        print("=" * 72)
        for finding in findings:
            print(f"  {finding}")
        print()
        print(
            "Every NEW dependency must name a resolvable source and a publisher, must be "
            "locked with artifact hashes, and must not introduce an install script. Fix "
            "the dependency change, not the audit."
        )
        return 1

    print("No dependency-integrity violations found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
