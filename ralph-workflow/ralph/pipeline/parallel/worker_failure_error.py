"""Worker failure error for parallel coordinator."""

from __future__ import annotations


class WorkerFailureError(Exception):
    """Raised internally when a parallel worker fails."""

    def __init__(self, unit_id: str, exit_code: int, error: str) -> None:
        super().__init__(error)
        self.unit_id = unit_id
        self.exit_code = exit_code
        self.error = error
