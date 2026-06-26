"""Black-box tests for periodic media registry prune (O(N) amortized).

wt-024 memory-perf AC-10: ``_drop_evicted_cache_entries`` was invoked
on EVERY ``_persist_media_session_entry`` / ``_persist_media_registry_entry``
call. Each call stats EVERY cached artifact's on-disk file. With N
entries and M adds, total cost is O(N*M) - quadratic in the worst case.

We gate the stat pass behind a periodic add-counter so it runs every
``_MEDIA_PRUNE_INTERVAL`` adds, not every add. Semantics preserved:
the dedup-by-artifact_id list comprehension still runs every add, and
the next prune tick drops any entries whose cache files were evicted.

The OrderedDict rebuild stays per-add because it is O(N) and is needed
for correct append-order semantics.

This test asserts:

1. After a prune tick, entries whose cache files were deleted are
   still dropped (semantics preserved).
2. The dedup-by-artifact_id replacement still happens on every add.
3. Between prune ticks, no Path.is_file stat pass fires (amortized
   bounded by the interval).

We use real FsWorkspace (tmp_path) so the audit policy treats the file
fixtures as load-bearing for the regression assertion.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import ralph.mcp.tools.workspace._media_io as _media_io_mod
from ralph.mcp.tools.workspace._media_io import (
    _MEDIA_PRUNE_INTERVAL,
    _persist_media_registry_entry,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import pytest


def _make_entry(artifact_id: str, cache_path: str = "") -> dict[str, str]:
    return {
        "artifact_id": artifact_id,
        "uri": f"ralph://media/{artifact_id}",
        "mime_type": "image/png",
        "title": f"title-{artifact_id}",
        "modality": "image",
        "delivery": "resource_reference_replay",
        "reason": "test",
        "source_path": "",
        "cache_path": cache_path,
        "source_uri": "",
        "block_type": "image",
        "failure_kind": "",
        "identity_key": f"id-{artifact_id}",
    }


def test_prune_interval_constant_is_positive() -> None:
    """``_MEDIA_PRUNE_INTERVAL`` must be a positive integer."""
    assert isinstance(_MEDIA_PRUNE_INTERVAL, int)
    assert _MEDIA_PRUNE_INTERVAL > 0, (
        f"_MEDIA_PRUNE_INTERVAL must be > 0; got {_MEDIA_PRUNE_INTERVAL}"
    )


def test_prune_drops_evicted_entries(tmp_path: Path) -> None:
    """After a prune tick, entries whose cache files were deleted are dropped."""
    real_workspace = FsWorkspace(tmp_path)

    # Create real cache file for A; do NOT create one for B.
    cache_a = tmp_path / ".agent" / "tmp" / "media" / "cache-A"
    cache_a.parent.mkdir(parents=True, exist_ok=True)
    cache_a.write_bytes(b"A-bytes")

    # Seed the registry with 2 entries (A has an extant cache, B does not).
    seed_path = tmp_path / ".agent" / "tmp" / "media_registry.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "artifacts": [
                    _make_entry("A", cache_path=".agent/tmp/media/cache-A"),
                    _make_entry("B", cache_path=".agent/tmp/media/cache-B"),
                ],
            }
        )
    )

    # Run ``_MEDIA_PRUNE_INTERVAL`` adds so a prune tick fires.
    for i in range(_MEDIA_PRUNE_INTERVAL):
        _persist_media_registry_entry(real_workspace, _make_entry(f"x-{i:04d}"))

    # Read the registry back and verify B was dropped, A was preserved.
    data = json.loads((tmp_path / ".agent" / "tmp" / "media_registry.json").read_text())
    artifacts = data.get("artifacts", [])
    artifact_ids = {entry.get("artifact_id") for entry in artifacts}
    assert "B" not in artifact_ids, (
        f"entry B (whose cache file was evicted) must be dropped from the "
        f"manifest on the next prune tick; got artifacts {sorted(artifact_ids)!r}"
    )
    assert "A" in artifact_ids, (
        f"entry A (with an extant cache file) must be preserved; got "
        f"artifacts {sorted(artifact_ids)!r}"
    )


def test_dedup_by_artifact_id_runs_every_add(tmp_path: Path) -> None:
    """The dedup-by-artifact_id list comprehension runs every add (no regression)."""
    real_workspace = FsWorkspace(tmp_path)

    seed_path = tmp_path / ".agent" / "tmp" / "media_registry.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps({"schema_version": "2", "artifacts": []}))

    # Re-add the same entry 3 times (well under the prune interval).
    for _ in range(3):
        _persist_media_registry_entry(real_workspace, _make_entry("dup-1"))

    data = json.loads((tmp_path / ".agent" / "tmp" / "media_registry.json").read_text())
    artifacts = data.get("artifacts", [])
    matching = [e for e in artifacts if e.get("artifact_id") == "dup-1"]
    assert len(matching) == 1, (
        f"dedup-by-artifact_id must keep exactly one entry; got {len(matching)}"
    )


def test_prune_gated_skips_stat_pass_between_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the prune tick actually does work by counting Path.is_file calls.

    The production code calls
    ``Path(self._workspace.absolute_path(cache_path)).is_file()`` to check
    whether a cache file still exists. We monkeypatch ``Path.is_file`` to
    count invocations and verify the periodic gate.
    """
    # Reset the module-level counter so the test starts at a known state
    # (earlier tests in the suite may have incremented it).
    _media_io_mod._media_add_counter = 0

    real_workspace = FsWorkspace(tmp_path)

    # Seed the registry with 2 entries pointing at NON-EXISTENT cache files.
    # Each prune tick will stat-check both and drop them (they're evicted).
    seed_path = tmp_path / ".agent" / "tmp" / "media_registry.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "artifacts": [
                    _make_entry("stale-A", cache_path=".agent/tmp/media/missing-A"),
                    _make_entry("stale-B", cache_path=".agent/tmp/media/missing-B"),
                ],
            }
        )
    )

    is_file_calls = {"n": 0}
    original_is_file = Path.is_file

    def counting_is_file(self: Path, *args: object, **kwargs: object) -> bool:
        is_file_calls["n"] += 1
        return original_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", counting_is_file)
    try:
        # First add (counter goes from 0 to 1, NOT a prune tick).
        # No Path.is_file calls expected for the prune pass because
        # counter % 32 != 0.
        _persist_media_registry_entry(real_workspace, _make_entry("add-1"))
        non_tick_calls = is_file_calls["n"]

        # Run (_MEDIA_PRUNE_INTERVAL - 2) more non-tick adds.
        for i in range(_MEDIA_PRUNE_INTERVAL - 2):
            _persist_media_registry_entry(real_workspace, _make_entry(f"add-{i + 2}"))
        # Counter is now 31. No prune yet.
        pre_tick = is_file_calls["n"]
        assert pre_tick == non_tick_calls, (
            f"non-tick adds must NOT fire Path.is_file; "
            f"non_tick={non_tick_calls}, pre_tick={pre_tick}"
        )

        # The next add (counter=32) IS the prune tick.
        _persist_media_registry_entry(real_workspace, _make_entry("tick-trigger"))
        post_tick = is_file_calls["n"]

        # The prune tick must have fired Path.is_file on the 2 stale entries.
        assert post_tick > pre_tick, (
            f"prune tick must have fired Path.is_file on stale entries; "
            f"pre_tick={pre_tick}, post_tick={post_tick}"
        )
        assert post_tick >= pre_tick + 2, (
            f"prune tick must stat-check BOTH stale entries; delta={post_tick - pre_tick}"
        )
    finally:
        monkeypatch.undo()
