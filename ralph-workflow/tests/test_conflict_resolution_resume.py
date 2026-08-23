"""Landed rebase stops survive a later failure via the progress sidecar."""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.conflict_resolution.progress import (
    RebaseResolutionProgress,
    load_progress,
    progress_path,
    save_progress,
)
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop, record_landed_stop


def test_four_landed_stops_survive_in_the_sidecar(tmp_path: Path) -> None:
    for index, sha in enumerate(("aaa1", "bbb2", "ccc3", "ddd4"), start=1):
        record_landed_stop(
            tmp_path,
            RebaseStop(
                sha=sha,
                subject=f"stop {index}",
                conflicted_files=("src/alpha.py",),
                stop_index=index,
                stop_cap=5,
            ),
        )
    progress = load_progress(tmp_path)
    assert progress is not None
    assert progress.landed_shas == ["aaa1", "bbb2", "ccc3", "ddd4"]
    assert progress_path(tmp_path).is_file()


def test_resume_reads_landed_stops_after_a_fresh_process(tmp_path: Path) -> None:
    save_progress(
        tmp_path,
        RebaseResolutionProgress(landed_shas=["aaa1", "bbb2"], remaining_paths=["src/omega.py"]),
    )
    reloaded = load_progress(tmp_path)
    assert reloaded is not None
    assert reloaded.landed_shas == ["aaa1", "bbb2"]
    assert reloaded.remaining_paths == ["src/omega.py"]
