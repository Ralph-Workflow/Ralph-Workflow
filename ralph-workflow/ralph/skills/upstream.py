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
from urllib.parse import unquote

if sys.platform != "win32":
    import fcntl

from ralph.checked_accessors import as_mapping, as_str_dict, optional_str
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.skills._agent_paths import (
    canonical_agent_skill_root,
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
    BACKUP_SUFFIX,
    STAGING_PREFIX,
    _ArchiveLimits,
    _SkillPayload,
    _StagedSkill,
    discard_staged,
    discard_tree,
    extract_archive,
    mirror_skill,
    publish_staged,
    recover_publication,
    rollback,
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

RELEASES_API_URL = "https://api.github.com/repos/obra/superpowers/releases/latest"

ARCHIVE_URL_TEMPLATE = "https://codeload.github.com/obra/superpowers/tar.gz/refs/tags/{tag}"

UNIVERSAL_SKILL_NAMES: tuple[str, ...] = (
    "test-driven-development",
    "systematic-debugging",
    "verification-before-completion",
)

SKILL_FILE_NAME = "SKILL.md"

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

try:
    _NOFOLLOW_FLAG: int = os.O_NOFOLLOW
except AttributeError:
    _NOFOLLOW_FLAG = 0

_ATTEMPT_LOCK = threading.Lock()

_REFERENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"`([^`\n]+)"
    r"|\]\((<[^>\n]*>)\)"
    r"|\]\(((?:[^()\n]|\([^()\n]*\))*)\)"
    r"|\]\(([^)\n]+)\)"
    r"|^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*(<[^>\n]*>|\S+)",
    re.MULTILINE,
)

_HTML_ATTRIBUTE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""\b(?:href|src)\s*=\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s"'<>`\n]+))""",
    re.IGNORECASE,
)


_FILE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*\.[A-Za-z]+$")

_CARRIED_SUFFIXES: Final[frozenset[str]] = frozenset(
    suffix.lower() for suffix in ALLOWED_FILE_SUFFIXES
)

_URL_SCHEME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

_TAG_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\d+")

_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

_CUE_VERBS: Final[tuple[str, ...]] = (
    "see",
    "read",
    "open",
    "consult",
    "check",
    "refer to",
)

_CUE_ADVERBS: Final[tuple[str, ...]] = ("please", "then", "also", "first", "now")

_CLAUSE_BOUNDARIES: Final[str] = ".!?:;(—"

_LIST_MARKERS: Final[frozenset[str]] = frozenset({"-", "*", "+"})

_NUMBERED_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\d+[.)]")

_EMPHASIS_RUN_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?:\*{2,}|_{2,})$")

_LOCATION_CUE: Final[str] = "in this directory"


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


@dataclass(frozen=True)
class _CompanionReference:
    raw: str
    resolved: str
    escapes: bool
    directory: bool


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
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "ralph-workflow" / CACHE_FILE_NAME


def attempt_lock_path() -> Path:
    cache = attempt_cache_path()
    return cache.with_name(cache.name + ATTEMPT_LOCK_SUFFIX)


def publication_lock_path() -> Path:
    return attempt_cache_path().parent / PUBLICATION_LOCK_NAME


def universal_state_path() -> Path:
    return Path.home() / ".config" / STATE_FILE_NAME


def _response_byte_cap(url: str) -> int:
    if url == RELEASES_API_URL:
        return MAX_RELEASE_BYTES
    prefix = ARCHIVE_URL_TEMPLATE.split("{", 1)[0]
    if prefix and url.startswith(prefix):
        return MAX_ARCHIVE_BYTES
    return MAX_RELEASE_BYTES


def _run_update(*, enabled: bool, now: datetime, http: HttpClient) -> UpdateOutcome:
    if not enabled:
        return UpdateOutcome(status="disabled")
    instant = _utc(now)
    _repair_publications(canonical_skills_root())
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
    _ensure_dir(lock_path.parent)
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
    return _acquire_posix_lock(descriptor)


def _acquire_posix_lock(descriptor: int) -> Callable[[], None] | None:
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
        age = time.time() - claim_path.stat().st_mtime
    except OSError:
        return False
    return age > LOCK_STALE_SECONDS


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


def _transport_error_reason(exc: Exception, prefix: str) -> str:
    if isinstance(exc, UpstreamResponseTooLargeError):
        return f"{prefix}-too-large"
    if isinstance(exc, UpstreamRedirectRefusedError):
        return f"{prefix}-redirect"
    return f"{prefix}-error:{type(exc).__name__}"


def _fetch_release_tag(http: HttpClient) -> tuple[str | None, str | None]:
    try:
        status, body = http(RELEASES_API_URL, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        return (None, _transport_error_reason(exc, "release-api"))
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


def _load_release_payload(
    http: HttpClient, tag: str
) -> tuple[dict[str, _SkillPayload] | None, str | None]:
    archive_url = ARCHIVE_URL_TEMPLATE.format(tag=tag)
    try:
        status, body = http(archive_url, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        return (None, _transport_error_reason(exc, "archive"))
    if status != HTTP_OK:
        return (None, f"archive-status-{status}")
    if len(body) > MAX_ARCHIVE_BYTES:
        return (None, "archive-too-large")
    try:
        payload = extract_archive(body, _archive_limits())
    except Exception as exc:
        return (None, str(exc))
    return (payload, None)


def _archive_limits() -> _ArchiveLimits:
    return _ArchiveLimits(
        skill_names=UNIVERSAL_SKILL_NAMES,
        allowed_suffixes=ALLOWED_FILE_SUFFIXES,
        max_file_bytes=MAX_FILE_BYTES,
        max_members=MAX_ARCHIVE_MEMBERS,
        max_extracted_bytes=MAX_EXTRACTED_BYTES,
        max_skill_files=MAX_SKILL_FILES,
    )


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


def _repair_publications(canonical_root: Path) -> None:
    with _advisory_lock(publication_lock_path()):
        for name in UNIVERSAL_SKILL_NAMES:
            recover_publication(canonical_root, name)
        sweep_staging(canonical_root)


def _install_release(tag: str, payload: Mapping[str, _SkillPayload]) -> UpdateOutcome:
    canonical_root = canonical_skills_root()
    with _advisory_lock(publication_lock_path()):
        sweep_staging(canonical_root)
        plan = _plan_install(canonical_root, tag, payload)
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
    _mirror_installed(canonical_root, installed, messages)
    _write_state(tag, digests)
    return UpdateOutcome(
        status="updated",
        attempted=True,
        tag=tag,
        installed=tuple(installed),
        preserved=plan.preserved,
        messages=tuple(messages),
    )


def _plan_install(
    canonical_root: Path, tag: str, payload: Mapping[str, _SkillPayload]
) -> _InstallPlan:
    skills: list[tuple[str, _SkillPayload]] = []
    preserved: list[str] = []
    messages: list[str] = []
    for name in UNIVERSAL_SKILL_NAMES:
        content = payload.get(name)
        if content is None:
            preserved.append(name)
            messages.append(f"{name}: absent from the {tag} archive; left unchanged")
            continue
        refusal = _payload_refusal(content)
        if refusal is not None:
            preserved.append(name)
            messages.append(f"{name}: not installed; {refusal}")
            continue
        reason = _preservation_reason(canonical_root / name)
        if reason is not None:
            preserved.append(name)
            messages.append(f"{name}: preserved; {reason}")
            continue
        skills.append((name, content))
    return _InstallPlan(
        tag=tag,
        skills=tuple(skills),
        preserved=tuple(preserved),
        messages=tuple(messages),
    )


def _payload_refusal(content: _SkillPayload) -> str | None:
    if SKILL_FILE_NAME not in content.files:
        return f"the payload carries no {SKILL_FILE_NAME}"
    return _missing_reference(content)


def _publish_plan(
    canonical_root: Path, plan: _InstallPlan
) -> tuple[list[str], dict[str, str], str | None]:
    staged: list[_StagedSkill] = []
    published: list[tuple[str, Path | None]] = []
    digests: dict[str, str] = {}
    try:
        for name, content in plan.skills:
            staged.append(
                stage_skill(canonical_root, name, content, plan.tag, source=UPSTREAM_REPO)
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
    recorded = verified_recorded_files(marker)
    if recorded is None:
        return "the Ralph managed marker carries no usable file manifest"
    if marker.get("managed_by") != MANAGED_BY:
        return "the directory is managed by another component"
    if not managed_content_matches(skill_dir, recorded):
        return "the installed files differ from the Ralph managed marker"
    return None


def _missing_reference(content: _SkillPayload) -> str | None:
    for relative in sorted(content.files):
        if Path(relative).suffix.lower() != ".md":
            continue
        data = content.files[relative][0]
        text = data.decode("utf-8", errors="replace")
        for reference in _iter_references(text, relative):
            if reference.escapes:
                return f"it references {reference.raw}, which is outside the skill directory"
            if _is_carried(content, reference):
                continue
            return _missing_reference_reason(reference)
    return None


def _is_carried(content: _SkillPayload, reference: _CompanionReference) -> bool:
    if reference.resolved in content.files:
        return True
    if not (reference.directory or _is_extensionless(reference.resolved)):
        return False
    prefix = f"{reference.resolved}/"
    return any(carried.startswith(prefix) for carried in content.files)


def _is_extensionless(path: str) -> bool:
    return "." not in path.rsplit("/", 1)[-1]


def _missing_reference_reason(reference: _CompanionReference) -> str:
    target = reference.resolved or reference.raw
    if reference.directory:
        return f"it references {target}/, which the archive does not carry"
    if _is_extensionless(target) or _carried_suffix(target) is not None:
        return f"it references {target}, which the archive does not carry"
    return f"it references {target}, whose extension the installer does not carry"


def _resolve_companion(relative: str, reference: str) -> tuple[str, bool]:
    base = relative.rsplit("/", 1)[0] if "/" in relative else ""
    parts = [part for part in base.split("/") if part]
    for segment in reference.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if not parts:
                return "", True
            parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts), False


def _carried_suffix(path: str) -> str | None:
    _, dot, extension = path.rpartition(".")
    if not dot:
        return None
    suffix = f".{extension.lower()}"
    if suffix in _CARRIED_SUFFIXES:
        return suffix
    return None


def _reference_offset(item: tuple[int, _CompanionReference]) -> int:
    return item[0]


def _iter_references(text: str, relative: str) -> Iterator[_CompanionReference]:
    found: list[tuple[int, _CompanionReference]] = []
    for match in _REFERENCE_PATTERN.finditer(text):
        raw_code: object = match.group(1)
        raw_angle: object = match.group(2)
        raw_balanced: object = match.group(3)
        raw_plain: object = match.group(4)
        raw_definition: object = match.group(5)
        reference: _CompanionReference | None
        if isinstance(raw_code, str):
            reference = _code_reference(text, match, raw_code, relative)
        elif isinstance(raw_angle, str):
            reference = _destination_reference(raw_angle, relative)
        elif isinstance(raw_balanced, str):
            reference = _destination_reference(raw_balanced, relative)
        elif isinstance(raw_plain, str):
            reference = _destination_reference(raw_plain, relative)
        elif isinstance(raw_definition, str):
            reference = _destination_reference(raw_definition, relative)
        else:
            continue
        if reference is not None:
            found.append((match.start(), reference))
    for match in _HTML_ATTRIBUTE_PATTERN.finditer(text):
        raw: object = match.group(1)
        if raw is None:
            raw = match.group(2)
        if raw is None:
            raw = match.group(3)
        if not isinstance(raw, str):
            continue
        reference = _destination_reference(raw, relative)
        if reference is not None:
            found.append((match.start(), reference))
    for _position, reference in sorted(found, key=_reference_offset):
        yield reference


def _code_reference(
    text: str, match: re.Match[str], raw: str, relative: str
) -> _CompanionReference | None:
    directed = _is_directed_code_span(text, match)
    candidate = _code_candidate(raw)
    if candidate is None:
        if not directed:
            return None
        candidate = unquote(raw.strip())
        if not candidate:
            return None
    if _is_external_reference(candidate):
        return None
    directory = candidate.endswith("/")
    file_shaped = _is_file_reference(candidate)
    resolved, escapes = _resolve_companion(relative, candidate)
    escaping = candidate.startswith("../") and escapes and directory
    if not (directed or file_shaped or escaping):
        return None
    return _CompanionReference(
        raw=candidate, resolved=resolved, escapes=escapes, directory=directory
    )


def _destination_reference(raw: str, relative: str) -> _CompanionReference | None:
    candidate = _destination_candidate(raw)
    if candidate is None:
        return None
    if _is_external_reference(candidate):
        return None
    directory = candidate.endswith("/")
    resolved, escapes = _resolve_companion(relative, candidate)
    return _CompanionReference(
        raw=candidate, resolved=resolved, escapes=escapes, directory=directory
    )


def _is_directed_code_span(text: str, match: re.Match[str]) -> bool:
    if _has_location_cue(text, match.end()):
        return True
    return _has_imperative_cue(_line_prefix(text, match.start()))


def _has_location_cue(text: str, end: int) -> bool:
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[end:line_end].strip().lower().startswith(_LOCATION_CUE)


def _line_prefix(text: str, start: int) -> str:
    return text[text.rfind("\n", 0, start) + 1 : start]


def _has_imperative_cue(prefix: str) -> bool:
    text = prefix.rstrip()
    lowered = text.lower()
    for verb in _CUE_VERBS:
        for cue in (verb, f"{verb} the"):
            if not lowered.endswith(cue):
                continue
            begin = len(text) - len(cue)
            if begin > 0 and (text[begin - 1].isalnum() or text[begin - 1] in "-_"):
                continue
            return _is_clause_initial(text[:begin].rstrip())
    return False


def _is_clause_initial(before: str) -> bool:
    if not before:
        return True
    return not _strip_leading_adverb(_clause_tail(before).strip())


def _clause_tail(before: str) -> str:
    boundary = -1
    for character in _CLAUSE_BOUNDARIES:
        boundary = max(boundary, before.rfind(character))
    if boundary != -1:
        return _EMPHASIS_RUN_PATTERN.sub("", before[boundary + 1 :])
    if _EMPHASIS_RUN_PATTERN.search(before):
        return ""
    tokens = before.split()
    if tokens and _is_list_marker(tokens[-1]):
        return ""
    return before


def _is_list_marker(token: str) -> bool:
    if token in _LIST_MARKERS:
        return True
    return _NUMBERED_MARKER_PATTERN.fullmatch(token) is not None


def _strip_leading_adverb(segment: str) -> str:
    lowered = segment.lower()
    for adverb in _CUE_ADVERBS:
        if lowered == adverb:
            return ""
        if lowered.startswith(f"{adverb} "):
            return segment[len(adverb) + 1 :]
    return segment


def _code_candidate(raw: str) -> str | None:
    candidate = raw.strip()
    if not candidate or any(character.isspace() for character in candidate):
        return None
    return unquote(candidate)


def _destination_candidate(raw: str) -> str | None:
    destination = _destination_text(raw)
    if destination is None:
        return None
    path, _, _ = unquote(destination).partition("#")
    path = path.strip()
    if not path:
        return None
    return path


def _destination_text(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if text.startswith("<"):
        end = text.find(">")
        if end == -1:
            return None
        return text[1:end]
    return text.split(None, 1)[0]


def _is_file_reference(candidate: str) -> bool:
    segment = candidate.rsplit("/", 1)[-1]
    _, dot, extension = segment.rpartition(".")
    if not dot or not _FILE_NAME_PATTERN.match(segment):
        return False
    if extension.islower() or extension.isupper():
        return True
    return f".{extension.lower()}" in _CARRIED_SUFFIXES


def _is_external_reference(candidate: str) -> bool:
    if candidate.startswith(("/", "~", "#")):
        return True
    return _URL_SCHEME_PATTERN.match(candidate) is not None


def _mirror_installed(canonical_root: Path, installed: list[str], messages: list[str]) -> None:
    siblings = sibling_skills_roots()
    for name in installed:
        try:
            messages.extend(mirror_skill(canonical_root, name, siblings))
        except OSError as exc:
            messages.append(f"{name}: the sibling mirror failed ({type(exc).__name__})")


__all__ = [
    "ALLOWED_FILE_SUFFIXES",
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
    "UPSTREAM_REPO",
    "HttpClient",
    "HttpResponse",
    "UpdateOutcome",
    "UpstreamRedirectRefusedError",
    "UpstreamResponseTooLargeError",
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
