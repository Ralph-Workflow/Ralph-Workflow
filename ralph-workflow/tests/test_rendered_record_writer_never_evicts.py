from __future__ import annotations

from pathlib import Path

from ralph.display.record_writer import RenderedRecordWriter


def _written_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_a_burst_larger_than_the_buffer_loses_no_entry(tmp_path: Path) -> None:
    writer = RenderedRecordWriter(tmp_path, "unit")
    record = writer.path
    burst = writer.buffer_capacity * 3

    for index in range(burst):
        writer.append(f"entry-{index}")
    writer.flush()

    written = _written_lines(record)

    assert len(written) == burst, f"{burst - len(written)} accepted entries were evicted"


def test_the_eager_flush_fires_before_the_deque_can_evict(tmp_path: Path) -> None:
    writer = RenderedRecordWriter(tmp_path, "unit")
    record = writer.path
    capacity = writer.buffer_capacity

    appended = 0
    while not _written_lines(record):
        writer.append(f"entry-{appended}")
        appended += 1
        assert appended <= capacity, "nothing flushed before the deque could evict"

    assert appended < capacity, (
        f"the eager flush fired at {appended} of a {capacity}-entry buffer, "
        "leaving no room before the deque evicts"
    )
    assert len(_written_lines(record)) == appended
