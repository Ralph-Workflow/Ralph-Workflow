from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

if sys.platform != "win32":
    import fcntl

from ralph.checked_accessors import as_mapping, as_str_dict, optional_str
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.skills._agent_paths import (
    agent_skill_roots,
    canonical_agent_skill_root,
    is_skill_present_for_agent,
    sibling_agent_skill_roots,
)
from ralph.skills._ownership import (
    MANAGED_MARKER_NAME,
    UNIVERSAL_MANAGED_BY,
    managed_content_matches,
    read_managed_marker,
    verified_recorded_files,
)
from ralph.skills._upstream_install import (
    ALLOWED_EXTENSIONLESS_COMPANIONS,
    BACKUP_SUFFIX,
    SKILL_FILE_NAME,
    STAGING_PREFIX,
    _ArchiveLimits,
    _SkillPayload,
    _StagedSkill,
    discard_staged,
    discard_tree,
    extract_archive,
    payload_refusal,
    publish_sibling_mirrors,
    publish_staged,
    recover_publication,
    rollback,
    settle_mirrors,
    stage_skill,
    sweep_staging,
)
from ralph.skills._upstream_transport import (
    _RedirectRefusedError as UpstreamRedirectRefusedError,
)
from ralph.skills._upstream_transport import (
    _ResponseTooLargeError as UpstreamResponseTooLargeError,
)
from ralph.skills._upstream_transport import (
    _TransportError as UpstreamTransportError,
)
from ralph.skills._upstream_transport import (
    streaming_client,
)

UPSTREAM_REPO = "obra/superpowers"

ANTI_SLOP_REPO = "miqdadbadjuber/anti-slop"

RELEASES_API_URL = "https://api.github.com/repos/obra/superpowers/releases/latest"

ARCHIVE_URL_TEMPLATE = "https://codeload.github.com/obra/superpowers/tar.gz/refs/tags/{tag}"

ANTI_SLOP_RELEASES_API_URL = "https://api.github.com/repos/miqdadbadjuber/anti-slop/releases/latest"

ANTI_SLOP_ARCHIVE_URL_TEMPLATE = (
    "https://codeload.github.com/miqdadbadjuber/anti-slop/tar.gz/refs/tags/{tag}"
)


@dataclass(frozen=True)
class _UpstreamSource:
    slug: str
    repo: str
    api_url: str
    archive_url_template: str
    skill_names: tuple[str, ...]


UpstreamSource = _UpstreamSource


UNIVERSAL_UPSTREAMS: tuple[UpstreamSource, ...] = (
    UpstreamSource(
        UPSTREAM_REPO,
        UPSTREAM_REPO,
        RELEASES_API_URL,
        ARCHIVE_URL_TEMPLATE,
        ("test-driven-development", "systematic-debugging", "verification-before-completion"),
    ),
    UpstreamSource(
        ANTI_SLOP_REPO,
        ANTI_SLOP_REPO,
        ANTI_SLOP_RELEASES_API_URL,
        ANTI_SLOP_ARCHIVE_URL_TEMPLATE,
        ("antislop",),
    ),
)

UNIVERSAL_SKILL_NAMES: tuple[str, ...] = tuple(
    name for upstream in UNIVERSAL_UPSTREAMS for name in upstream.skill_names
)

MANAGED_BY = UNIVERSAL_MANAGED_BY
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_RELEASE_BYTES = 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2048
MAX_EXTRACTED_BYTES = 32 * 1024 * 1024
MAX_SKILL_FILES = 512
ALLOWED_FILE_SUFFIXES: frozenset[str] = frozenset(
    {".json", ".md", ".py", ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml"}
)
REQUEST_TIMEOUT_SECONDS = 5.0
ATTEMPT_WINDOW = timedelta(hours=24)
LOCK_WAIT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.05
LOCK_STALE_SECONDS = 120.0
LOCK_CLAIM_SUFFIX = ".claim"
CACHE_FILE_NAME = "skill-updates.json"
ATTEMPT_LOCK_SUFFIX = ".lock"
PUBLICATION_LOCK_NAME = "skill-publish.lock"
STATE_FILE_NAME = "ralph-workflow-universal-skills.json"

type HttpResponse = tuple[int, bytes]

type HttpClient = Callable[[str, float], HttpResponse]

HTTP_OK = 200

HTTP_NOT_FOUND = 404

NO_RELEASE_ERROR = "release-api-no-release"

_NOFOLLOW_FLAG: int = getattr(os, "O_NOFOLLOW", 0)

_ATTEMPT_LOCK = threading.Lock()

_CARRIED_SUFFIXES: Final[frozenset[str]] = frozenset(
    suffix.lower() for suffix in ALLOWED_FILE_SUFFIXES
)
_TAG_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\d+")
_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class UpdateOutcome:
    status: str
    attempted: bool = False
    tag: str | None = None
    installed: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class _InstallPlan:
    tag: str
    skills: tuple[tuple[str, _SkillPayload], ...]
    preserved: tuple[str, ...]
    messages: tuple[str, ...]


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
    return streaming_client(_response_byte_cap)


def canonical_skills_root() -> Path:
    return canonical_agent_skill_root().resolve()


def sibling_skills_roots() -> tuple[Path, ...]:
    return tuple(entry.resolve() for entry in sibling_agent_skill_roots())


def attempt_cache_path() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "ralph-workflow" / CACHE_FILE_NAME


def attempt_lock_path() -> Path:
    return attempt_cache_path().with_name(attempt_cache_path().name + ATTEMPT_LOCK_SUFFIX)


def publication_lock_path() -> Path:
    return attempt_cache_path().parent / PUBLICATION_LOCK_NAME


def universal_state_path() -> Path:
    return Path.home() / ".config" / STATE_FILE_NAME


def _response_byte_cap(url: str) -> int:
    for source in UNIVERSAL_UPSTREAMS:
        prefix = source.archive_url_template.split("{", 1)[0]
        if prefix and url.startswith(prefix):
            return MAX_ARCHIVE_BYTES
    return MAX_RELEASE_BYTES


def _run_update(*, enabled: bool, now: datetime, http: HttpClient) -> UpdateOutcome:
    if not enabled:
        return UpdateOutcome(status="disabled")
    instant = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    _repair_publications(canonical_skills_root())
    if not _reserve_attempt(attempt_cache_path(), instant):
        return UpdateOutcome(
            status="skipped",
            messages=("the universal skill update was attempted within the last 24 h",),
        )
    state = _read_state()
    outcomes = tuple(_update_source(source, state, http) for source in UNIVERSAL_UPSTREAMS)
    return _aggregate_outcomes(outcomes)


def _update_source(
    source: UpstreamSource, state: Mapping[str, object], http: HttpClient
) -> UpdateOutcome:
    tag, error = _fetch_release_tag(http, source)
    if tag is None:
        if error == NO_RELEASE_ERROR:
            return UpdateOutcome(
                status="no-release",
                attempted=True,
                messages=(f"{source.slug}: no release is published yet",),
            )
        return UpdateOutcome(
            status="failed",
            attempted=True,
            error=error,
            messages=(f"{source.slug}: the upstream release could not be read",),
        )
    if not _needs_update(tag, state, source):
        return _settle_mirrors(source, tag)
    payload, error = _load_release_payload(http, source, tag)
    if payload is None:
        return UpdateOutcome(
            status="failed",
            attempted=True,
            tag=tag,
            error=error,
            messages=(f"{source.slug}: the upstream skill archive could not be installed",),
        )
    return _install_release(source, tag, payload)


def _aggregate_outcomes(outcomes: tuple[UpdateOutcome, ...]) -> UpdateOutcome:
    statuses = {outcome.status for outcome in outcomes}
    error = next((outcome.error for outcome in outcomes if outcome.error is not None), None)
    if "failed" in statuses:
        return UpdateOutcome(
            status="failed",
            attempted=True,
            error=error,
            messages=_joined_messages(outcomes),
        )
    status = "no-release"
    for candidate in ("incomplete", "updated", "repaired", "up-to-date", "preserved"):
        if candidate in statuses:
            status = candidate
            break
    return UpdateOutcome(
        status=status,
        attempted=True,
        tag=next((outcome.tag for outcome in outcomes if outcome.tag is not None), None),
        installed=_joined_names(outcome.installed for outcome in outcomes),
        preserved=_joined_names(outcome.preserved for outcome in outcomes),
        messages=_joined_messages(outcomes),
    )


def _joined_names(groups: Iterator[tuple[str, ...]]) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for group in groups:
        for name in group:
            names.setdefault(name, None)
    return tuple(names)


def _joined_messages(outcomes: tuple[UpdateOutcome, ...]) -> tuple[str, ...]:
    messages: list[str] = []
    for outcome in outcomes:
        messages.extend(outcome.messages)
    return tuple(messages)


def _reserve_attempt(cache_path: Path, now: datetime) -> bool:
    with (
        _ATTEMPT_LOCK,
        _advisory_lock(cache_path.with_name(cache_path.name + ATTEMPT_LOCK_SUFFIX)),
    ):
        previous = _read_attempt(cache_path)
        if previous is not None and (now - previous) < ATTEMPT_WINDOW:
            return False
        _write_json_atomic(cache_path, {"last_attempt": now.isoformat()})
        return True


@contextmanager
def _advisory_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = _open_lock_descriptor(lock_path, os.O_CREAT | os.O_RDWR | _NOFOLLOW_FLAG)
    try:
        release = _acquire_platform_lock(descriptor)
        if release is not None:
            try:
                yield
            finally:
                release()
            return
        with _claim_lock(lock_path):
            yield
    finally:
        os.close(descriptor)


def _open_lock_descriptor(path: Path, flags: int) -> int:
    return os.open(path, flags, 0o600)


def _acquire_platform_lock(descriptor: int) -> Callable[[], None] | None:
    if sys.platform == "win32":
        return None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError:
        return None

    def _release() -> None:
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)

    return _release


@contextmanager
def _claim_lock(lock_path: Path) -> Iterator[None]:
    claim_path = lock_path.with_name(lock_path.name + LOCK_CLAIM_SUFFIX)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            descriptor = _open_lock_descriptor(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _claim_is_stale(claim_path) or time.monotonic() >= deadline:
                DEFAULT_FILE_BACKEND.unlink(claim_path, missing_ok=True)
                continue
            time.sleep(LOCK_POLL_SECONDS)
            continue
        os.close(descriptor)
        break
    try:
        yield
    finally:
        DEFAULT_FILE_BACKEND.unlink(claim_path, missing_ok=True)


def _claim_is_stale(claim_path: Path) -> bool:
    try:
        return (time.time() - claim_path.stat().st_mtime) > LOCK_STALE_SECONDS
    except OSError:
        return False


def _read_attempt(cache_path: Path) -> datetime | None:
    document = _read_json_mapping(cache_path)
    if document is None:
        return None
    stamp = optional_str(document.get("last_attempt"), field="last_attempt")
    if stamp is None:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:
        return None


def _read_json_mapping(path: Path) -> Mapping[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed: object = json.loads(raw)
        return as_mapping(parsed, field=path.name)
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
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


def _write_json_atomic(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _transport_error_reason(exc: Exception, prefix: str) -> str:
    if isinstance(exc, UpstreamResponseTooLargeError):
        return f"{prefix}-too-large"
    return (
        f"{prefix}-redirect"
        if isinstance(exc, UpstreamRedirectRefusedError)
        else f"{prefix}-error:{type(exc).__name__}"
    )


def _parse_release_tag(status: int, body: bytes) -> tuple[str | None, str | None]:
    if status == HTTP_NOT_FOUND:
        return (None, NO_RELEASE_ERROR)
    if status != HTTP_OK:
        return (None, f"release-api-status-{status}")
    if len(body) > MAX_RELEASE_BYTES:
        return (None, "release-api-too-large")
    try:
        payload: object = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return (None, "release-api-malformed")
    tag = optional_str(_mapping(payload).get("tag_name"), field="tag_name")
    if tag is None or _TAG_PATTERN.fullmatch(tag) is None:
        return (None, "release-api-malformed")
    return (tag, None)


def _fetch_release_tag(http: HttpClient, source: UpstreamSource) -> tuple[str | None, str | None]:
    try:
        status, body = http(source.api_url, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        return (None, _transport_error_reason(exc, "release-api"))
    return _parse_release_tag(status, body)


def _load_release_payload(
    http: HttpClient, source: UpstreamSource, tag: str
) -> tuple[dict[str, _SkillPayload] | None, str | None]:
    archive_url = source.archive_url_template.format(tag=tag)
    try:
        status, body = http(archive_url, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        return (None, _transport_error_reason(exc, "archive"))
    if status != HTTP_OK:
        return (None, f"archive-status-{status}")
    if len(body) > MAX_ARCHIVE_BYTES:
        return (None, "archive-too-large")
    try:
        payload = extract_archive(body, _archive_limits(source))
    except Exception as exc:
        return (None, str(exc))
    return (payload, None)


def _archive_limits(source: UpstreamSource | None = None) -> _ArchiveLimits:
    skill_names = UNIVERSAL_SKILL_NAMES if source is None else source.skill_names
    return _ArchiveLimits(
        skill_names=skill_names,
        allowed_suffixes=ALLOWED_FILE_SUFFIXES,
        max_file_bytes=MAX_FILE_BYTES,
        max_members=MAX_ARCHIVE_MEMBERS,
        max_extracted_bytes=MAX_EXTRACTED_BYTES,
        max_skill_files=MAX_SKILL_FILES,
        allowed_extensionless_names=ALLOWED_EXTENSIONLESS_COMPANIONS,
    )


def _source_slug(source: str | UpstreamSource) -> str:
    return source.slug if isinstance(source, UpstreamSource) else source


def _source_skills(source: str | UpstreamSource) -> tuple[str, ...]:
    slug = _source_slug(source)
    return next((s.skill_names for s in UNIVERSAL_UPSTREAMS if slug in (s.slug, s.repo)), ())


def _source_for_skill(skill_name: str) -> str:
    return next((s.slug for s in UNIVERSAL_UPSTREAMS if skill_name in s.skill_names), UPSTREAM_REPO)


def _migrate_state(state: Mapping[str, object]) -> dict[str, object]:
    if "sources" in state:
        return dict(state)
    migrated = {k: v for k, v in state.items() if k not in ("source", "installed_tag")}
    source = str(state.get("source") or UPSTREAM_REPO)
    tag = state.get("installed_tag")
    installed_tag = tag if isinstance(tag, str) else ""
    sources: dict[str, object] = {}
    for name in _mapping(state.get("skills")):
        sources[_source_for_skill(str(name))] = {"installed_tag": installed_tag}
    sources.setdefault(source, {"installed_tag": installed_tag})
    migrated["sources"] = sources
    return migrated


def _needs_update(
    tag: str,
    state: Mapping[str, object],
    source: str | UpstreamSource = UPSTREAM_REPO,
) -> bool:
    migrated = _migrate_state(state)
    source_entry = _mapping(_mapping(migrated.get("sources")).get(_source_slug(source)))
    installed_tag = source_entry.get("installed_tag")
    if not isinstance(installed_tag, str) or not installed_tag or _is_newer_tag(tag, installed_tag):
        return True
    skills = _mapping(migrated.get("skills"))
    for name in _source_skills(source):
        stag = _mapping(skills.get(name)).get("source_tag")
        if not isinstance(stag, str) or not stag or _is_newer_tag(tag, stag):
            return True
    return False


def _is_newer_tag(candidate: str, installed: str) -> bool:
    if candidate == installed:
        return False
    cand_parts: list[str] = _TAG_NUMBER_PATTERN.findall(candidate)
    inst_parts: list[str] = _TAG_NUMBER_PATTERN.findall(installed)
    cand_nums = tuple(int(p) for p in cand_parts)
    inst_nums = tuple(int(p) for p in inst_parts)
    return cand_nums > inst_nums if cand_nums and inst_nums else candidate != installed


def _read_state() -> Mapping[str, object]:
    path = universal_state_path()
    document = _read_json_mapping(path)
    if document is None:
        return {}
    if "sources" not in document and ("source" in document or "installed_tag" in document):
        migrated = _migrate_state(document)
        with suppress(OSError):
            _write_json_atomic(path, migrated)
        return migrated
    return document


def _write_state(
    source: UpstreamSource,
    tag: str,
    installed: Mapping[str, str],
) -> None:
    state = dict(_read_state())
    sources = dict(_mapping(state.get("sources")))
    skills = dict(_mapping(state.get("skills")))
    slug = _source_slug(source)
    sources[slug] = {"installed_tag": tag}
    for name, digest in installed.items():
        skills[name] = {
            "source": slug,
            "source_tag": tag,
            "installed_sha256": digest,
        }
    _write_json_atomic(
        universal_state_path(),
        {"sources": sources, "skills": skills, "updated_at": datetime.now(tz=UTC).isoformat()},
    )


def _repair_publications(canonical_root: Path) -> None:
    with _advisory_lock(publication_lock_path()):
        for name in UNIVERSAL_SKILL_NAMES:
            recover_publication(canonical_root, name)
        sweep_staging(canonical_root)


def _user_owned_skills() -> frozenset[str]:
    return frozenset(
        name
        for name in UNIVERSAL_SKILL_NAMES
        if any(is_skill_present_for_agent(name, entry.agent) for entry in agent_skill_roots())
    )


def _install_release(
    source: UpstreamSource, tag: str, payload: Mapping[str, _SkillPayload]
) -> UpdateOutcome:
    canonical_root = canonical_skills_root()
    user_owned = _user_owned_skills()
    with _advisory_lock(publication_lock_path()):
        sweep_staging(canonical_root)
        plan = _plan_install(canonical_root, source, tag, payload, user_owned)
        if not plan.skills:
            return UpdateOutcome(
                status="preserved",
                attempted=True,
                tag=tag,
                preserved=plan.preserved,
                messages=plan.messages,
            )
        installed, digests, failure = _publish_plan(canonical_root, plan)
    if failure is not None:
        return UpdateOutcome(
            status="failed",
            attempted=True,
            tag=tag,
            preserved=plan.preserved,
            messages=(*plan.messages, failure),
            error="install-failed",
        )
    messages = list(plan.messages)
    mirror_messages, failed_skills = publish_sibling_mirrors(
        canonical_root, tuple(installed), sibling_skills_roots()
    )
    messages.extend(mirror_messages)
    _write_state(source, tag, digests)
    return UpdateOutcome(
        status="incomplete" if failed_skills else "updated",
        attempted=True,
        tag=tag,
        installed=tuple(installed),
        preserved=plan.preserved,
        messages=tuple(messages),
    )


def _plan_install(
    canonical_root: Path,
    source: UpstreamSource,
    tag: str,
    payload: Mapping[str, _SkillPayload],
    user_owned: frozenset[str],
) -> _InstallPlan:
    skills: list[tuple[str, _SkillPayload]] = []
    preserved: list[str] = []
    messages: list[str] = []
    for name in source.skill_names:
        content = payload.get(name)
        if name in user_owned:
            preserved.append(name)
            messages.append(
                f"{name}: preserved; the user already has this skill in an agent skill root"
            )
        elif content is None:
            preserved.append(name)
            messages.append(f"{name}: absent from the {tag} archive; left unchanged")
        elif (refusal := payload_refusal(content, _carried_suffix)) is not None:
            preserved.append(name)
            messages.append(f"{name}: not installed; {refusal}")
        elif (reason := _preservation_reason(canonical_root / name)) is not None:
            preserved.append(name)
            messages.append(f"{name}: preserved; {reason}")
        else:
            skills.append((name, content))
    return _InstallPlan(
        tag=tag, skills=tuple(skills), preserved=tuple(preserved), messages=tuple(messages)
    )


def _carried_suffix(path: str) -> str | None:
    _, dot, extension = path.rpartition(".")
    suffix = f".{extension.lower()}"
    return suffix if dot and suffix in _CARRIED_SUFFIXES else None


def _publish_plan(
    canonical_root: Path, plan: _InstallPlan
) -> tuple[list[str], dict[str, str], str | None]:
    staged: list[_StagedSkill] = []
    published: list[tuple[str, Path | None]] = []
    digests: dict[str, str] = {}
    try:
        for name, content in plan.skills:
            staged.append(
                stage_skill(canonical_root, name, content, plan.tag, source=_source_for_skill(name))
            )
        for entry in staged:
            backup = publish_staged(entry, canonical_root / entry.name)
            published.append((entry.name, backup))
            digests[entry.name] = entry.digest
    except OSError as exc:
        rollback(canonical_root, published)
        discard_staged(staged)
        return (
            [],
            {},
            f"the install failed ({type(exc).__name__}); the previous installation was kept",
        )
    except BaseException:
        rollback(canonical_root, published)
        discard_staged(staged)
        raise
    for _name, backup in published:
        if backup is not None:
            discard_tree(backup)
    discard_staged(staged)
    return ([name for name, _ in published], digests, None)


def _preservation_reason(skill_dir: Path) -> str | None:
    if not skill_dir.exists():
        return None
    marker = read_managed_marker(skill_dir)
    if marker is None:
        return "the Ralph managed marker cannot be read"
    if (recorded := verified_recorded_files(marker)) is None:
        return "the Ralph managed marker carries no usable file manifest"
    if marker.get("managed_by") != MANAGED_BY:
        return "the directory is managed by another component"
    if not managed_content_matches(skill_dir, recorded):
        return "the installed files differ from the Ralph managed marker"
    return None


def _settle_mirrors(source: UpstreamSource, tag: str) -> UpdateOutcome:
    state = _read_state()
    sources = _mapping(state.get("sources"))
    source_entry = _mapping(sources.get(source.slug))
    source_tag = source_entry.get("installed_tag")
    effective_tag = str(source_tag) if isinstance(source_tag, str) and source_tag else tag
    if not effective_tag:
        return UpdateOutcome(status="up-to-date", attempted=True, tag=tag or None)
    canonical_root = canonical_skills_root()
    has_canonical = any((canonical_root / name).exists() for name in source.skill_names)
    if not has_canonical and not source_tag and source.slug != UPSTREAM_REPO:
        return UpdateOutcome(status="up-to-date", attempted=True, tag=tag or None)
    settlement = settle_mirrors(
        canonical_root, source.skill_names, sibling_skills_roots(), effective_tag
    )
    return UpdateOutcome(
        status=settlement.status,
        attempted=True,
        tag=tag or None,
        installed=settlement.repaired,
        preserved=settlement.preserved,
        messages=settlement.messages,
    )


__all__ = [
    "ALLOWED_EXTENSIONLESS_COMPANIONS",
    "ALLOWED_FILE_SUFFIXES",
    "ANTI_SLOP_ARCHIVE_URL_TEMPLATE",
    "ANTI_SLOP_RELEASES_API_URL",
    "ANTI_SLOP_REPO",
    "ARCHIVE_URL_TEMPLATE",
    "ATTEMPT_LOCK_SUFFIX",
    "ATTEMPT_WINDOW",
    "BACKUP_SUFFIX",
    "CACHE_FILE_NAME",
    "MANAGED_BY",
    "MANAGED_MARKER_NAME",
    "MAX_ARCHIVE_BYTES",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_EXTRACTED_BYTES",
    "MAX_FILE_BYTES",
    "MAX_RELEASE_BYTES",
    "MAX_SKILL_FILES",
    "PUBLICATION_LOCK_NAME",
    "RELEASES_API_URL",
    "REQUEST_TIMEOUT_SECONDS",
    "SKILL_FILE_NAME",
    "STAGING_PREFIX",
    "STATE_FILE_NAME",
    "UNIVERSAL_SKILL_NAMES",
    "UNIVERSAL_UPSTREAMS",
    "UPSTREAM_REPO",
    "HttpClient",
    "HttpResponse",
    "UpdateOutcome",
    "UpstreamRedirectRefusedError",
    "UpstreamResponseTooLargeError",
    "UpstreamSource",
    "UpstreamTransportError",
    "attempt_cache_path",
    "attempt_lock_path",
    "canonical_skills_root",
    "default_http_client",
    "maybe_update_universal_skills",
    "publication_lock_path",
    "sibling_skills_roots",
    "universal_state_path",
]
