from __future__ import annotations


class _StubSubscriber:
    """Minimal subscriber stub — only waiting_status_line is needed."""

    @property
    def waiting_status_line(self) -> str | None:
        return None
