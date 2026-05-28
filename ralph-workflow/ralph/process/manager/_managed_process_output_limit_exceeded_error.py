"""Error raised when a managed process exceeds an output-byte budget."""

from __future__ import annotations


class ManagedProcessOutputLimitExceededError(RuntimeError):
    def __init__(self, *, output_limit_bytes: int, stdout: bytes, stderr: bytes) -> None:
        super().__init__(f"process output exceeded {output_limit_bytes} bytes")
        self.output_limit_bytes = output_limit_bytes
        self.stdout = stdout
        self.stderr = stderr


__all__ = ["ManagedProcessOutputLimitExceededError"]
