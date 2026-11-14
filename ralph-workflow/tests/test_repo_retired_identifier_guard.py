from __future__ import annotations

import importlib.resources
import json
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.resources.abc import Traversable

RETIRED_IDENT: str = "commit_" + "cleanup"
RETIRED_HYPHEN_IDENT: str = "commit-" + "cleanup"
FORBIDDEN_TEXT_STRINGS: tuple[str, ...] = (RETIRED_IDENT, RETIRED_HYPHEN_IDENT)
FORBIDDEN_BYTE_STRINGS: tuple[bytes, ...] = tuple(
    text.encode("utf-8") for text in FORBIDDEN_TEXT_STRINGS
)

ARCHIVE_PREFIXES: tuple[str, ...] = (
    ".pi-subagents/artifacts/",
    "ralph-workflow/tests/agents/invoke/fixtures/",
)

_SENTINEL_TRACKED_PATH = "ralph-workflow/checkpoint.json"

_INDEX_SIGNATURE = b"DIRC"
_INDEX_HEADER_BYTES = 12
_INDEX_ENTRY_FIXED_BYTES = 62
_INDEX_CE_EXTENDED = 0x4000
_INDEX_CE_NAMEMASK = 0x0FFF
_INDEX_SPARSE_DIR_MODE = 0o040000
_INDEX_SPLIT_LINK_SIGNATURE = b"link"
_INDEX_SPARSE_SIGNATURE = b"sdir"
_INDEX_TRAILER_DIGEST_BYTES = 20


def _is_historical_archive(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in ARCHIVE_PREFIXES)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    raise AssertionError("repository root not found")


def _git_dir(repo_root: Path) -> Path:
    dot_git = repo_root / ".git"
    if dot_git.is_dir():
        return dot_git
    pointer = dot_git.read_text(encoding="utf-8").strip()
    prefix = "gitdir:"
    if not pointer.startswith(prefix):
        raise AssertionError(f"unrecognized git directory pointer: {pointer!r}")
    target = Path(pointer[len(prefix) :].strip())
    if not target.is_absolute():
        target = repo_root / target
    return target


def _parse_index_entries(data: bytes) -> tuple[tuple[str, ...], int]:
    if data[:4] != _INDEX_SIGNATURE:
        raise AssertionError("git index signature is missing")
    version, entry_count = struct.unpack(">II", data[4:_INDEX_HEADER_BYTES])
    if version not in (2, 3):
        raise AssertionError(f"unsupported git index version {version}; expected 2 or 3")
    offset = _INDEX_HEADER_BYTES
    paths: list[str] = []
    for _ in range(entry_count):
        entry_start = offset
        flags = struct.unpack(">H", data[offset + 60 : offset + 62])[0]
        mode = struct.unpack(">I", data[offset + 24 : offset + 28])[0]
        offset += _INDEX_ENTRY_FIXED_BYTES
        if flags & _INDEX_CE_EXTENDED:
            offset += 2
        name_length = flags & _INDEX_CE_NAMEMASK
        if name_length == _INDEX_CE_NAMEMASK:
            name_end = data.index(b"\0", offset)
            name = data[offset:name_end]
            offset = name_end
        else:
            name = data[offset : offset + name_length]
            offset += name_length
        offset = entry_start + ((offset - entry_start + 8) & ~7)
        if mode == _INDEX_SPARSE_DIR_MODE:
            raise AssertionError("sparse git index is not supported")
        paths.append(name.decode("utf-8", errors="surrogateescape"))
    _assert_index_trailer(data, offset)
    return tuple(paths), version


def _assert_index_trailer(data: bytes, offset: int) -> None:
    trailer = data[offset:]
    if len(trailer) < _INDEX_TRAILER_DIGEST_BYTES:
        raise AssertionError("git index trailer is missing")
    body = trailer[:-_INDEX_TRAILER_DIGEST_BYTES]
    while body:
        if len(body) < 8:
            raise AssertionError("git index extension header is truncated")
        signature = body[:4]
        size = struct.unpack(">I", body[4:8])[0]
        if signature == _INDEX_SPLIT_LINK_SIGNATURE:
            raise AssertionError("split git index is not supported")
        if signature == _INDEX_SPARSE_SIGNATURE:
            raise AssertionError("sparse git index is not supported")
        body = body[8 + size :]


def _tracked_paths(repo_root: Path) -> tuple[str, ...]:
    index_path = _git_dir(repo_root) / "index"
    if not index_path.is_file():
        raise AssertionError(f"git index is missing: {index_path}")
    paths, _version = _parse_index_entries(index_path.read_bytes())
    return paths


def _scan_tracked_files(repo_root: Path, tracked_rel_paths: Sequence[str]) -> list[str]:
    violations: list[str] = []
    for rel_path in tracked_rel_paths:
        normalized = rel_path.replace("\\", "/")
        if _is_historical_archive(normalized):
            continue
        for forbidden in FORBIDDEN_TEXT_STRINGS:
            if forbidden in normalized:
                violations.append(f"{normalized}: forbidden identifier in tracked path")
        file_path = repo_root / rel_path
        if not file_path.is_file():
            continue
        content = file_path.read_bytes()
        for forbidden in FORBIDDEN_BYTE_STRINGS:
            if forbidden in content:
                violations.append(f"{normalized}: forbidden identifier in tracked content")
    return violations


def _scan_traversable(traversable: Traversable, rel_path: str = "") -> list[str]:
    current_path = f"{rel_path}/{traversable.name}" if rel_path else traversable.name
    violations: list[str] = []
    for forbidden in FORBIDDEN_TEXT_STRINGS:
        if forbidden in traversable.name:
            violations.append(f"{current_path}: forbidden identifier in packaged path")
    if traversable.is_file():
        content = traversable.read_bytes()
        for forbidden in FORBIDDEN_BYTE_STRINGS:
            if forbidden in content:
                violations.append(f"{current_path}: forbidden identifier in packaged content")
    elif traversable.is_dir():
        for child in traversable.iterdir():
            violations.extend(_scan_traversable(child, current_path))
    return violations


def _scan_schema(data: object) -> list[str]:
    violations: list[str] = []
    if isinstance(data, str):
        for forbidden in FORBIDDEN_TEXT_STRINGS:
            if forbidden in data:
                violations.append(f"string match: {data}")
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str):
                for forbidden in FORBIDDEN_TEXT_STRINGS:
                    if forbidden in key:
                        violations.append(f"key match: {key}")
            violations.extend(_scan_schema(value))
    elif isinstance(data, list):
        for item in data:
            violations.extend(_scan_schema(item))
    return violations


def _synthetic_index(entries: Sequence[str], *, version: int = 2) -> bytes:
    body = b""
    for entry in entries:
        raw = b"\0" * 60 + len(entry).to_bytes(2, "big") + entry.encode("utf-8")
        padded = raw + b"\0" * (((len(raw) + 8) & ~7) - len(raw))
        body += padded
    header = _INDEX_SIGNATURE + struct.pack(">II", version, len(entries))
    return header + body + b"\0" * _INDEX_TRAILER_DIGEST_BYTES


def test_tracked_inventory_is_read_from_the_git_index() -> None:
    tracked = _tracked_paths(_repo_root())

    assert _SENTINEL_TRACKED_PATH in tracked
    assert len(tracked) > 1


def test_no_retired_identifier_in_tracked_inventory() -> None:
    repo_root = _repo_root()
    violations = _scan_tracked_files(repo_root, _tracked_paths(repo_root))
    assert not violations, "Forbidden identifier found:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "package_name",
    ["ralph.policy.defaults", "ralph.prompts.templates"],
)
def test_no_retired_identifier_in_packaged_resources(package_name: str) -> None:
    violations = _scan_traversable(importlib.resources.files(package_name))
    assert not violations, f"Forbidden identifier found in {package_name}: {violations}"


def test_no_retired_identifier_in_checkpoint_schema_surface() -> None:
    schema = PipelineState.model_json_schema()
    violations = _scan_schema(schema)
    assert not violations, f"Forbidden identifier found in checkpoint schema: {violations}"
    serialized = json.dumps(schema)
    assert not any(value in serialized for value in FORBIDDEN_TEXT_STRINGS)

    checkpoint_sample = _repo_root() / _SENTINEL_TRACKED_PATH
    sample = json.loads(checkpoint_sample.read_text(encoding="utf-8"))
    sample_violations = _scan_schema(sample)
    assert not sample_violations, (
        f"Forbidden identifier found in sample checkpoint: {sample_violations}"
    )


def test_tracked_scanner_regression_detects_hidden_directory_content(tmp_path: Path) -> None:
    hidden_path = ".opencode/skills/current.md"
    target = tmp_path / hidden_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"phase = " + FORBIDDEN_BYTE_STRINGS[0])

    violations = _scan_tracked_files(tmp_path, [hidden_path])

    assert violations == [f"{hidden_path}: forbidden identifier in tracked content"]


def test_tracked_scanner_regression_detects_hidden_dot_file_content(tmp_path: Path) -> None:
    hidden_path = ".codex/config.toml"
    target = tmp_path / hidden_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"phase = " + FORBIDDEN_BYTE_STRINGS[1])

    violations = _scan_tracked_files(tmp_path, [hidden_path])

    assert violations == [f"{hidden_path}: forbidden identifier in tracked content"]


def test_tracked_scanner_regression_detects_tracked_filename(tmp_path: Path) -> None:
    retired_filename = f"tests/{RETIRED_IDENT}.txt"
    target = tmp_path / retired_filename
    target.parent.mkdir(parents=True)
    target.write_text("current content", encoding="utf-8")

    violations = _scan_tracked_files(tmp_path, [retired_filename])

    assert violations == [f"{retired_filename}: forbidden identifier in tracked path"]


def test_tracked_scanner_regression_detects_hidden_tracked_filename(tmp_path: Path) -> None:
    retired_filename = f".opencode/{RETIRED_HYPHEN_IDENT}.md"
    target = tmp_path / retired_filename
    target.parent.mkdir(parents=True)
    target.write_text("current content", encoding="utf-8")

    violations = _scan_tracked_files(tmp_path, [retired_filename])

    assert violations == [f"{retired_filename}: forbidden identifier in tracked path"]


def test_tracked_scanner_still_reports_clean_inventory(tmp_path: Path) -> None:
    clean_path = ".opencode/skills/current.md"
    target = tmp_path / clean_path
    target.parent.mkdir(parents=True)
    target.write_text("phase = review_commit\n", encoding="utf-8")

    assert _scan_tracked_files(tmp_path, [clean_path]) == []


def test_only_historical_archive_prefixes_are_exempt() -> None:
    assert ARCHIVE_PREFIXES == (
        ".pi-subagents/artifacts/",
        "ralph-workflow/tests/agents/invoke/fixtures/",
    )
    assert _is_historical_archive("ralph-workflow/tests/agents/invoke/fixtures/capture.jsonl")
    assert _is_historical_archive(".pi-subagents/artifacts/reviewer_transcript.jsonl")
    assert not _is_historical_archive(".opencode/skills/current.md")
    assert not _is_historical_archive(".codex/config.toml")
    assert not _is_historical_archive("ralph-workflow/PROMPT.md")
    assert not _is_historical_archive("PROMPT.md")
    assert not _is_historical_archive("ralph-workflow/ralph/pipeline/state.py")


def test_index_parser_reads_version_2_entries() -> None:
    entries = ("a.txt", "dir with space/c d.txt", "sub/b.txt")

    parsed, version = _parse_index_entries(_synthetic_index(entries))

    assert version == 2
    assert parsed == entries


def test_index_parser_reads_version_3_entries() -> None:
    entries = ("a.txt", "sub/b.txt")

    parsed, version = _parse_index_entries(_synthetic_index(entries, version=3))

    assert version == 3
    assert parsed == entries


def test_index_parser_rejects_unsupported_index_version() -> None:
    data = _synthetic_index(("a.txt",), version=4)

    with pytest.raises(AssertionError, match="unsupported git index version 4"):
        _parse_index_entries(data)


def test_index_parser_rejects_split_index() -> None:
    data = bytearray(_synthetic_index(("a.txt",)))
    data[-_INDEX_TRAILER_DIGEST_BYTES:] = (
        b"link" + (0).to_bytes(4, "big") + (b"\0" * _INDEX_TRAILER_DIGEST_BYTES)
    )

    with pytest.raises(AssertionError, match="split git index is not supported"):
        _parse_index_entries(bytes(data))


def test_index_parser_rejects_corrupt_index() -> None:
    with pytest.raises(AssertionError, match="git index signature is missing"):
        _parse_index_entries(b"not-an-index" + b"\0" * 64)
