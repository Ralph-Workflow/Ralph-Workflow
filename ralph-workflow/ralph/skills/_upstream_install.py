from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import (
    write_bytes_if_changed,
    write_text_if_changed,
)
from ralph.skills._ownership import (
    MANAGED_MARKER_NAME,
    UNIVERSAL_MANAGED_BY,
    managed_content_matches,
    manifest_digest,
    may_replace,
    read_managed_marker,
    verified_recorded_files,
)

STAGING_PREFIX = ".ralph-staging-"

BACKUP_SUFFIX = ".ralph-backup"


class _ArchiveRejectedError(Exception):
    pass


class _StagingError(OSError):
    pass


@dataclass(frozen=True)
class _ArchiveLimits:
    skill_names: tuple[str, ...]
    allowed_suffixes: frozenset[str]
    max_file_bytes: int
    max_members: int
    max_extracted_bytes: int
    max_skill_files: int


@dataclass(frozen=True)
class _SkillPayload:
    files: dict[str, tuple[bytes, int]]


@dataclass(frozen=True)
class _StagedSkill:
    name: str
    staging: Path
    digest: str


@dataclass(frozen=True)
class _MirrorSettlement:
    status: str
    repaired: tuple[str, ...]
    preserved: tuple[str, ...]
    messages: tuple[str, ...]


def extract_archive(body: bytes, limits: _ArchiveLimits) -> dict[str, _SkillPayload]:
    buckets: dict[str, list[tuple[str, tarfile.TarInfo]]] = {}
    member_count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > limits.max_members:
                    raise _ArchiveRejectedError("the archive carries too many members")
                located = _locate_member(member, limits)
                if located is None:
                    continue
                skill, relative = located
                if member.size > limits.max_file_bytes:
                    raise _ArchiveRejectedError("the archive carries a file above the size cap")
                buckets.setdefault(skill, []).append((relative, member))
            payloads: dict[str, _SkillPayload] = {}
            extracted = 0
            for skill, entries in buckets.items():
                if len(entries) > limits.max_skill_files:
                    raise _ArchiveRejectedError(f"the {skill} skill carries too many files")
                payload, size = _build_payload(
                    archive, entries, limits, limits.max_extracted_bytes - extracted
                )
                extracted += size
                payloads[skill] = payload
    except tarfile.TarError as exc:
        raise _ArchiveRejectedError(f"the archive could not be read: {type(exc).__name__}") from exc
    if not payloads:
        raise _ArchiveRejectedError("the archive carries no universal skill directory")
    return payloads


def _locate_member(member: tarfile.TarInfo, limits: _ArchiveLimits) -> tuple[str, str] | None:
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
    if skill not in limits.skill_names:
        return None
    relative = "/".join(segments[index + 2 :])
    if _escapes_skill_dir(relative):
        raise _ArchiveRejectedError("the archive carries a member path that escapes its directory")
    if Path(relative).suffix.lower() not in limits.allowed_suffixes:
        raise _ArchiveRejectedError(f"the archive carries {relative} with a refused extension")
    return (skill, relative)


def _escapes_skill_dir(relative: str) -> bool:
    parts = Path(relative).parts
    return Path(relative).is_absolute() or any(part in ("..", "") for part in parts)


def _build_payload(
    archive: tarfile.TarFile,
    entries: list[tuple[str, tarfile.TarInfo]],
    limits: _ArchiveLimits,
    remaining_bytes: int,
) -> tuple[_SkillPayload, int]:
    files: dict[str, tuple[bytes, int]] = {}
    total = 0
    for relative, member in entries:
        if relative in files:
            raise _ArchiveRejectedError(f"the archive carries {relative} twice")
        data = _read_member(archive, member, limits)
        total += len(data)
        if total > remaining_bytes:
            raise _ArchiveRejectedError("the archive extracts more than the size cap allows")
        files[relative] = (data, member.mode & 0o777)
    return (_SkillPayload(files=files), total)


def _read_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, limits: _ArchiveLimits
) -> bytes:
    if member.size > limits.max_file_bytes:
        raise _ArchiveRejectedError("the archive carries a file above the size cap")
    handle = archive.extractfile(member)
    if handle is None:
        raise _ArchiveRejectedError("the archive carries an unreadable file")
    with handle:
        data = handle.read()
    if len(data) > limits.max_file_bytes:
        raise _ArchiveRejectedError("the archive carries a file above the size cap")
    return data


def stage_skill(
    root: Path, name: str, content: _SkillPayload, tag: str, *, source: str
) -> _StagedSkill:
    staging = root / f"{STAGING_PREFIX}{name}-{os.urandom(6).hex()}"
    digests: dict[str, str] = {}
    try:
        ensure_dir(staging)
        for relative, (data, mode) in sorted(content.files.items()):
            write_file(staging / relative, data, mode)
            digests[relative] = hashlib.sha256(data).hexdigest()
        digest = manifest_digest(digests)
        marker: dict[str, object] = {
            "managed_by": UNIVERSAL_MANAGED_BY,
            "source": source,
            "source_tag": tag,
            "installed_sha256": digest,
            "files": digests,
        }
        write_json(staging / MANAGED_MARKER_NAME, marker)
        _validate_staged(staging, digests)
    except BaseException:
        discard_tree(staging)
        raise
    return _StagedSkill(name=name, staging=staging, digest=digest)


def _validate_staged(staging: Path, digests: dict[str, str]) -> None:
    if not managed_content_matches(staging, digests):
        raise _StagingError("the staged skill does not match its manifest")
    marker = read_managed_marker(staging)
    if marker is None:
        raise _StagingError("the staged skill carries no usable managed marker")
    recorded = verified_recorded_files(marker)
    if recorded is None or recorded != digests:
        raise _StagingError("the staged skill carries no usable managed marker")


def publish_staged(entry: _StagedSkill, live: Path) -> Path | None:
    backup = backup_path(live)
    discard_tree(backup)
    replaced = False
    if live.exists() or live.is_symlink():
        live.replace(backup)
        replaced = True
    try:
        entry.staging.replace(live)
    except BaseException:
        if replaced:
            restore_backup(backup, live)
        raise
    return backup if replaced else None


def backup_path(live: Path) -> Path:
    return live.with_name(f".{live.name}{BACKUP_SUFFIX}")


def rollback(root: Path, published: list[tuple[str, Path | None]]) -> None:
    for name, backup in reversed(published):
        live = root / name
        if backup is None:
            discard_tree(live)
            continue
        restore_backup(backup, live)


def restore_backup(backup: Path, live: Path) -> None:
    if not (backup.exists() or backup.is_symlink()):
        return
    discard_tree(live)
    if live.exists() or live.is_symlink():
        return
    try:
        backup.replace(live)
        return
    except OSError:
        pass
    try:
        shutil.copytree(backup, live)
    except OSError:
        return
    discard_tree(backup)


def recover_publication(root: Path, name: str) -> None:
    live = root / name
    if live.exists() or live.is_symlink():
        return
    backup = backup_path(live)
    if not backup.is_dir():
        return
    restore_backup(backup, live)


def sweep_staging(root: Path) -> None:
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.name.startswith(STAGING_PREFIX):
            discard_tree(entry)


def discard_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        remove_file(path)
        return
    shutil.rmtree(path, ignore_errors=True)


def discard_staged(staged: list[_StagedSkill]) -> None:
    for entry in staged:
        discard_tree(entry.staging)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_file(target: Path, data: bytes, mode: int) -> None:
    ensure_dir(target.parent)
    write_bytes_if_changed(DEFAULT_FILE_BACKEND, target, data)
    target.chmod(mode)


def write_json(path: Path, document: dict[str, object]) -> None:
    ensure_dir(path.parent)
    write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        path,
        json.dumps(document, indent=2) + "\n",
        encoding="utf-8",
    )


def mirror_skill(
    canonical_root: Path, skill_name: str, sibling_roots: tuple[Path, ...]
) -> tuple[list[str], list[str]]:
    messages: list[str] = []
    failed_roots: list[str] = []
    for sibling_root in sibling_roots:
        try:
            messages.extend(mirror_skill_into(canonical_root, skill_name, sibling_root))
        except OSError as exc:
            failed_roots.append(str(sibling_root))
            messages.append(
                f"{skill_name}: the sibling mirror in {sibling_root} failed ({type(exc).__name__})"
            )
    return messages, failed_roots


def mirror_skill_into(canonical_root: Path, skill_name: str, sibling_root: Path) -> list[str]:
    canonical_dir = canonical_root / skill_name
    canonical_target = _resolved(canonical_dir)
    messages: list[str] = []
    resolved_root = _resolved(sibling_root)
    if resolved_root == _resolved(canonical_root):
        return messages
    sibling_dir = sibling_root / skill_name
    if sibling_dir.is_symlink() and _resolved(sibling_dir) == canonical_target:
        return messages
    if sibling_dir.exists() or sibling_dir.is_symlink():
        if not _may_replace_sibling(sibling_dir):
            messages.append(
                f"{skill_name}: the sibling entry in {sibling_root} is preserved; "
                "it is not provably Ralph-managed"
            )
            return messages
        if sibling_dir.is_symlink():
            remove_file(sibling_dir)
        else:
            discard_tree(sibling_dir)
    ensure_dir(sibling_root)
    _link_skill(sibling_dir, canonical_dir)
    return messages


def publish_sibling_mirrors(
    canonical_root: Path, skill_names: tuple[str, ...], sibling_roots: tuple[Path, ...]
) -> tuple[list[str], list[str]]:
    messages: list[str] = []
    failed_skills: list[str] = []
    for skill_name in skill_names:
        skill_messages, failed_roots = mirror_skill(canonical_root, skill_name, sibling_roots)
        messages.extend(skill_messages)
        if failed_roots:
            failed_skills.append(skill_name)
    return messages, failed_skills


def settle_mirrors(
    canonical_root: Path,
    skill_names: tuple[str, ...],
    sibling_roots: tuple[Path, ...],
    tag: str,
) -> _MirrorSettlement:
    repaired: list[str] = []
    preserved: list[str] = []
    missing: list[str] = []
    messages: list[str] = []
    for skill_name in skill_names:
        canonical_dir = canonical_root / skill_name
        canonical_digest = _managed_digest(canonical_dir)
        if canonical_digest is None:
            if not canonical_dir.exists() and _mirror_entry_exists(sibling_roots, skill_name):
                missing.append(skill_name)
                messages.append(
                    f"{skill_name}: the canonical installation is missing and cannot be mirrored"
                )
            continue
        for sibling_root in sibling_roots:
            if _mirror_is_current(canonical_dir, sibling_root / skill_name, canonical_digest):
                continue
            try:
                root_messages = mirror_skill_into(canonical_root, skill_name, sibling_root)
            except OSError as exc:
                missing.append(skill_name)
                messages.append(
                    f"{skill_name}: the sibling mirror in {sibling_root} failed "
                    f"({type(exc).__name__})"
                )
                continue
            if root_messages:
                preserved.append(skill_name)
                messages.extend(root_messages)
                continue
            repaired.append(skill_name)
    if missing:
        status = "incomplete"
        names = ", ".join(_unique_names(missing))
        summary = f"the universal skill mirrors are incomplete ({tag}): {names}"
    elif repaired:
        status = "repaired"
        summary = f"the universal skill mirrors were restored ({tag})"
    else:
        status = "up-to-date"
        summary = f"the universal skills are up to date ({tag})"
    messages.append(summary)
    return _MirrorSettlement(
        status=status,
        repaired=_unique_names(repaired),
        preserved=_unique_names(preserved),
        messages=tuple(messages),
    )


def _mirror_entry_exists(sibling_roots: tuple[Path, ...], skill_name: str) -> bool:
    return any(
        (root / skill_name).exists() or (root / skill_name).is_symlink() for root in sibling_roots
    )


def _unique_names(values: list[str]) -> tuple[str, ...]:
    unique: dict[str, None] = {}
    for value in values:
        unique.setdefault(value, None)
    return tuple(unique)


def _mirror_is_current(canonical_dir: Path, sibling_dir: Path, canonical_digest: str) -> bool:
    if sibling_dir.is_symlink():
        return _resolved(sibling_dir) == _resolved(canonical_dir)
    return _managed_digest(sibling_dir) == canonical_digest


def _managed_digest(skill_dir: Path) -> str | None:
    marker = read_managed_marker(skill_dir)
    if marker is None or marker.get("managed_by") != UNIVERSAL_MANAGED_BY:
        return None
    recorded = verified_recorded_files(marker)
    if recorded is None or not managed_content_matches(skill_dir, recorded):
        return None
    digest = marker.get("installed_sha256")
    return digest if isinstance(digest, str) else None


def _may_replace_sibling(entry: Path) -> bool:
    if may_replace(entry):
        return True
    return _is_own_mirror(entry)


def _is_own_mirror(entry: Path) -> bool:
    if entry.is_symlink() or not entry.is_dir():
        return False
    marker = read_managed_marker(entry)
    if marker is None or marker.get("managed_by") != UNIVERSAL_MANAGED_BY:
        return False
    recorded = verified_recorded_files(marker)
    if recorded is None:
        return False
    return managed_content_matches(entry, recorded)


def _link_skill(sibling_dir: Path, canonical_dir: Path) -> None:
    try:
        sibling_dir.symlink_to(canonical_dir, target_is_directory=True)
    except OSError:
        shutil.copytree(canonical_dir, sibling_dir)


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _resolved(path: Path) -> Path:
    return path.resolve()
