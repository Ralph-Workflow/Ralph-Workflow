from __future__ import annotations

from ralph.mcp.explore.reindex_bench import (
    hash_bytes_accelerated,
    hash_bytes_scalar,
    select_hash_implementation,
)


def test_hash_implementations_are_deterministic() -> None:
    data = bytes(range(256)) * 128
    assert hash_bytes_scalar(data) == hash_bytes_scalar(data)
    assert hash_bytes_accelerated(data) == hash_bytes_accelerated(data)


def test_selection_is_explicit_and_auto_uses_accelerated() -> None:
    assert select_hash_implementation("scalar") is hash_bytes_scalar
    assert select_hash_implementation("accelerated") is hash_bytes_accelerated
    assert select_hash_implementation("auto") is hash_bytes_accelerated


def test_empty_and_non_ascii_bytes_are_supported() -> None:
    data = "héllo".encode()
    assert isinstance(hash_bytes_scalar(b""), str)
    assert isinstance(hash_bytes_accelerated(data), str)
