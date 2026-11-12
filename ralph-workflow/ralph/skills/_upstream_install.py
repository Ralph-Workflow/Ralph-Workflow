"""Extraction, staging and publication of the universal skill payloads.

The updater owns three primitives, and they live here so that the module
that owns the public contract stays small and focused:

EXTRACTION
    The release tarball is never handed to ``extractall``. Every member is
    inspected: absolute paths, ``..`` escapes, symlinks, hardlinks, device
    members, refused extensions, duplicate relative paths and the aggregate
    limits are all checked before a single byte is written.

STAGING
    A skill is staged COMPLETE -- every file plus the managed marker -- in a
    temporary staging directory inside the target skills root, and the
    staged content is validated against the manifest it carries.

PUBLICATION
    The staged directory is published with a rename, so the live directory
    is never half-written: it carries either the complete previous content
    or the complete new content. A failed publication restores the previous
    content, and a rollback restores every skill an attempt published before
    it failed.

The ownership rules are NOT re-implemented here: the marker shape, the file
manifest and the pristine-content check come from
:mod:`ralph.skills._ownership`, so there is exactly ONE implementation of
them in the repository.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ralph.skills._ownership import (
    MANAGED_MARKER_NAME,
    UNIVERSAL_MANAGED_BY,
    managed_content_matches,
    manifest_digest,
    may_replace,
    read_managed_marker,
    verified_recorded_files,
)

#: Prefix of the staging directory a skill is written into before it is
#: published. The prefix is hidden, so a staging directory never reads as a
#: skill of the skills root.
STAGING_PREFIX = ".ralph-staging-"

#: Suffix of the backup directory a publication moves the previous content
#: into. A process that dies between the two renames of a publication leaves
#: that backup behind; the next attempt restores it.
BACKUP_SUFFIX = ".ralph-backup"


class _ArchiveRejectedError(Exception):
    """Raised when the upstream archive violates the install contract."""


class _StagingError(OSError):
    """Raised when a staged skill directory does not match its manifest."""


@dataclass(frozen=True)
class _ArchiveLimits:
    """The limits one archive extraction runs under.

    Attributes:
        skill_names: The skills the archive may carry.
        allowed_suffixes: The file extensions a skill directory may carry.
        max_file_bytes: The cap of one extracted file.
        max_members: The cap of the member count of the archive.
        max_extracted_bytes: The cap of the total extracted size.
        max_skill_files: The cap of the file count of one skill directory.
    """

    skill_names: tuple[str, ...]
    allowed_suffixes: frozenset[str]
    max_file_bytes: int
    max_members: int
    max_extracted_bytes: int
    max_skill_files: int


@dataclass(frozen=True)
class _SkillPayload:
    """The validated content of one upstream skill directory.

    Attributes:
        files: Relative path inside the skill directory mapped to its bytes
            and its upstream permission bits.
    """

    files: dict[str, tuple[bytes, int]]


@dataclass(frozen=True)
class _StagedSkill:
    """One staged skill directory, ready to be published.

    Attributes:
        name: The name of the staged skill.
        staging: The staging directory that carries the complete skill.
        digest: The manifest digest of the staged content.
    """

    name: str
    staging: Path
    digest: str


def extract_archive(body: bytes, limits: _ArchiveLimits) -> dict[str, _SkillPayload]:
    """Return the validated content of every universal skill in the archive."""
    buckets: dict[str, list[tuple[str, tarfile.TarInfo]]] = {}
    member_count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for member in archive.getmembers():
                member_count += 1
                if member_count > limits.max_members:
                    raise _ArchiveRejectedError("the archive carries too many members")
                located = _locate_member(member, limits)
                if located is None:
                    continue
                skill, relative = located
                buckets.setdefault(skill, []).append((relative, member))
            payloads: dict[str, _SkillPayload] = {}
            extracted = 0
            for skill, entries in buckets.items():
                if len(entries) > limits.max_skill_files:
                    raise _ArchiveRejectedError(f"the {skill} skill carries too many files")
                payload, size = _build_payload(archive, entries, limits)
                extracted += size
                if extracted > limits.max_extracted_bytes:
                    raise _ArchiveRejectedError(
                        "the archive extracts more than the size cap allows"
                    )
                payloads[skill] = payload
    except tarfile.TarError as exc:
        raise _ArchiveRejectedError(f"the archive could not be read: {type(exc).__name__}") from exc
    if not payloads:
        raise _ArchiveRejectedError("the archive carries no universal skill directory")
    return payloads


def _locate_member(member: tarfile.TarInfo, limits: _ArchiveLimits) -> tuple[str, str] | None:
    """Return the skill name and relative path a member belongs to.

    The archive layout is located structurally: the member must carry a
    ``skills/<skill-name>/`` segment, so the top-level directory name is
    irrelevant (the upstream tag spelling and the archive directory name
    differ).
    """
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
    """Return True when ``relative`` leaves the skill directory."""
    parts = Path(relative).parts
    return Path(relative).is_absolute() or any(part in ("..", "") for part in parts)


def _build_payload(
    archive: tarfile.TarFile, entries: list[tuple[str, tarfile.TarInfo]], limits: _ArchiveLimits
) -> tuple[_SkillPayload, int]:
    """Return the validated payload of one skill directory and its size."""
    files: dict[str, tuple[bytes, int]] = {}
    total = 0
    for relative, member in entries:
        if relative in files:
            raise _ArchiveRejectedError(f"the archive carries {relative} twice")
        data = _read_member(archive, member, limits)
        total += len(data)
        files[relative] = (data, member.mode & 0o777)
    return (_SkillPayload(files=files), total)


def _read_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, limits: _ArchiveLimits
) -> bytes:
    """Return the bytes of one archive member, enforcing the size cap."""
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
    """Write the COMPLETE skill into a staging directory and validate it.

    The staging directory lives inside the target skills root, so the
    publication is a rename inside one filesystem. Nothing outside the
    staging directory is touched: a failure here removes the staging
    directory and leaves the previous installation byte-identical.
    """
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
    """Verify the staged directory carries exactly the manifest it claims."""
    if not managed_content_matches(staging, digests):
        raise _StagingError("the staged skill does not match its manifest")
    marker = read_managed_marker(staging)
    if marker is None:
        raise _StagingError("the staged skill carries no usable managed marker")
    recorded = verified_recorded_files(marker)
    if recorded is None or recorded != digests:
        raise _StagingError("the staged skill carries no usable managed marker")


def publish_staged(entry: _StagedSkill, live: Path) -> Path | None:
    """Publish the staged skill directory as the live one.

    The live directory is replaced by a rename, so it is never half-written:
    it carries either the complete previous content or the complete new
    content. A failed publication restores the previous content.

    Returns:
        The backup path of the replaced directory, or None when the live
        directory did not exist.
    """
    backup = backup_path(live)
    discard_tree(backup)
    replaced = False
    if live.exists() or live.is_symlink():
        live.replace(backup)  # filesystem-write-ok: atomic swap of the managed skill directory.
        replaced = True
    try:
        entry.staging.replace(live)  # filesystem-write-ok: atomic publication of the staged skill.
    except BaseException:
        if replaced:
            restore_backup(backup, live)
        raise
    return backup if replaced else None


def backup_path(live: Path) -> Path:
    """Return the backup path a publication moves ``live`` into."""
    return live.with_name(f".{live.name}{BACKUP_SUFFIX}")


def rollback(root: Path, published: list[tuple[str, Path | None]]) -> None:
    """Restore every skill an attempt published before it failed."""
    for name, backup in reversed(published):
        live = root / name
        if backup is None:
            discard_tree(live)
            continue
        restore_backup(backup, live)


def restore_backup(backup: Path, live: Path) -> None:
    """Put the previous installation back after a failed publication."""
    try:
        backup.replace(live)  # filesystem-write-ok: atomic restore of the previous installation.
        return
    except OSError:
        pass
    try:
        # filesystem-write-ok: restore the previous installation byte-identically when the atomic restore is unavailable.
        shutil.copytree(backup, live, dirs_exist_ok=True)
    except OSError:
        return
    discard_tree(backup)


def recover_publication(root: Path, name: str) -> None:
    """Restore a publication that a killed process left half-finished.

    A publication moves the previous content into the backup directory and
    then renames the staged content into place. A process that dies between
    the two renames leaves the backup behind and the live directory missing;
    the next attempt restores the previous content from the backup.
    """
    live = root / name
    if live.exists() or live.is_symlink():
        return
    backup = backup_path(live)
    if not backup.is_dir():
        return
    restore_backup(backup, live)


def sweep_staging(root: Path) -> None:
    """Remove every staging directory a killed process left behind.

    A staging directory belongs to the publication that is in flight, so the
    caller runs the sweep while it holds the publication lock: a staging
    directory of a LIVE publication is never in the root the sweep walks.
    """
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.name.startswith(STAGING_PREFIX):
            discard_tree(entry)


def discard_tree(path: Path) -> None:
    """Remove a directory this updater owns, ignoring a missing path."""
    # filesystem-write-ok: removal of a staging, backup or mirrored directory this updater owns
    shutil.rmtree(path, ignore_errors=True)


def discard_staged(staged: list[_StagedSkill]) -> None:
    """Remove every staging directory that was not published."""
    for entry in staged:
        discard_tree(entry.staging)


def ensure_dir(path: Path) -> None:
    """Create ``path`` and its parents when they are missing."""
    # filesystem-write-ok: managed skill and state directory creation in the allowed write set
    path.mkdir(parents=True, exist_ok=True)


def write_file(target: Path, data: bytes, mode: int) -> None:
    """Write one managed skill file, preserving the upstream mode bits."""
    ensure_dir(target.parent)
    # filesystem-write-ok: managed upstream skill content write inside the skill directory
    target.write_bytes(data)
    # filesystem-write-ok: upstream mode preservation for the managed skill content
    target.chmod(mode)


def write_json(path: Path, document: dict[str, object]) -> None:
    """Write a JSON document to ``path``."""
    ensure_dir(path.parent)
    # filesystem-write-ok: managed marker publication inside the skill directory
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def mirror_skill(
    canonical_root: Path, skill_name: str, sibling_roots: tuple[Path, ...]
) -> list[str]:
    """Mirror one installed skill directory into the registered sibling roots.

    A sibling entry is replaced ONLY when Ralph can prove it owns the entry:
    the shared ownership predicate must report it as MANAGED, or the entry
    must be a pristine mirror of the updater's OWN universal content. Every
    other entry is preserved and reported.

    Returns:
        The report lines for the sibling entries that were preserved.
    """
    canonical_dir = canonical_root / skill_name
    canonical_target = _resolved(canonical_dir)
    messages: list[str] = []
    for sibling_root in sibling_roots:
        resolved_root = _resolved(sibling_root)
        if resolved_root == _resolved(canonical_root):
            continue
        sibling_dir = sibling_root / skill_name
        if sibling_dir.is_symlink() and _resolved(sibling_dir) == canonical_target:
            continue
        if sibling_dir.exists() or sibling_dir.is_symlink():
            if not _may_replace_sibling(sibling_dir):
                messages.append(
                    f"{skill_name}: the sibling entry in {sibling_root} is preserved; "
                    "it is not provably Ralph-managed"
                )
                continue
            if sibling_dir.is_symlink():
                remove_file(sibling_dir)
            else:
                discard_tree(sibling_dir)
        ensure_dir(sibling_root)
        _link_skill(sibling_dir, canonical_dir)
    return messages


def _may_replace_sibling(entry: Path) -> bool:
    """Return True when Ralph may replace the sibling entry ``entry``.

    The shared ownership predicate is the gate: it authorises a replace ONLY
    for a MANAGED entry. A pristine mirror of the updater's OWN universal
    content is Ralph-managed as well, even though the shared predicate
    reports the universal marker as FOREIGN -- that marker belongs to this
    updater, not to the prune or the project-scope installer.
    """
    if may_replace(entry):
        return True
    return _is_own_mirror(entry)


def _is_own_mirror(entry: Path) -> bool:
    """Return True when ``entry`` is a pristine mirror of the updater's content."""
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
    """Point the sibling skill directory at the canonical one."""
    try:
        sibling_dir.symlink_to(canonical_dir, target_is_directory=True)
    except OSError:
        # filesystem-write-ok: fallback materialization of the mirrored skill content
        shutil.copytree(canonical_dir, sibling_dir)


def remove_file(path: Path) -> None:
    """Remove a file this updater installed in an earlier release."""
    # filesystem-write-ok: removal of a stale file installed by an earlier release
    path.unlink(missing_ok=True)


def _resolved(path: Path) -> Path:
    """Return the fully resolved form of ``path``."""
    return path.resolve()
