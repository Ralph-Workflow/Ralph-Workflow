from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from ralph.pipeline.integration_preservation_bundle import (
    PRESERVATION_BUNDLE_RETENTION_SECONDS,
    prune_expired_preservation_bundles,
)

if TYPE_CHECKING:
    from pathlib import Path


def _bundle(state_dir: Path, token: str, age_seconds: float) -> Path:
    bundle = state_dir / "integration-preservation" / token
    bundle.mkdir(parents=True)
    (bundle / "worktree.tar").write_bytes(b"x" * 64)
    stamp = time.time() - age_seconds
    os.utime(bundle, (stamp, stamp))
    return bundle


def test_a_bundle_past_its_retention_is_removed(tmp_path: Path) -> None:
    expired = _bundle(tmp_path, "old", PRESERVATION_BUNDLE_RETENTION_SECONDS + 60)

    removed = prune_expired_preservation_bundles(tmp_path)

    assert not expired.exists()
    assert removed == (expired,)


def test_a_recent_bundle_is_kept(tmp_path: Path) -> None:
    recent = _bundle(tmp_path, "new", 60)

    assert prune_expired_preservation_bundles(tmp_path) == ()
    assert (recent / "worktree.tar").is_file()


def test_a_state_dir_without_bundles_is_left_alone(tmp_path: Path) -> None:
    assert prune_expired_preservation_bundles(tmp_path) == ()
    assert not (tmp_path / "integration-preservation").exists()
