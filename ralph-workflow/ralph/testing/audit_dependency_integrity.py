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

PYPROJECT_MANIFEST = "pyproject.toml"
LOCKFILE_MANIFEST = "uv.lock"
MANIFEST_NAMES: frozenset[str] = frozenset({PYPROJECT_MANIFEST, LOCKFILE_MANIFEST})

SETUP_SCRIPT_FILES: frozenset[str] = frozenset({"setup.py", "setup.cfg"})

CATEGORY_MISSING_SOURCE = "missing-source"
CATEGORY_MISSING_PUBLISHER = "missing-publisher"
CATEGORY_INSTALL_SCRIPT = "install-script"
CATEGORY_MISSING_HASH = "missing-hash"
CATEGORY_MISSING_LOCK_ENTRY = "missing-lock-entry"

DEPENDENCY_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("project", "dependencies"),
    ("project", "optional-dependencies"),
    ("dependency-groups",),
    ("build-system", "requires"),
)

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

INSTALL_HOOK_TABLES: frozenset[str] = frozenset(
    {
        "tool.hatch.build.hooks.custom",
        "tool.uv.build-hook",
        "tool.uv.scripts",
    }
)

BUILD_BACKEND_PATH_KEY = "build-system.backend-path"

PUBLISHER_EVIDENCE_KEYS: frozenset[str] = frozenset({"publisher", "maintainer", "owner"})

REQUIREMENT_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"(?P<rest>.*)$",
    re.DOTALL,
)

RESOLVABLE_SOURCE_KEYS: frozenset[str] = frozenset({"registry", "url", "git"})

LOCAL_SOURCE_KEYS: frozenset[str] = frozenset({"directory", "editable", "virtual"})

SOURCE_CONTROL_HOSTS: frozenset[str] = frozenset(
    {
        "bitbucket.org",
        "codeberg.org",
        "github.com",
        "gitlab.com",
    }
)

MIN_SOURCE_CONTROL_URL_SEGMENTS = 2


REQUIRED_CLI_ROOT_ARGUMENTS = 2


@dataclass(frozen=True)
class DependencyFinding:
    file_path: str
    line: int
    category: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


@dataclass(frozen=True)
class _Requirement:
    name: str
    raw: str
    url: str | None
    extras: tuple[str, ...]


def _regex_group_str(match: re.Match[str], group: str) -> str:
    value = match.group(group)
    if not isinstance(value, str):
        msg = "expected regex capture group to be str"
        raise TypeError(msg)
    return value


def _regex_optional_group_str(match: re.Match[str], group: str) -> str | None:
    value: str | None = match.group(group)
    return value


def _parse_toml(text: str) -> dict[str, object] | None:
    try:
        parsed_obj: object = tomllib.loads(text)
    except (OSError, ValueError):
        return None
    if not isinstance(parsed_obj, dict):
        return None
    return cast("dict[str, object]", parsed_obj)


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(raw: str) -> _Requirement | None:
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
    current: Mapping[str, object] = data
    for key in keys:
        value = current.get(key)
        if not isinstance(value, Mapping):
            return {}
        current = cast("Mapping[str, object]", value)
    return current


def _collect_requirement_strings(data: Mapping[str, object]) -> list[str]:
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
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        yield path, key
        if isinstance(value, Mapping):
            yield from _iter_key_paths(cast("Mapping[str, object]", value), path)


def _find_line(text: str, needle: str) -> int:
    stripped = needle.strip()
    if not stripped:
        return 0
    for index, line in enumerate(text.splitlines(), start=1):
        if stripped in line:
            return index
    return 0


def _publisher_from_url(url: str) -> str | None:
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
    source = entry.get("source")
    if not isinstance(source, Mapping):
        return None
    for key in RESOLVABLE_SOURCE_KEYS:
        value = cast("Mapping[str, object]", source).get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}"
    return None


def _lock_entry_has_hash(entry: Mapping[str, object]) -> bool:
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


def _lock_entry_is_local(entry: Mapping[str, object]) -> bool:
    source = entry.get("source")
    if not isinstance(source, Mapping):
        return False
    for key in LOCAL_SOURCE_KEYS:
        value = cast("Mapping[str, object]", source).get(key)
        if isinstance(value, str) and value:
            return True
    return False


def _lock_entry_publisher(
    entry: Mapping[str, object],
    current_data: Mapping[str, object],
    name: str,
) -> str | None:
    publisher = _evidence_publisher(current_data, _normalize_name(name))
    if publisher is not None:
        return publisher
    source = entry.get("source")
    if isinstance(source, Mapping):
        git_url = cast("Mapping[str, object]", source).get("git")
        if isinstance(git_url, str) and git_url:
            return _publisher_from_url(git_url)
    return None


def _index_sources(data: Mapping[str, object]) -> dict[str, str]:
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
    table = _nested_table(data, "tool", "ralph", "dependency-evidence", package)
    for key in sorted(PUBLISHER_EVIDENCE_KEYS):
        value = table.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _evidence_source(data: Mapping[str, object], package: str) -> str | None:
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
) -> str | None:
    publisher = _evidence_publisher(current_data, requirement.name)
    if publisher is not None:
        return publisher
    if requirement.url:
        owner = _publisher_from_url(requirement.url)
        if owner is not None:
            return owner
    return None


def _new_install_signals(
    baseline_data: Mapping[str, object],
    current_data: Mapping[str, object],
    baseline_setup_files: frozenset[str],
    current_setup_files: frozenset[str],
) -> list[str]:
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
        publisher = _resolve_publisher(requirement, current_data)
        if publisher is None:
            findings.append(
                DependencyFinding(
                    file_path=PYPROJECT_MANIFEST,
                    line=_find_line(current_pyproject, requirement.raw),
                    category=CATEGORY_MISSING_PUBLISHER,
                    detail=(
                        f"new package '{requirement.name}' has no publisher reference: "
                        "the change names no owner or maintainer for it. "
                        "fix: record the publisher in a "
                        "[tool.ralph.dependency-evidence.<package>] table (publisher, "
                        "maintainer or owner key) or in a source-control URL that names "
                        "the owner, so the package can be verified against the registry"
                    ),
                )
            )
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

    new_direct_names = {requirement.name for requirement in new_requirements}
    lock_text = current_lock or ""
    for key in sorted(set(current_packages) - set(baseline_packages)):
        entry = current_packages[key]
        name, version = key
        if _lock_entry_is_local(entry):
            continue
        line = _find_line(lock_text, f'"{name}"')
        if _lock_source(entry) is None:
            findings.append(
                DependencyFinding(
                    file_path=LOCKFILE_MANIFEST,
                    line=line,
                    category=CATEGORY_MISSING_SOURCE,
                    detail=(
                        f"new lockfile entry '{name}=={version}' names no downloaded "
                        "source: it is not a registry, URL or git source, so its "
                        "provenance is unverifiable. fix: re-lock the dependency set "
                        "so the entry pins a registry, URL or git source with "
                        "artifact hashes"
                    ),
                )
            )
            continue
        if not _lock_entry_has_hash(entry):
            findings.append(
                DependencyFinding(
                    file_path=LOCKFILE_MANIFEST,
                    line=line,
                    category=CATEGORY_MISSING_HASH,
                    detail=(
                        f"new lockfile entry '{name}=={version}' pins no artifact hash: "
                        "the lockfile cannot guarantee which bytes are installed. fix: "
                        "re-lock the dependency set so every artifact carries its "
                        "sha256 hash"
                    ),
                )
            )
        if _normalize_name(name) in new_direct_names:
            continue
        if _lock_entry_publisher(entry, current_data, name) is None:
            findings.append(
                DependencyFinding(
                    file_path=LOCKFILE_MANIFEST,
                    line=line,
                    category=CATEGORY_MISSING_PUBLISHER,
                    detail=(
                        f"new lockfile entry '{name}=={version}' has no publisher "
                        "reference: the change names no owner or maintainer for it. "
                        "fix: record the publisher in a "
                        "[tool.ralph.dependency-evidence.<package>] table (publisher, "
                        "maintainer or owner key) or lock it from a source-control "
                        "URL that names the owner, so the package can be verified "
                        "against the registry"
                    ),
                )
            )

    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def audit_dependency_roots(baseline_root: Path, current_root: Path) -> list[DependencyFinding]:
    baseline = read_manifests(baseline_root, MANIFEST_NAMES)
    current = read_manifests(current_root, MANIFEST_NAMES)
    baseline_files = read_manifests(baseline_root, SETUP_SCRIPT_FILES)
    current_files = read_manifests(current_root, SETUP_SCRIPT_FILES)
    merged_baseline = {**baseline, **baseline_files}
    merged_current = {**current, **current_files}
    return audit_dependency_change(merged_baseline, merged_current)


def main(argv: list[str] | None = None) -> int:
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
