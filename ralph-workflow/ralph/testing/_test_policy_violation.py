"""TestPolicyViolation data class for audit_test_policy."""

from __future__ import annotations


class TestPolicyViolation:
    """A single policy violation found in a test file."""

    __test__ = False

    def __init__(
        self,
        file_path: str,
        line: int,
        category: str,
        detail: str,
    ) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"
