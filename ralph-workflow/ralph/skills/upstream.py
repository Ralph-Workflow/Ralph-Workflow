from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from ralph.checked_accessors import as_mapping, as_str_dict, optional_str
from ralph.skills._agent_paths import (
    canonical_agent_skill_root,
    sibling_agent_skill_roots,
)

UPSTREAM_REPO = "obra/superpowers"

RELEASES_API_URL = "https://api.github.com/repos/obra/superpowers/releases/latest"

ARCHIVE_URL_TEMPLATE = "https://codeload.github.com/obra/superpowers/tar.gz/refs/tags/{tag}"

UNIVERSAL_SKILL_NAMES: tuple[str, ...] = (
    "test-driven-development",
    "systematic-debugging",
    "verification-before-completion",
)

MANAGED_MARKER_NAME = ".ralph-managed.json"

MANAGED_BY = "ralph-universal"

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024

MAX_FILE_BYTES = 1024 * 1024

ALLOWED_FILE_SUFFIXES: frozenset[str] = frozenset(
    {".json", ".md", ".py", ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml"}
)

REQUEST_TIMEOUT_SECONDS = 5.0

ATTEMPT_WINDOW = timedelta(hours=24)

CACHE_FILE_NAME = "skill-updates.json"

STATE_FILE_NAME = "ralph-workflow-universal-skills.json"

type HttpResponse = tuple[int, bytes]

type HttpClient = Callable[[str, float], HttpResponse]

HTTP_OK = 200

_ATTEMPT_LOCK = threading.Lock()

_REFERENCE_PATTERN = re.compile(r"`([^`\n]+)`|\]\(([^)\n]+)\)")

_FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9]{1,8}$")

_TAG_NUMBER_PATTERN = re.compile(r"\d+")


class _ArchiveRejectedError(Exception):
    pass


@dataclass(frozen=True)
class _SkillPayload:
    files: dict[str, tuple[bytes, int]]


@dataclass(frozen=True)
class UpdateOutcome:
    status: str
    attempted: bool = False
    tag: str | None = None
    installed: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    error: str | None = None


def maybe_update_universal_skills(
    *, enabled: bool, now: datetime, http: HttpClient
) -> UpdateOutcome:
    try:
        return _run_update(enabled=enabled, now=now, http=http)
    except Exception:
        return UpdateOutcome(
            status="failed",
            error="unexpected-error",
            messages=("the universal skill update failed unexpectedly",),
        )


def default_http_client() -> HttpClient:

    def _get(url: str, timeout: float) -> HttpResponse:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        return (response.status_code, response.content)

    return _get


def canonical_skills_root() -> Path:
    return canonical_agent_skill_root().resolve()


def sibling_skills_roots() -> tuple[Path, ...]:
    return tuple(entry.resolve() for entry in sibling_agent_skill_roots())


def attempt_cache_path() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "ralph-workflow" / CACHE_FILE_NAME


def universal_state_path() -> Path:
    return Path.home() / ".config" / STATE_FILE_NAME


def _run_update(*, enabled: bool, now: datetime, http: HttpClient) -> UpdateOutcome:
    if not enabled:
        return UpdateOutcome(status="disabled")
    instant = _utc(now)
    if not _reserve_attempt(attempt_cache_path(), instant):
        return UpdateOutcome(
            status="skipped",
            messages=("the universal skill update was attempted within the last 24 h",),
        )
    tag, error = _fetch_release_tag(http)
    if tag is None:
        return UpdateOutcome(
            status="failed",
            attempted=True,
            error=error,
            messages=("the upstream release could not be read",),
        )
    if not _needs_update(tag, _read_state()):
        return UpdateOutcome(
            status="up-to-date",
            attempted=True,
            tag=tag,
            messages=(f"the universal skills are up to date ({tag})",),
        )
    payload, error = _load_release_payload(http, tag)
    if payload is None:
        return UpdateOutcome(
            status="failed",
            attempted=True,
            tag=tag,
            error=error,
            messages=("the upstream skill archive could not be installed",),
        )
    return _install_release(tag, payload)


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _reserve_attempt(cache_path: Path, now: datetime) -> bool:
    with _ATTEMPT_LOCK:
        previous = _read_attempt(cache_path)
        if previous is not None and (now - previous) < ATTEMPT_WINDOW:
            return False
        _write_json_atomic(cache_path, {"last_attempt": now.isoformat()})
        return True


def _read_attempt(cache_path: Path) -> datetime | None:
    document = _read_json_mapping(cache_path)
    if document is None:
        return None
    stamp = optional_str(document.get("last_attempt"), field="last_attempt")
    if stamp is None:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_json_mapping(path: Path) -> Mapping[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload: object = json.loads(raw)
    except ValueError:
        return None
    try:
        return as_mapping(payload, field=path.name)
    except TypeError:
        return None


def _mapping(value: object) -> Mapping[str, object]:
    try:
        return as_mapping(value)
    except TypeError:
        return {}


def _string_map(value: object) -> dict[str, str]:
    try:
        return as_str_dict(value)
    except (TypeError, ValueError):
        return {}


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _write_json_atomic(path: Path, document: Mapping[str, object]) -> None:
    _ensure_dir(path.parent)
    payload = json.dumps(document, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp"
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _fetch_release_tag(http: HttpClient) -> tuple[str | None, str | None]:
    try:
        status, body = http(RELEASES_API_URL, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        return (None, f"release-api-error:{type(exc).__name__}")
    if status != HTTP_OK:
        return (None, f"release-api-status-{status}")
    try:
        payload: object = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return (None, "release-api-malformed")
    tag = optional_str(_mapping(payload).get("tag_name"), field="tag_name")
    if tag is None:
        return (None, "release-api-malformed")
    return (tag, None)


def _load_release_payload(
    http: HttpClient, tag: str
) -> tuple[dict[str, _SkillPayload] | None, str | None]:
    archive_url = ARCHIVE_URL_TEMPLATE.format(tag=tag)
    try:
        status, body = http(archive_url, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        return (None, f"archive-error:{type(exc).__name__}")
    if status != HTTP_OK:
        return (None, f"archive-status-{status}")
    if len(body) > MAX_ARCHIVE_BYTES:
        return (None, "archive-too-large")
    try:
        payload = _extract_archive(body)
    except _ArchiveRejectedError as exc:
        return (None, str(exc))
    return (payload, None)


def _extract_archive(body: bytes) -> dict[str, _SkillPayload]:
    buckets: dict[str, list[tuple[str, tarfile.TarInfo]]] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for member in archive.getmembers():
                located = _locate_member(member)
                if located is None:
                    continue
                skill, relative = located
                buckets.setdefault(skill, []).append((relative, member))
            payloads: dict[str, _SkillPayload] = {}
            for skill, entries in buckets.items():
                payloads[skill] = _build_payload(archive, entries)
    except tarfile.TarError as exc:
        raise _ArchiveRejectedError(f"the archive could not be read: {type(exc).__name__}") from exc
    if not payloads:
        raise _ArchiveRejectedError("the archive carries no universal skill directory")
    return payloads


def _locate_member(member: tarfile.TarInfo) -> tuple[str, str] | None:
    name = member.name
    if name.startswith("/") or "\\" in name:
        raise _ArchiveRejectedError("the archive carries an absolute member path")
    segments = name.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise _ArchiveRejectedError("the archive carries a member path that escapes its directory")
    if member.issym() or member.islnk():
        raise _ArchiveRejectedError("the archive carries a link member")
    if member.isdir():
        return None
    if not member.isfile():
        raise _ArchiveRejectedError("the archive carries a device member")
    if "skills" not in segments:
        return None
    index = segments.index("skills")
    if index + 2 >= len(segments):
        return None
    skill = segments[index + 1]
    if skill not in UNIVERSAL_SKILL_NAMES:
        return None
    relative = "/".join(segments[index + 2 :])
    if Path(relative).suffix.lower() not in ALLOWED_FILE_SUFFIXES:
        raise _ArchiveRejectedError(f"the archive carries {relative} with a refused extension")
    return (skill, relative)


def _build_payload(
    archive: tarfile.TarFile, entries: list[tuple[str, tarfile.TarInfo]]
) -> _SkillPayload:
    files: dict[str, tuple[bytes, int]] = {}
    for relative, member in entries:
        files[relative] = (_read_member(archive, member), member.mode & 0o777)
    return _SkillPayload(files=files)


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if member.size > MAX_FILE_BYTES:
        raise _ArchiveRejectedError("the archive carries a file above the size cap")
    handle = archive.extractfile(member)
    if handle is None:
        raise _ArchiveRejectedError("the archive carries an unreadable file")
    with handle:
        data = handle.read()
    if len(data) > MAX_FILE_BYTES:
        raise _ArchiveRejectedError("the archive carries a file above the size cap")
    return data


def _needs_update(tag: str, state: Mapping[str, object]) -> bool:
    skills = _mapping(state.get("skills"))
    for name in UNIVERSAL_SKILL_NAMES:
        entry = _mapping(skills.get(name))
        source_tag = entry.get("source_tag")
        if not isinstance(source_tag, str) or not source_tag:
            return True
        if _is_newer_tag(tag, source_tag):
            return True
    return False


def _is_newer_tag(candidate: str, installed: str) -> bool:
    if candidate == installed:
        return False
    candidate_parts: list[str] = _TAG_NUMBER_PATTERN.findall(candidate)
    installed_parts: list[str] = _TAG_NUMBER_PATTERN.findall(installed)
    candidate_numbers = tuple(int(part) for part in candidate_parts)
    installed_numbers = tuple(int(part) for part in installed_parts)
    if candidate_numbers and installed_numbers:
        return candidate_numbers > installed_numbers
    return candidate != installed


def _read_state() -> Mapping[str, object]:
    document = _read_json_mapping(universal_state_path())
    return document if document is not None else {}


def _write_state(tag: str, installed: Mapping[str, str]) -> None:
    state = _read_state()
    skills = dict(_mapping(state.get("skills")))
    for name, digest in installed.items():
        skills[name] = {"source_tag": tag, "installed_sha256": digest}
    document: dict[str, object] = {
        "source": UPSTREAM_REPO,
        "installed_tag": tag,
        "skills": skills,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    _write_json_atomic(universal_state_path(), document)


def _install_release(tag: str, payload: Mapping[str, _SkillPayload]) -> UpdateOutcome:
    canonical_root = canonical_skills_root()
    installed: list[str] = []
    preserved: list[str] = []
    messages: list[str] = []
    digests: dict[str, str] = {}
    for name in UNIVERSAL_SKILL_NAMES:
        content = payload.get(name)
        if content is None:
            preserved.append(name)
            messages.append(f"{name}: absent from the {tag} archive; left unchanged")
            continue
        refusal = _missing_reference(content)
        if refusal is not None:
            preserved.append(name)
            messages.append(f"{name}: not installed; {refusal}")
            continue
        reason = _preservation_reason(canonical_root / name)
        if reason is not None:
            preserved.append(name)
            messages.append(f"{name}: preserved; {reason}")
            continue
        try:
            digests[name] = _install_skill(canonical_root / name, content, tag)
        except OSError as exc:
            preserved.append(name)
            messages.append(f"{name}: the install failed ({type(exc).__name__})")
            continue
        installed.append(name)
    _mirror_installed(canonical_root, installed, messages)
    if installed:
        _write_state(tag, digests)
    status = "updated" if installed else "preserved"
    return UpdateOutcome(
        status=status,
        attempted=True,
        tag=tag,
        installed=tuple(installed),
        preserved=tuple(preserved),
        messages=tuple(messages),
    )


def _install_skill(skill_dir: Path, content: _SkillPayload, tag: str) -> str:
    _ensure_dir(skill_dir)
    digests: dict[str, str] = {}
    for relative, (data, mode) in sorted(content.files.items()):
        target = skill_dir / relative
        _write_file(target, data, mode)
        digests[relative] = hashlib.sha256(data).hexdigest()
    for relative in _stale_files(skill_dir, content):
        _remove_file(skill_dir / relative)
    digest = _manifest_digest(digests)
    marker: dict[str, object] = {
        "managed_by": MANAGED_BY,
        "source": UPSTREAM_REPO,
        "source_tag": tag,
        "installed_sha256": digest,
        "files": digests,
    }
    _write_json(skill_dir / MANAGED_MARKER_NAME, marker)
    return digest


def _manifest_digest(files: Mapping[str, str]) -> str:
    manifest: dict[str, str] = dict(sorted(files.items()))
    payload: str = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stale_files(skill_dir: Path, content: _SkillPayload) -> list[str]:
    marker = _read_json_mapping(skill_dir / MANAGED_MARKER_NAME)
    if marker is None:
        return []
    previous = _string_map(marker.get("files"))
    return sorted(name for name in previous if name not in content.files)


def _write_file(target: Path, data: bytes, mode: int) -> None:
    _ensure_dir(target.parent)
    target.write_bytes(data)
    target.chmod(mode)


def _remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path)


def _preservation_reason(skill_dir: Path) -> str | None:
    if not skill_dir.exists():
        return None
    marker_path = skill_dir / MANAGED_MARKER_NAME
    if not marker_path.is_file():
        return "the directory carries no Ralph managed marker"
    marker = _read_json_mapping(marker_path)
    if marker is None:
        return "the Ralph managed marker cannot be read"
    issue = _marker_issue(marker)
    if issue is not None:
        return issue
    if _string_map(marker.get("files")) != _current_digests(skill_dir):
        return "the installed files differ from the Ralph managed marker"
    return None


def _marker_issue(marker: Mapping[str, object]) -> str | None:
    if marker.get("managed_by") != MANAGED_BY:
        return "the directory is managed by another component"
    if not _string_map(marker.get("files")):
        return "the Ralph managed marker carries no file manifest"
    return None


def _current_digests(skill_dir: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(skill_dir).as_posix()
        if relative == MANAGED_MARKER_NAME:
            continue
        digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _missing_reference(content: _SkillPayload) -> str | None:
    installed = {Path(name).name for name in content.files}
    for relative in sorted(content.files):
        if not relative.endswith(".md"):
            continue
        data = content.files[relative][0]
        for reference in _iter_references(data.decode("utf-8", errors="replace")):
            if reference in installed:
                continue
            if Path(reference).suffix.lower() in ALLOWED_FILE_SUFFIXES:
                return f"it references {reference}, which the archive does not carry"
    return None


def _iter_references(text: str) -> Iterator[str]:
    for match in _REFERENCE_PATTERN.finditer(text):
        token: str = match.group(1) or match.group(2) or ""
        candidate = token.strip()
        if not candidate or " " in candidate:
            continue
        if candidate.startswith(("http://", "https://", "/", "~", "#")):
            continue
        if ".." in candidate.split("/"):
            continue
        name = candidate.rsplit("/", 1)[-1]
        if not _FILE_NAME_PATTERN.match(name):
            continue
        yield name


def _mirror_installed(canonical_root: Path, installed: list[str], messages: list[str]) -> None:
    for name in installed:
        try:
            _mirror_skill(canonical_root, name)
        except OSError as exc:
            messages.append(f"{name}: the sibling mirror failed ({type(exc).__name__})")


def _mirror_skill(canonical_root: Path, skill_name: str) -> None:
    canonical_dir = canonical_root / skill_name
    canonical_target = _resolved(canonical_dir)
    home = _resolved(Path.home())
    for sibling_root in sibling_skills_roots():
        resolved_root = _resolved(sibling_root)
        if resolved_root == _resolved(canonical_root):
            continue
        if not _is_within(resolved_root, home):
            continue
        sibling_dir = sibling_root / skill_name
        if sibling_dir.is_symlink():
            if _resolved(sibling_dir) == canonical_target:
                continue
            _remove_file(sibling_dir)
        elif sibling_dir.is_dir():
            if _preservation_reason(sibling_dir) is not None:
                continue
            _remove_tree(sibling_dir)
        elif sibling_dir.exists():
            continue
        _ensure_dir(sibling_root)
        _link_skill(sibling_dir, canonical_dir)


def _link_skill(sibling_dir: Path, canonical_dir: Path) -> None:
    try:
        sibling_dir.symlink_to(canonical_dir, target_is_directory=True)
    except OSError:
        shutil.copytree(canonical_dir, sibling_dir)


def _resolved(path: Path) -> Path:
    return path.resolve()


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


__all__ = [
    "ALLOWED_FILE_SUFFIXES",
    "ARCHIVE_URL_TEMPLATE",
    "ATTEMPT_WINDOW",
    "CACHE_FILE_NAME",
    "MANAGED_BY",
    "MANAGED_MARKER_NAME",
    "MAX_ARCHIVE_BYTES",
    "MAX_FILE_BYTES",
    "RELEASES_API_URL",
    "REQUEST_TIMEOUT_SECONDS",
    "STATE_FILE_NAME",
    "UNIVERSAL_SKILL_NAMES",
    "UPSTREAM_REPO",
    "HttpClient",
    "HttpResponse",
    "UpdateOutcome",
    "attempt_cache_path",
    "canonical_skills_root",
    "default_http_client",
    "maybe_update_universal_skills",
    "sibling_skills_roots",
    "universal_state_path",
]
