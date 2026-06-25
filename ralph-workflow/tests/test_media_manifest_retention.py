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
  - On ``add`` of a NEW identity that pushes ``len(_entries) >=
    max_entries``, evicts the oldest ``_entries`` item AND removes every
    ``_identity_index`` mapping whose value is the evicted
    ``artifact_id``.  Existing identities dedup by overwriting in place
    and refresh their position to the back of the OrderedDict (LRU
    semantics).

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


def test_media_manifest_re_add_refreshes_lru_position() -> None:
    """Re-adding an existing identity MUST refresh its LRU position.

    After a re-add, the entry moves to the back of the OrderedDict.
    The oldest previously-recent entry is the next eviction target,
    not the just-refreshed one.

    Verified via the public surface: after re-add of 'oldest', the
    next-new identity evicts 'middle' (not 'oldest').
    """
    manifest = MediaManifest(max_entries=2)

    _add_entry(manifest, title="oldest", label="old", identity_key="k:oldest")
    _add_entry(manifest, title="middle", label="mid", identity_key="k:middle")

    # Re-add "oldest" — should refresh its position; it now sits
    # BEHIND "middle" in insertion order, so the next eviction
    # would target "middle".
    _add_entry(manifest, title="oldest", label="old2", identity_key="k:oldest")

    ordered_titles = [entry.title for entry in manifest.list_entries()]
    assert ordered_titles[-1] == "oldest", (
        f"after re-add, 'oldest' should be at the back (LRU); got {ordered_titles}"
    )
    assert ordered_titles[0] == "middle", (
        f"after re-add, 'middle' should be at the front (next to evict);"
        f" got {ordered_titles}"
    )

    # Add one more to force eviction of 'middle', keeping 'oldest'.
    _add_entry(manifest, title="newest", label="new", identity_key="k:newest")

    final_titles = [entry.title for entry in manifest.list_entries()]
    assert "middle" not in final_titles, (
        f"middle should have been evicted after LRU refresh;"
        f" got {final_titles}"
    )
    assert "oldest" in final_titles
    assert "newest" in final_titles


def test_media_manifest_position_refresh_keeps_identity_index_consistent() -> None:
    """After a re-add refreshes the LRU position, the ``_identity_index``
    mapping for the same identity_key MUST still point at the same
    artifact_id (the dedup contract).

    Verified via ``add()`` return: repeated adds with the same
    ``identity_key`` MUST return the same ``artifact_id``.
    """
    manifest = MediaManifest(max_entries=3)

    first = _add_entry(
        manifest, title="stable", label="v1", identity_key="k:stable"
    )

    # Re-add several times; the artifact_id MUST stay stable.
    for _ in range(3):
        again = _add_entry(
            manifest, title="stable", label="vN", identity_key="k:stable"
        )
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
