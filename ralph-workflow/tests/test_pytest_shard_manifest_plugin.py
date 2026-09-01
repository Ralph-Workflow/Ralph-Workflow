"""Behavior tests for manifest-bounded pytest shard collection."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import pytest_shard_manifest_plugin as manifest_plugin


def test_load_shard_manifest_preserves_declared_file_order(tmp_path: Path) -> None:
    manifest_path = tmp_path / "shard.txt"
    manifest_path.write_text(
        "tests/test_bravo.py\ntests/unit/test_alpha.py\n",
        encoding="utf-8",
    )

    manifest = manifest_plugin.load_shard_manifest(manifest_path)

    assert manifest.paths == (
        "tests/test_bravo.py",
        "tests/unit/test_alpha.py",
    )
    assert manifest.order_for("tests/test_bravo.py") == 0
    assert manifest.order_for("tests/unit/test_alpha.py") == 1


@pytest.mark.parametrize(
    "contents",
    (
        "",
        "tests/test_alpha.py\n\n\ntests/test_bravo.py\n",
        "tests/test_alpha.py\ntests/test_alpha.py\n",
        "/tests/test_alpha.py\n",
        "tests/../test_alpha.py\n",
        "src/test_alpha.py\n",
        "tests/helper.py\n",
    ),
)
def test_load_shard_manifest_rejects_malformed_entries(
    tmp_path: Path,
    contents: str,
) -> None:
    manifest_path = tmp_path / "shard.txt"
    manifest_path.write_text(contents, encoding="utf-8")

    with pytest.raises(pytest.UsageError, match="invalid Ralph shard manifest"):
        manifest_plugin.load_shard_manifest(manifest_path)


def test_load_shard_manifest_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(pytest.UsageError, match="unable to read Ralph shard manifest"):
        manifest_plugin.load_shard_manifest(tmp_path / "missing.txt")


def test_manifest_filters_only_collectable_test_modules() -> None:
    manifest = manifest_plugin.ShardManifest(("tests/test_selected.py",))

    assert manifest.should_ignore("tests/test_other.py") is True
    assert manifest.should_ignore("tests/unit/other_test.py") is True
    assert manifest.should_ignore("tests/test_selected.py") is False
    assert manifest.should_ignore("tests/_support/helper.py") is False
    assert manifest.should_ignore("ralph/testing/test_helper.py") is False
