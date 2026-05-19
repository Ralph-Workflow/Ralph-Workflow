"""CliResult helper for test_cli_parse_counter_overrides.py."""

from __future__ import annotations


class CliResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
