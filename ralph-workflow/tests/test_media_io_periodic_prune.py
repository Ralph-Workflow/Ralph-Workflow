from __future__ import annotations

import json
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.tools.workspace._media_io import (
    _MEDIA_PRUNE_INTERVAL,
    _persist_media_registry_entry,
    _reset_media_prune_counter,
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
    assert isinstance(_MEDIA_PRUNE_INTERVAL, int)
    assert _MEDIA_PRUNE_INTERVAL > 0, (
        f"_MEDIA_PRUNE_INTERVAL must be > 0; got {_MEDIA_PRUNE_INTERVAL}"
    )


def test_prune_drops_evicted_entries(tmp_path: Path) -> None:
    real_workspace = FsWorkspace(tmp_path)

    cache_a = tmp_path / ".agent" / "tmp" / "media" / "cache-A"
    cache_a.parent.mkdir(parents=True, exist_ok=True)
    cache_a.write_bytes(b"A-bytes")

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

    for i in range(_MEDIA_PRUNE_INTERVAL):
        _persist_media_registry_entry(real_workspace, _make_entry(f"x-{i:04d}"))

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
    real_workspace = FsWorkspace(tmp_path)

    seed_path = tmp_path / ".agent" / "tmp" / "media_registry.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps({"schema_version": "2", "artifacts": []}))

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
    _reset_media_prune_counter(count(1))

    real_workspace = FsWorkspace(tmp_path)

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
        _persist_media_registry_entry(real_workspace, _make_entry("add-1"))
        non_tick_calls = is_file_calls["n"]

        for i in range(_MEDIA_PRUNE_INTERVAL - 2):
            _persist_media_registry_entry(real_workspace, _make_entry(f"add-{i + 2}"))
        pre_tick = is_file_calls["n"]
        assert pre_tick == non_tick_calls, (
            f"non-tick adds must NOT fire Path.is_file; "
            f"non_tick={non_tick_calls}, pre_tick={pre_tick}"
        )

        _persist_media_registry_entry(real_workspace, _make_entry("tick-trigger"))
        post_tick = is_file_calls["n"]

        assert post_tick > pre_tick, (
            f"prune tick must have fired Path.is_file on stale entries; "
            f"pre_tick={pre_tick}, post_tick={post_tick}"
        )
        assert post_tick >= pre_tick + 2, (
            f"prune tick must stat-check BOTH stale entries; delta={post_tick - pre_tick}"
        )
    finally:
        monkeypatch.undo()
