from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.completion_signals import _check_completion_sentinel

if TYPE_CHECKING:
    from pathlib import Path


def test_check_completion_sentinel_returns_false_when_run_id_is_none(tmp_path: Path) -> None:
    assert _check_completion_sentinel(tmp_path, None) is False


def test_check_completion_sentinel_returns_false_when_file_not_found(tmp_path: Path) -> None:
    def fake_read(_path: Path) -> str:
        raise FileNotFoundError

    assert _check_completion_sentinel(tmp_path, "test-run-id", _read_fn=fake_read) is False


def test_check_completion_sentinel_returns_true_when_file_exists(tmp_path: Path) -> None:
    assert (
        _check_completion_sentinel(
            tmp_path,
            "test-run-id",
            _read_fn=lambda _path: '{"run_id": "test-run-id"}',
        )
        is True
    )


def test_check_completion_sentinel_path_ends_with_correct_filename(tmp_path: Path) -> None:
    seen: list[Path] = []

    def fake_read(path: Path) -> str:
        seen.append(path)
        return '{"run_id": "test-run-id"}'

    assert _check_completion_sentinel(tmp_path, "test-run-id", _read_fn=fake_read) is True
    assert seen == [tmp_path / ".agent" / "completion_seen_test-run-id.json"]
