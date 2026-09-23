from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git import atomic_append_text


def test_atomic_append_text_empty_payload_is_noop(tmp_path: Path) -> None:
    target = tmp_path / "empty_payload.txt"
    target.write_text("initial\n", encoding="utf-8")
    pre_bytes = target.read_bytes()

    atomic_append_text(target, "", encoding="utf-8")

    assert target.read_bytes() == pre_bytes
    staging_siblings = [
        path
        for path in target.parent.iterdir()
        if path.name.startswith(target.name) and ".ralph-staging." in path.name
    ]
    assert not staging_siblings


def test_atomic_append_text_preserves_crlf_in_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "crlf_existing.txt"
    target.write_bytes(b"line-one\r\nline-two\r\n")

    atomic_append_text(target, "line-three\n", encoding="utf-8")

    raw_bytes = target.read_bytes()
    assert raw_bytes == b"line-one\r\nline-two\r\nline-three\n"
    assert raw_bytes.count(b"\r\n") == 2
    raw_text = target.read_text(encoding="utf-8")
    assert "line-one" in raw_text
    assert "line-two" in raw_text
    assert "line-three" in raw_text


def test_atomic_append_text_inserts_separator_when_existing_lacks_trailing_newline(
    tmp_path: Path,
) -> None:
    target = tmp_path / "no_trailing_newline.txt"
    target.write_text("existing-without-newline", encoding="utf-8")

    atomic_append_text(target, "*.cache\n", encoding="utf-8")

    assert "existing-without-newline\n*.cache" in target.read_text(encoding="utf-8")


def test_atomic_append_text_propagates_oserror_when_target_is_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "im_a_directory"
    target.mkdir()

    with pytest.raises(OSError):
        atomic_append_text(target, "payload\n", encoding="utf-8")


def test_atomic_append_text_replaces_target_symlink(tmp_path: Path) -> None:
    real_dir = tmp_path / "real_target_dir"
    real_dir.mkdir()
    real_file = real_dir / "real.txt"
    real_file.write_text("REAL content\n")

    symlink_dir = tmp_path / "symlink_dir"
    symlink_dir.symlink_to(real_dir)
    target_via_symlink = symlink_dir / "via_symlink.txt"
    target_via_symlink.symlink_to(real_file)

    atomic_append_text(target_via_symlink, "appended\n", encoding="utf-8")

    assert real_file.read_text(encoding="utf-8") == "REAL content\n"
    assert not target_via_symlink.is_symlink()
    assert target_via_symlink.read_text(encoding="utf-8") == "REAL content\nappended\n"
