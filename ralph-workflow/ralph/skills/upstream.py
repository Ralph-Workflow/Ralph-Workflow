"""Runtime updater for the universal skills published by ``obra/superpowers``.

The three universal skills (``test-driven-development``,
``systematic-debugging`` and ``verification-before-completion``) are installed
USER-GLOBAL: into the canonical user-global skill root and into every
registered sibling root. They are never written into the project tree.

The content comes from ONE upstream origin: the ``obra/superpowers`` release
tags. The release API names the newest tag; the matching ``codeload``
tarball carries the whole skill directory, so the ``SKILL.md`` AND every
companion file (``find-polluter.sh``, ``condition-based-waiting-example.ts``,
...) are installed together.

CONTRACT
========

:func:`maybe_update_universal_skills` is the single public entry point. It
NEVER raises: every failure is reported through the returned
:class:`UpdateOutcome`. The caller owns the kill switch (the ``enabled``
argument) and the clock (the ``now`` argument); this module reads no
configuration file and applies no policy of its own.

TRANSPORT
=========

The production transport is BOUNDED. It NEVER follows a redirect: a 3xx
answer is a refusal, because the upstream origin is the only origin this
module talks to. A response that declares a ``Content-Length`` above its cap
is refused before the body is read, and a body that grows past its cap WHILE
IT STREAMS aborts the transfer mid-flight. The cap is
:data:`MAX_RELEASE_BYTES` for the release JSON and :data:`MAX_ARCHIVE_BYTES`
for the release archive. The timeout covers the connect AND the read phase.
The :data:`HttpClient` seam stays injectable, so the whole path can be
exercised without a socket.

CADENCE
=======

At most ONE upstream attempt per 24 h. The attempt is RESERVED in the attempt
cache BEFORE the first request, so a failed attempt still consumes the window
and two concurrent starts still make exactly one attempt. The reservation is
INTER-PROCESS safe: the cache is guarded by an exclusive advisory lock on a
sidecar lock file, so two racing PROCESSES still make exactly one attempt.
The cache lives at ``$XDG_CACHE_HOME/ralph-workflow/skill-updates.json``
(falling back to ``~/.cache/ralph-workflow/skill-updates.json``) and is
written atomically through a temp file plus ``os.replace``.

TRANSACTIONAL INSTALL
=====================

Every payload is validated BEFORE anything is written: a payload without a
``SKILL.md`` is refused, a markdown reference must resolve to an EXACT
relative path of an installed file, and the archive as a whole is capped in
member count and in total extracted bytes.

The install itself is transactional. Each skill directory is staged in
COMPLETE (every file plus the managed marker) inside a temporary staging
directory in the target skills root, validated there, and then published
with an atomic rename. A failure removes ONLY the staging directory and
leaves the previous installation byte-identical; a failure after an earlier
skill was published rolls that skill back. The installed-tag state file is
written only after every skill published successfully. A publication that a
killed process left half-finished is restored on the next start, BEFORE the
attempt window is consulted, and the staging directories such a process left
behind are reclaimed.

WRITE CONTAINMENT
=================

Skill CONTENT is written ONLY under ``<user-global-root>/<skill-name>/`` of
the canonical root and of every registered sibling root. The only other
writes are the two state files: the installed-tag state file
(``~/.config/ralph-workflow-universal-skills.json``) and the attempt cache
with its lock sidecars. Nothing else is written anywhere, and the project's
git tree is never touched.

USER EDITS AND OWNERSHIP
=========================

A skill directory Ralph manages carries a ``.ralph-managed.json`` marker that
records the sha256 of every installed file. Before an existing directory is
overwritten, the on-disk digests are compared with the marker's manifest: a
mismatch means the USER edited the skill, so the directory is left untouched
and reported as preserved. A directory without a readable Ralph marker is
treated as user-owned and preserved as well. The marker shape, the file
manifest and the pristine-content check come from
:mod:`ralph.skills._ownership`, so there is exactly ONE implementation of
them in the repository.

SIBLING MIRROR
==============

The sibling roots mirror the canonical installation with a symlink. A sibling
entry that is not provably Ralph-managed is PRESERVED and reported: the
mirror never removes an entry it cannot prove it owns.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph.checked_accessors import as_mapping, as_str_dict, optional_str
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

#: The file every universal skill directory must carry.
SKILL_FILE_NAME = "SKILL.md"

#: ``managed_by`` value recorded in the marker of a universal skill.
MANAGED_BY = UNIVERSAL_MANAGED_BY

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024

#: Maximum size of the release API response, in bytes.
MAX_RELEASE_BYTES = 1024 * 1024

#: Maximum size of a single extracted file, in bytes.
MAX_FILE_BYTES = 1024 * 1024

#: Maximum number of members the release archive may carry.
MAX_ARCHIVE_MEMBERS = 2048

#: Maximum total size of every extracted file of the release archive, in bytes.
MAX_EXTRACTED_BYTES = 32 * 1024 * 1024

#: Maximum number of files one skill directory may carry.
MAX_SKILL_FILES = 512

#: File extensions the installer accepts inside a skill directory.
ALLOWED_FILE_SUFFIXES: frozenset[str] = frozenset(
    {".json", ".md", ".py", ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml"}
)

REQUEST_TIMEOUT_SECONDS = 5.0

ATTEMPT_WINDOW = timedelta(hours=24)

CACHE_FILE_NAME = "skill-updates.json"

#: Suffix of the lock file that guards the attempt cache across processes.
ATTEMPT_LOCK_SUFFIX = ".lock"

#: Name of the lock file that guards the publication across processes.
PUBLICATION_LOCK_NAME = "skill-publish.lock"

#: State file name, relative to ``~/.config``.
STATE_FILE_NAME = "ralph-workflow-universal-skills.json"

type HttpResponse = tuple[int, bytes]

type HttpClient = Callable[[str, float], HttpResponse]

HTTP_OK = 200

_ATTEMPT_LOCK = threading.Lock()

_REFERENCE_PATTERN = re.compile(r"`([^`\n]+)`|\]\(([^)\n]+)\)")

_FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9]{1,8}$")

_TAG_NUMBER_PATTERN = re.compile(r"\d+")


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
    """The skills one release may install, after every payload was validated.

    Attributes:
        tag: The upstream release tag the plan belongs to.
        skills: The skills to install, in installation order.
        preserved: The skills the plan leaves untouched.
        messages: Human-readable report lines for the preserved skills.
    """

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
    """Return the production HTTP client for the upstream seam.

    The client is a callable that takes ``(url, timeout)`` and returns a
    ``(status_code, body)`` pair. The transport is BOUNDED:

    * a redirect is REFUSED, never followed;
    * a declared ``Content-Length`` above the cap of the URL is refused
      before the body is read;
    * a body that grows past the cap WHILE IT STREAMS aborts the transfer;
    * the timeout covers the connect AND the read phase.

    Transport failures propagate to the caller, which swallows them and
    reports a failed outcome.
    """
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
    """Return the lock-file path that guards the attempt cache."""
    cache = attempt_cache_path()
    return cache.with_name(cache.name + ATTEMPT_LOCK_SUFFIX)


def publication_lock_path() -> Path:
    """Return the lock-file path that guards the publication."""
    return attempt_cache_path().parent / PUBLICATION_LOCK_NAME


def universal_state_path() -> Path:
    return Path.home() / ".config" / STATE_FILE_NAME


def _response_byte_cap(url: str) -> int:
    """Return the byte cap that applies to ``url``.

    The release API answers with a small JSON document, the archive with the
    release tarball. An unknown URL falls back to the SMALLER cap: an
    unknown origin is refused conservatively, never generously.
    """
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
    """Reserve the 24 h attempt window.

    The reservation is INTER-PROCESS safe: the cache is guarded by an
    exclusive advisory lock on a sidecar lock file, so two racing PROCESSES
    produce exactly ONE upstream attempt. The lock file is advisory: where
    the platform cannot lock it, the in-process lock still orders the
    threads of one process.

    Returns:
        True when this call owns the attempt, False when an attempt already
        happened inside the window.
    """
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
    """Hold an exclusive advisory lock on ``lock_path``.

    The lock is an ``fcntl`` advisory lock, so two racing PROCESSES
    serialize on it. A platform that cannot lock the file degrades to the
    in-process ordering of the caller.
    """
    _ensure_dir(lock_path.parent)
    descriptor = os.open(  # filesystem-write-ok: the advisory-lock sidecar carries no content; it exists only so flock() can serialize the attempt reservation.  # resource-lifecycle-ok: the descriptor is closed in this manager's finally block, so it never outlives the with-block.
        lock_path,
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError:
            # The platform cannot lock the file: the in-process lock still
            # orders the threads of this process.
            yield
            return
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


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
    """Return the machine-readable reason of a transport failure."""
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
    """Return the limits one archive extraction runs under."""
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
    """Restore the publications a killed process left half-finished.

    The repair runs BEFORE the attempt window is consulted: a publication
    that was interrupted must not wait for the next daily attempt to be
    repaired, and the staging directories such a process left behind are
    reclaimed with it.
    """
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
    """Return the skills a release may install, after validating every payload.

    Every payload is validated BEFORE the first write: a payload without a
    ``SKILL.md``, a payload with a dangling markdown reference and a
    directory the user edited are all preserved and reported here.
    """
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
    """Return why a skill payload must not be installed, or None."""
    if SKILL_FILE_NAME not in content.files:
        return f"the payload carries no {SKILL_FILE_NAME}"
    return _missing_reference(content)


def _publish_plan(
    canonical_root: Path, plan: _InstallPlan
) -> tuple[list[str], dict[str, str], str | None]:
    """Stage and publish every planned skill; roll the whole plan back on failure.

    Every failure rolls the plan back: the skills this attempt published are
    restored, and the staging directories it created are removed. A failure
    that is not an I/O failure (an interrupt, an exit) is re-raised after the
    rollback, so the caller sees it and the installation stays intact.

    Returns:
        The published skill names, their manifest digests and a failure
        message. The failure message is None when the plan published.
    """
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
    """Return why an existing skill directory must not be overwritten.

    The marker shape, the file manifest and the pristine-content check come
    from :mod:`ralph.skills._ownership`, so this decision and the sibling
    mirror's decision share ONE implementation.
    """
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
    """Return a reason when the skill references a file that is not installed.

    A reference matches an installed file by its EXACT relative path: a
    reference to ``docs/example.ts`` is satisfied only by the file at
    ``docs/example.ts``, and a reference to ``example.ts`` only by the
    top-level ``example.ts``.
    """
    for relative in sorted(content.files):
        if not relative.endswith(".md"):
            continue
        data = content.files[relative][0]
        for reference in _iter_references(data.decode("utf-8", errors="replace")):
            if reference in content.files:
                continue
            if Path(reference).suffix.lower() in ALLOWED_FILE_SUFFIXES:
                return f"it references {reference}, which the archive does not carry"
    return None


def _iter_references(text: str) -> Iterator[str]:
    """Yield the relative file paths the markdown text references."""
    for match in _REFERENCE_PATTERN.finditer(text):
        token: str = match.group(1) or match.group(2) or ""
        candidate = token.strip()
        if not candidate or " " in candidate:
            continue
        if candidate.startswith(("http://", "https://", "/", "~", "#")):
            continue
        parts = [part for part in candidate.split("/") if part]
        if not parts or any(part == ".." for part in parts):
            continue
        if not _FILE_NAME_PATTERN.match(parts[-1]):
            continue
        yield "/".join(parts)


def _mirror_installed(canonical_root: Path, installed: list[str], messages: list[str]) -> None:
    """Mirror every installed skill directory into the sibling roots."""
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
