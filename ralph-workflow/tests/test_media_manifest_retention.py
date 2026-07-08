"""Black-box tests for MediaManifest bounded retention (wt-024 M2).

The ``MediaManifest`` class lives at
``ralph/mcp/multimodal/resources.py`` and accumulates every new
identity via ``add()`` for the session lifetime.  This was an
unbounded growth path (each entry retains its ``raw_bytes`` payload).

The M2 fix:

  - Converts ``_entries`` to an ``OrderedDict`` (still dict-compatible
    for all callers that read it).
  - Adds a ``max_entries`` dataclass field (default 256, preserving
    existing small-fixture behaviour).
  - On ``add`` of a NEW identity that pushes ``len(_entries) >
    max_entries``, evicts the oldest ``_entries`` item AND removes every
    ``_identity_index`` mapping whose value is the evicted
    ``artifact_id``.  Re-adding an EXISTING identity dedups in place
    AND PRESERVES its original insertion position (the observable
    ``list_entries()`` / ``resources/list`` order must stay stable
    across duplicate adds per wt-024 analysis feedback).

Tests are pure in-memory (no real disk I/O, no subprocess, no
``time.sleep``) and stay well within the 60s combined budget.
All assertions go through the PUBLIC surface (get / list_entries /
add / is_empty / max_entries) so the test file stays free of
type-ignore comments per the AGENTS.md type-ignore policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.multimodal._media_entry_extras import MediaEntryExtras
from ralph.mcp.multimodal.resources import MediaManifest

if TYPE_CHECKING:
    from ralph.mcp.multimodal._manifest_entry import ManifestEntry


def _make_payload(label: str) -> bytes:
    """Build a distinct payload for each entry."""
    return f"payload-{label}".encode()


def _add_entry(
    manifest: MediaManifest,
    *,
    title: str,
    label: str,
    identity_key: str | None = None,
) -> ManifestEntry:
    """Add a single distinct entry to the manifest and return it."""
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
    """Default ``max_entries`` is 256 so existing fixtures are unaffected."""
    manifest = MediaManifest()
    assert manifest.max_entries == 256


def test_media_manifest_max_entries_override_is_respected() -> None:
    """``max_entries`` constructor argument is stored verbatim."""
    manifest = MediaManifest(max_entries=4)
    assert manifest.max_entries == 4


def test_media_manifest_evicts_oldest_when_cap_exceeded() -> None:
    """Adding a 3rd distinct identity with ``max_entries=2`` evicts the oldest.

    After 3 adds with a cap of 2, only the 2 most-recent artifacts are
    retained.  The oldest entry's artifact_id is no longer reachable
    via ``get`` and no longer appears in ``list_entries``.
    """
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
    # The evicted artifact_id is no longer retrievable.
    assert manifest.get(first_id) is None


def test_media_manifest_identity_index_resyncs_on_eviction() -> None:
    """After eviction, re-adding the evicted identity MUST yield a NEW
    artifact_id (not the original one) because ``_identity_index`` has
    dropped the stale mapping.  Otherwise a re-add would land on a
    phantom artifact that no longer exists in ``_entries``.

    Verified with a single fresh manifest: add an identity, force its
    eviction, then re-add the same identity and confirm the new
    artifact_id is different from the original.
    """
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

    # Force eviction of 'stale'.
    _add_entry(
        manifest,
        title="newcomer",
        label="newcomer",
        identity_key="identity:newcomer:1",
    )

    # 'stale' identity_key was evicted.  Re-adding the same identity
    # MUST yield a NEW artifact_id because the index mapping was
    # cleared (the old artifact_id is no longer in _entries).
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

    # Confirm via ``get()`` that the old artifact_id is no longer
    # retrievable (the stale entry was truly evicted, not just
    # orphaned in the index).
    assert manifest.get(stale_first_artifact_id) is None, (
        f"old artifact_id {stale_first_artifact_id} must NOT be"
        f" retrievable after eviction; got entry="
        f"{manifest.get(stale_first_artifact_id)}"
    )


def test_media_manifest_retained_identity_dedups_on_re_add() -> None:
    """A retained identity MUST continue to dedup to its existing
    artifact_id on re-add (no phantom duplicates).  This is the
    complement of the eviction test: an identity that has NOT been
    evicted should keep its artifact_id stable across re-adds.
    """
    manifest = MediaManifest(max_entries=10)

    first = _add_entry(
        manifest,
        title="stable",
        label="v1",
        identity_key="identity:stable",
    )
    first_artifact_id = first.artifact_id

    # Re-add several times; the artifact_id MUST stay stable.
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

    # Only one entry exists for this identity (counting via list_entries
    # because the public API doesn't expose a "count by identity" helper).
    stable_entries = [
        entry for entry in manifest.list_entries() if entry.identity_key == "identity:stable"
    ]
    assert len(stable_entries) == 1


def test_media_manifest_re_add_existing_identity_does_not_grow() -> None:
    """Re-adding the same identity MUST NOT grow the manifest (dedup).

    The re-add path overwrites the existing entry in place AND
    refreshes its position in the OrderedDict (LRU).  The count of
    entries must stay constant.
    """
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

    # Re-add the same identity with a new payload.
    _add_entry(
        manifest,
        title="shared",
        label="v2",
        identity_key="identity:shared",
    )

    # No growth: same 2 entries.
    assert len(manifest.list_entries()) == 2, (
        "re-adding an existing identity MUST NOT grow the manifest"
    )
    second_ids = {entry.artifact_id for entry in manifest.list_entries()}
    assert first_ids == second_ids, (
        f"re-add of existing identity should preserve the same"
        f" artifact_ids; first={first_ids}, second={second_ids}"
    )


def test_media_manifest_re_add_preserves_insertion_order() -> None:
    """Re-adding an EXISTING identity MUST preserve its original
    insertion position (NOT move it to the back / LRU refresh).

    The memory-cap fix must not change the observable ordering of
    ``list_entries()`` for duplicate adds.  ``resources/list`` (in
    ``ralph/mcp/server/_mcp_server.py``) forwards
    ``list_entries()`` order directly to MCP clients, so reordering
    on duplicate add would be an externally visible behavior change
    that the memory cap did not require (wt-024 analysis feedback).

    Verified via the public surface: after re-adding 'oldest', the
    list_entries() order is still ['oldest', 'middle']; re-adding
    'middle' does not move it to the front either.
    """
    manifest = MediaManifest(max_entries=3)

    _add_entry(manifest, title="oldest", label="old", identity_key="k:oldest")
    _add_entry(manifest, title="middle", label="mid", identity_key="k:middle")
    _add_entry(manifest, title="newest", label="new", identity_key="k:newest")

    baseline_titles = [entry.title for entry in manifest.list_entries()]
    assert baseline_titles == ["oldest", "middle", "newest"], (
        f"baseline insertion order should be oldest->middle->newest; got {baseline_titles}"
    )

    # Re-add "oldest" several times — its position MUST stay at the front.
    for _ in range(3):
        _add_entry(manifest, title="oldest", label="old2", identity_key="k:oldest")
        ordered_titles = [entry.title for entry in manifest.list_entries()]
        assert ordered_titles == ["oldest", "middle", "newest"], (
            f"re-adding 'oldest' MUST preserve its original position; got {ordered_titles}"
        )

    # Re-add "middle" — its position MUST stay in the middle.
    _add_entry(manifest, title="middle", label="mid2", identity_key="k:middle")
    ordered_titles = [entry.title for entry in manifest.list_entries()]
    assert ordered_titles == ["oldest", "middle", "newest"], (
        f"re-adding 'middle' MUST preserve its original position; got {ordered_titles}"
    )

    # Re-add "newest" — its position MUST stay at the back.
    _add_entry(manifest, title="newest", label="new2", identity_key="k:newest")
    ordered_titles = [entry.title for entry in manifest.list_entries()]
    assert ordered_titles == ["oldest", "middle", "newest"], (
        f"re-adding 'newest' MUST preserve its original position; got {ordered_titles}"
    )


def test_media_manifest_re_add_does_not_protect_from_eviction() -> None:
    """An identity that has been re-added (and so has a 'fresh' payload)
    is NOT protected from eviction.  The oldest insertion-order entry
    is the next eviction target, regardless of how many times it has
    been re-added.  This confirms the M2 fix's FIFO eviction is based
    on insertion order, not LRU activity.
    """
    manifest = MediaManifest(max_entries=2)

    _add_entry(manifest, title="oldest", label="old", identity_key="k:oldest")
    _add_entry(manifest, title="middle", label="mid", identity_key="k:middle")

    # Re-add "oldest" many times — its position stays at the front,
    # so it is still the next eviction target.
    for _ in range(5):
        _add_entry(manifest, title="oldest", label="old2", identity_key="k:oldest")

    # Adding a 3rd identity evicts 'oldest' (insertion-order oldest).
    _add_entry(manifest, title="newest", label="new", identity_key="k:newest")

    final_titles = [entry.title for entry in manifest.list_entries()]
    assert "oldest" not in final_titles, (
        f"oldest (in insertion order) should have been evicted; got {final_titles}"
    )
    assert "middle" in final_titles
    assert "newest" in final_titles


def test_media_manifest_position_refresh_keeps_identity_index_consistent() -> None:
    """After a re-add, the ``_identity_index`` mapping for the same
    identity_key MUST still point at the same artifact_id (the dedup
    contract).  Verified via ``add()`` return: repeated adds with
    the same ``identity_key`` MUST return the same ``artifact_id``
    AND ``list_entries()`` MUST still have exactly one entry for
    that identity_key.
    """
    manifest = MediaManifest(max_entries=3)

    first = _add_entry(manifest, title="stable", label="v1", identity_key="k:stable")

    # Re-add several times; the artifact_id MUST stay stable.
    for _ in range(3):
        again = _add_entry(manifest, title="stable", label="vN", identity_key="k:stable")
        assert again.artifact_id == first.artifact_id, (
            f"re-adding the same identity MUST keep the artifact_id stable;"
            f" first={first.artifact_id}, repeat={again.artifact_id}"
        )

    # Only one entry exists for this identity.
    stable_entries = [
        entry for entry in manifest.list_entries() if entry.identity_key == "k:stable"
    ]
    assert len(stable_entries) == 1


def test_media_manifest_eviction_at_default_cap_with_default_256() -> None:
    """At the default cap (256), the 257th distinct identity evicts the oldest.

    Smoke test to confirm the default cap is wired through to the
    eviction logic (not just into the field).
    """
    manifest = MediaManifest()  # default max_entries=256
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
    # The first item must have been evicted.
    assert "item-0" not in titles
    # The last item must be retained.
    assert "item-256" in titles


class TestMediaManifestRawBytesRelease:
    """AC-06 regression: MediaManifest does NOT retain _raw_bytes when a
    durable replay source (byte_loader or cache_path) is supplied at
    add-time. The raw payload is rehydratable via the loader so
    storing both wastes up to 256 x multi-MB of memory.
    """

    def test_media_manifest_does_not_retain_raw_bytes_when_byte_loader_supplied(
        self,
    ) -> None:
        """A byte_loader at add-time suppresses _raw_bytes retention."""
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
        # The loader is still used to rehydrate on demand (no data loss).
        assert entry.load_bytes() == payload
        assert loader_calls["count"] == 1

    def test_media_manifest_does_not_retain_raw_bytes_when_cache_path_supplied(
        self,
    ) -> None:
        """A cache_path at add-time also suppresses _raw_bytes retention."""
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
        # cache_path is preserved for later reload via byte_loader.
        assert entry.cache_path == "/tmp/cache-path"

    def test_media_manifest_retains_raw_bytes_when_no_durable_source(self) -> None:
        """Without byte_loader or cache_path, raw_bytes IS retained.

        This preserves the in-memory-only contract for legacy callers
        that pass raw bytes directly without any replay source. The
        raw_bytes release is opt-in via a durable source, not
        unconditional (which would silently break legacy callers).
        """
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
        """Re-adding a loader-backed identity keeps _raw_bytes == None.

        The dedup path must not re-introduce _raw_bytes when the
        re-added entry was previously created with a byte_loader.
        """
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

        # Re-add with the SAME identity_key but a different payload.
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
