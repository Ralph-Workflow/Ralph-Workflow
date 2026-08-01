"""Bounded, actionable diagnostics for AGY headless failures."""

from __future__ import annotations

import re
from pathlib import Path

_AGY_CLI_LOG_PATH = Path.home() / ".gemini" / "antigravity-cli" / "cli.log"
_QUOTA_PATTERN = re.compile(
    r"(?:RESOURCE_EXHAUSTED(?: \(code 429\))?|\b429\b|quota exhausted)", re.IGNORECASE
)
_AUTH_PATTERN = re.compile(
    r"(?:not logged in|authentication failed|failed to get OAuth token|OAuth failure)",
    re.IGNORECASE,
)
_MODEL_PATTERN = re.compile(
    r"(?:model .*?(?:not recognized|not in local config)|failed to resolve model)", re.IGNORECASE
)
_MODEL_ID_PATTERN = re.compile(r"(?:model flag\s+|model ID\s+)(\S+)", re.IGNORECASE)
_QUOTA_RESET_PATTERN = re.compile(r"Resets in\s+([^\s.]+)", re.IGNORECASE)


def agy_empty_output_reason(output: list[str], *, cli_log_path: Path | None = None) -> str | None:
    """Return AGY's actionable empty-output cause from output or its bounded log tail."""
    evidence = "\n".join(output)
    if not evidence:
        path = cli_log_path or _AGY_CLI_LOG_PATH
        try:
            evidence = path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            evidence = ""
    if _QUOTA_PATTERN.search(evidence):
        reset = _QUOTA_RESET_PATTERN.search(evidence)
        reset_hint = f" (resets in {reset.group(1)})" if reset else ""
        return (
            "AGY exited without output because API quota exhausted (quota is exhausted)"
            f"{reset_hint}; wait for quota reset and retry"
        )
    if _AUTH_PATTERN.search(evidence):
        return "AGY exited without output because authentication failed; authenticate with AGY and retry"
    if _MODEL_PATTERN.search(evidence):
        model = _MODEL_ID_PATTERN.search(evidence)
        model_hint = f" '{model.group(1)}' is not recognized" if model else " is unavailable"
        return (
            "AGY exited without output because the requested model"
            f"{model_hint}; run `agy models` and select a listed ID"
        )
    return None
