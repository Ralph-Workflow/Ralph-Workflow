
from __future__ import annotations

import os
import re
from pathlib import Path

from ralph.recovery.failure_classifier import _is_subscription_limit_message


def _default_agy_cli_log_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "cli.log"


_AGY_CLI_LOG_PATH = _default_agy_cli_log_path()
_AGY_CLI_LOG_TAIL_BYTES = 4096
_AUTH_PATTERN = re.compile(
    r"(?:not logged in|authentication failed|failed to get OAuth token|OAuth failure|auth timed out)",
    re.IGNORECASE,
)
_CHAINED_AUTH_SUCCEEDED_PATTERN = re.compile(r"ChainedAuth succeeded", re.IGNORECASE)
_MODEL_PATTERN = re.compile(
    r"(?:model .*?(?:not recognized|not in local config)|failed to resolve model)", re.IGNORECASE
)
_MODEL_ID_PATTERN = re.compile(r"(?:model flag\s+|model ID\s+)(\S+)", re.IGNORECASE)
_QUOTA_RESET_PATTERN = re.compile(r"Resets in\s+([^\s.]+)", re.IGNORECASE)
_PERMISSION_AUTO_DENY_PATTERN = re.compile(
    r"no output produced.*?permission.*?auto-denied", re.IGNORECASE | re.DOTALL
)
_PRINT_MODE_TIMEOUT_PATTERN = re.compile(
    r"Print mode: timed out after \d+ polls(?:\s*\(printed=\d+\))?", re.IGNORECASE
)


def agy_cli_log_start_offset(cli_log_path: Path | None = None) -> tuple[Path, int]:
    path = cli_log_path or _AGY_CLI_LOG_PATH
    try:
        return path, path.stat().st_size
    except OSError:
        return path, 0


def agy_fresh_cli_log_tail(cli_log_path: Path, start_offset: int) -> str:
    try:
        end_offset = cli_log_path.stat().st_size
        if end_offset <= start_offset:
            return ""
        read_offset = max(start_offset, end_offset - _AGY_CLI_LOG_TAIL_BYTES)
        with cli_log_path.open("rb") as log_file:
            log_file.seek(read_offset, os.SEEK_SET)
            return log_file.read(_AGY_CLI_LOG_TAIL_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def agy_empty_output_reason(output: list[str], cli_log_path: Path | None = None) -> str | None:
    evidence = "\n".join(output)
    if not evidence:
        path = cli_log_path or _AGY_CLI_LOG_PATH
        try:
            evidence = path.read_text(encoding="utf-8", errors="replace")[-_AGY_CLI_LOG_TAIL_BYTES:]
        except OSError:
            evidence = ""
    if _is_subscription_limit_message([evidence]):
        reset = _QUOTA_RESET_PATTERN.search(evidence)
        reset_hint = f" (resets in {reset.group(1)})" if reset else ""
        return (
            "AGY exited without output because API quota exhausted (quota is exhausted)"
            f"{reset_hint}; wait for quota reset and retry"
        )
    if _AUTH_PATTERN.search(evidence) and not _CHAINED_AUTH_SUCCEEDED_PATTERN.search(evidence):
        return "AGY exited without output because authentication failed; authenticate with AGY and retry"
    if _PERMISSION_AUTO_DENY_PATTERN.search(evidence):
        return (
            "AGY exited without output because a tool's permission request was "
            "auto-denied in headless mode (no prompt is possible); re-run with "
            "--dangerously-skip-permissions or add the tool to the permissions.allow "
            "setting and retry"
        )
    if _MODEL_PATTERN.search(evidence):
        model = _MODEL_ID_PATTERN.search(evidence)
        model_hint = f" '{model.group(1)}' is not recognized" if model else " is unavailable"
        return (
            "AGY exited without output because the requested model"
            f"{model_hint}; run `agy models` and select a listed ID"
        )
    if _PRINT_MODE_TIMEOUT_PATTERN.search(evidence):
        return (
            "AGY exited without output because print-mode polling timed out before "
            "a result was printed; retry the invocation, and if this recurs check "
            "whether a tool permission is being auto-denied in headless mode"
        )
    return None
