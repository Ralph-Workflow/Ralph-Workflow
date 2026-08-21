"""An accepted rendered-record entry must never be silently evicted.

``RenderedRecordWriter`` buffers into a ``deque(maxlen=512)``, which
drops its oldest item without a word once full. ``append()`` therefore
flushes eagerly, before that cap, so a chatty run between
two explicit ``flush()`` calls cannot lose a line it already accepted.

That guard cites a regression test in its own docstring. The file it
names does not exist, and nothing in the suite referenced the threshold
at all -- raising it to 100000 left every test green while re-arming the
silent eviction. A bound whose only protection is a comment is not a
bound.
"""

from __future__ import annotations

from pathlib import Path

from ralph.display.record_writer import RenderedRecordWriter


def _written_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_a_burst_larger_than_the_buffer_loses_no_entry(tmp_path: Path) -> None:
    """More entries than the deque holds, with no explicit flush."""
    writer = RenderedRecordWriter(tmp_path, "unit")
    record = writer.path
    burst = writer.buffer_capacity * 3

    for index in range(burst):
        writer.append(f"entry-{index}")
    writer.flush()

    written = _written_lines(record)

    # The COUNT is the property: a deque drops silently, so a missing
    # line is the only evidence eviction leaves. (The formatter renders
    # a bare string entry without a body, so the text of an individual
    # entry is not available to assert on here.)
    assert len(written) == burst, f"{burst - len(written)} accepted entries were evicted"


def test_the_eager_flush_fires_before_the_deque_can_evict(tmp_path: Path) -> None:
    """The threshold must leave room, not sit at or past the cap.

    Sitting at the cap means the flush happens only once the deque has
    already dropped something.
    """
    writer = RenderedRecordWriter(tmp_path, "unit")
    record = writer.path
    capacity = writer.buffer_capacity

    # Find the eager-flush point by OBSERVATION rather than by reading
    # the constant: append until something reaches the file.
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
