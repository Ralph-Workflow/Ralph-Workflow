"""Regression coverage for the bounded Codex lifetime-home registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.transport import codex as codex_module

if TYPE_CHECKING:
    from pathlib import Path


def test_codex_home_regression_lifetime_registry_cap_never_removes_active_homes(
    tmp_path: Path,
) -> None:
    """S-10: registry eviction drops only atexit tracking, never a live home."""
    original_homes = codex_module._all_allocated_codex_homes
    original_cap = codex_module._ALL_CODEX_HOMES_CAP
    codex_module._all_allocated_codex_homes = set()
    codex_module._ALL_CODEX_HOMES_CAP = 3
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        homes = [codex_module._allocate_codex_home_dir(tmp_path) for _ in range(5)]

        assert len(codex_module._all_allocated_codex_homes) == 3
        assert str(homes[-1]) in codex_module._all_allocated_codex_homes
        assert all(home.exists() for home in homes)
        evicted = {
            str(home) for home in homes if str(home) not in codex_module._all_allocated_codex_homes
        }
        warnings = [record for record in records if "lifetime registry" in record]
        assert len(warnings) == 2
        for warning in warnings:
            assert "3" in warning
            assert any(path in warning for path in evicted)
            assert "%d" not in warning
            assert "%s" not in warning

        codex_module.cleanup_codex_homes()

        assert sum(home.exists() for home in homes) == 2
        assert codex_module._all_allocated_codex_homes == set()
    finally:
        logger.remove(sink_id)
        codex_module._all_allocated_codex_homes = original_homes
        codex_module._ALL_CODEX_HOMES_CAP = original_cap
