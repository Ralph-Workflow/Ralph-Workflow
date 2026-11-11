from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.multimodal._media_entry_extras import MediaEntryExtras
from ralph.mcp.multimodal.resources import MediaManifest

if TYPE_CHECKING:
    from ralph.mcp.multimodal._manifest_entry import ManifestEntry


def _make_payload(label: str) -> bytes:
    return f"payload-{label}".encode()


def _add_entry(
    manifest: MediaManifest,
    *,
    title: str,
    label: str,
    identity_key: str | None = None,
) -> ManifestEntry:
    extras = None
    if identity_key is not None:
        extras = MediaEntryExtras(identity_key=identity_key)
    return manifest.add(
        title=title,
        mime_type="image/png",
        modality="image",
        raw_bytes=_make_payload(label),
        extras=extras,
    )


def test_media_manifest_default_max_entries_is_256() -> None:
    manifest = MediaManifest()
    assert manifest.max_entries == 256


def test_media_manifest_max_entries_override_is_respected() -> None:
    manifest = MediaManifest(max_entries=4)
    assert manifest.max_entries == 4


def test_media_manifest_evicts_oldest_when_cap_exceeded() -> None:
    manifest = MediaManifest(max_entries=2)

    _add_entry(manifest, title="first", label="1")
    first_entries = manifest.list_entries()
    assert len(first_entries) == 1
    first_id = first_entries[0].artifact_id

    _add_entry(manifest, title="second", label="2")
    _add_entry(manifest, title="third", label="3")

    entries = manifest.list_entries()
    assert len(entries) == 2, (
        f"expected exactly 2 entries after exceeding cap of 2,"
        f" got {len(entries)}: {[e.title for e in entries]}"
    )

    titles = [entry.title for entry in entries]
    assert "second" in titles
    assert "third" in titles
    assert "first" not in titles, (
        f"oldest entry 'first' should have been evicted, got titles={titles}"
    )
    assert manifest.get(first_id) is None


def test_media_manifest_identity_index_resyncs_on_eviction() -> None:
    manifest = MediaManifest(max_entries=2)

    stale_first = _add_entry(
        manifest,
        title="stale",
        label="stale",
        identity_key="identity:stale:1",
    )
    stale_first_artifact_id = stale_first.artifact_id

    _add_entry(
        manifest,
        title="other",
        label="other",
        identity_key="identity:other:1",
    )

    _add_entry(
        manifest,
        title="newcomer",
        label="newcomer",
        identity_key="identity:newcomer:1",
    )

    new_after_stale = _add_entry(
        manifest,
        title="stale-revived",
        label="stale2",
        identity_key="identity:stale:1",
    )
    assert new_after_stale.artifact_id != stale_first_artifact_id, (
        f"re-add of an evicted identity MUST land on a new artifact_id"
        f" (not the stale original); stale_first_artifact_id="
        f"{stale_first_artifact_id}, new_after_stale="
        f"{new_after_stale.artifact_id}"
    )

    assert manifest.get(stale_first_artifact_id) is None, (
        f"old artifact_id {stale_first_artifact_id} must NOT be"
        f" retrievable after eviction; got entry="
        f"{manifest.get(stale_first_artifact_id)}"
    )


def test_media_manifest_retained_identity_dedups_on_re_add() -> None:
    manifest = MediaManifest(max_entries=10)

    first = _add_entry(
        manifest,
        title="stable",
        label="v1",
        identity_key="identity:stable",
    )
    first_artifact_id = first.artifact_id

    for index in range(5):
        again = _add_entry(
            manifest,
            title="stable",
            label=f"v{index}",
            identity_key="identity:stable",
        )
        assert again.artifact_id == first_artifact_id, (
            f"re-add of a retained identity MUST dedup to its existing"
            f" artifact_id; first={first_artifact_id}, index={index},"
            f" repeat={again.artifact_id}"
        )

    stable_entries = [
        entry for entry in manifest.list_entries() if entry.identity_key == "identity:stable"
    ]
    assert len(stable_entries) == 1


def test_media_manifest_re_add_existing_identity_does_not_grow() -> None:
    manifest = MediaManifest(max_entries=3)

    _add_entry(
        manifest,
        title="shared",
        label="v1",
        identity_key="identity:shared",
    )
    _add_entry(
        manifest,
        title="other",
        label="other",
        identity_key="identity:other",
    )

    assert len(manifest.list_entries()) == 2
    first_ids = {entry.artifact_id for entry in manifest.list_entries()}

    _add_entry(
        manifest,
        title="shared",
        label="v2",
        identity_key="identity:shared",
    )

    assert len(manifest.list_entries()) == 2, (
        "re-adding an existing identity MUST NOT grow the manifest"
    )
    second_ids = {entry.artifact_id for entry in manifest.list_entries()}
    assert first_ids == second_ids, (
        f"re-add of existing identity should preserve the same"
        f" artifact_ids; first={first_ids}, second={second_ids}"
    )


def test_media_manifest_re_add_preserves_insertion_order() -> None:
    manifest = MediaManifest(max_entries=3)

    _add_entry(manifest, title="oldest", label="old", identity_key="k:oldest")
    _add_entry(manifest, title="middle", label="mid", identity_key="k:middle")
    _add_entry(manifest, title="newest", label="new", identity_key="k:newest")

    baseline_titles = [entry.title for entry in manifest.list_entries()]
    assert baseline_titles == ["oldest", "middle", "newest"], (
        f"baseline insertion order should be oldest->middle->newest; got {baseline_titles}"
    )

    for _ in range(3):
        _add_entry(manifest, title="oldest", label="old2", identity_key="k:oldest")
        ordered_titles = [entry.title for entry in manifest.list_entries()]
        assert ordered_titles == ["oldest", "middle", "newest"], (
            f"re-adding 'oldest' MUST preserve its original position; got {ordered_titles}"
        )

    _add_entry(manifest, title="middle", label="mid2", identity_key="k:middle")
    ordered_titles = [entry.title for entry in manifest.list_entries()]
    assert ordered_titles == ["oldest", "middle", "newest"], (
        f"re-adding 'middle' MUST preserve its original position; got {ordered_titles}"
    )

    _add_entry(manifest, title="newest", label="new2", identity_key="k:newest")
    ordered_titles = [entry.title for entry in manifest.list_entries()]
    assert ordered_titles == ["oldest", "middle", "newest"], (
        f"re-adding 'newest' MUST preserve its original position; got {ordered_titles}"
    )


def test_media_manifest_re_add_does_not_protect_from_eviction() -> None:
    manifest = MediaManifest(max_entries=2)

    _add_entry(manifest, title="oldest", label="old", identity_key="k:oldest")
    _add_entry(manifest, title="middle", label="mid", identity_key="k:middle")

    for _ in range(5):
        _add_entry(manifest, title="oldest", label="old2", identity_key="k:oldest")

    _add_entry(manifest, title="newest", label="new", identity_key="k:newest")

    final_titles = [entry.title for entry in manifest.list_entries()]
    assert "oldest" not in final_titles, (
        f"oldest (in insertion order) should have been evicted; got {final_titles}"
    )
    assert "middle" in final_titles
    assert "newest" in final_titles


def test_media_manifest_position_refresh_keeps_identity_index_consistent() -> None:
    manifest = MediaManifest(max_entries=3)

    first = _add_entry(manifest, title="stable", label="v1", identity_key="k:stable")

    for _ in range(3):
        again = _add_entry(manifest, title="stable", label="vN", identity_key="k:stable")
        assert again.artifact_id == first.artifact_id, (
            f"re-adding the same identity MUST keep the artifact_id stable;"
            f" first={first.artifact_id}, repeat={again.artifact_id}"
        )

    stable_entries = [
        entry for entry in manifest.list_entries() if entry.identity_key == "k:stable"
    ]
    assert len(stable_entries) == 1


def test_media_manifest_eviction_at_default_cap_with_default_256() -> None:
    manifest = MediaManifest()
    for index in range(257):
        _add_entry(
            manifest,
            title=f"item-{index}",
            label=str(index),
            identity_key=f"k:item-{index}",
        )
    entries = manifest.list_entries()
    assert len(entries) == 256
    titles = {entry.title for entry in entries}
    assert "item-0" not in titles
    assert "item-256" in titles


class TestMediaManifestRawBytesRelease:
    def test_media_manifest_does_not_retain_raw_bytes_when_byte_loader_supplied(
        self,
    ) -> None:
        payload = b"a" * 1024
        loader_calls = {"count": 0}

        def loader() -> bytes:
            loader_calls["count"] += 1
            return payload

        manifest = MediaManifest()
        entry = manifest.add(
            title="loader-backed",
            mime_type="image/png",
            modality="image",
            raw_bytes=payload,
            extras=MediaEntryExtras(byte_loader=loader),
        )
        assert entry._raw_bytes is None, (
            f"raw_bytes should be released when byte_loader is supplied"
            f" at add-time, got _raw_bytes={entry._raw_bytes!r}"
        )
        assert entry.load_bytes() == payload
        assert loader_calls["count"] == 1

    def test_media_manifest_does_not_retain_raw_bytes_when_cache_path_supplied(
        self,
    ) -> None:
        payload = b"cached-payload-bytes"
        manifest = MediaManifest()
        entry = manifest.add(
            title="cache-backed",
            mime_type="image/png",
            modality="image",
            raw_bytes=payload,
            extras=MediaEntryExtras(cache_path="/tmp/cache-path"),
        )
        assert entry._raw_bytes is None, (
            f"raw_bytes should be released when cache_path is supplied"
            f" at add-time, got _raw_bytes={entry._raw_bytes!r}"
        )
        assert entry.cache_path == "/tmp/cache-path"

    def test_media_manifest_retains_raw_bytes_when_no_durable_source(self) -> None:
        payload = b"in-memory-only-payload"
        manifest = MediaManifest()
        entry = manifest.add(
            title="memory-only",
            mime_type="image/png",
            modality="image",
            raw_bytes=payload,
        )
        assert entry._raw_bytes == payload, (
            f"raw_bytes SHOULD be retained when no durable source is"
            f" supplied, got _raw_bytes={entry._raw_bytes!r}"
        )

    def test_media_manifest_loader_backed_dedup_preserves_no_raw_bytes(self) -> None:
        payload = b"shared-loader-payload"
        loader_calls = {"count": 0}

        def loader() -> bytes:
            loader_calls["count"] += 1
            return payload

        manifest = MediaManifest()
        first = manifest.add(
            title="shared",
            mime_type="image/png",
            modality="image",
            raw_bytes=payload,
            extras=MediaEntryExtras(
                byte_loader=loader,
                identity_key="identity:shared-loader",
            ),
        )
        assert first._raw_bytes is None

        re_added = manifest.add(
            title="shared",
            mime_type="image/png",
            modality="image",
            raw_bytes=b"a-different-payload-that-should-not-be-stored",
            extras=MediaEntryExtras(
                byte_loader=loader,
                identity_key="identity:shared-loader",
            ),
        )
        assert re_added.artifact_id == first.artifact_id
        assert re_added._raw_bytes is None, (
            "re-add of a loader-backed identity MUST keep _raw_bytes None"
        )
        assert re_added.load_bytes() == payload
