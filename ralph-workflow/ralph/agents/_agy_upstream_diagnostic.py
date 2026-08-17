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
# Structural match only (no literal tool name such as "command") so any tool that
# hits headless mode's permission wall is recognized, e.g.:
#   jetski: no output produced — a tool required the "command" permission that
#   headless mode cannot prompt for, so it was auto-denied. ...
_PERMISSION_AUTO_DENY_PATTERN = re.compile(
    r"no output produced.*?permission.*?auto-denied", re.IGNORECASE | re.DOTALL
)
# cli.log side-effect of the same failure family (and also of unrelated hangs), e.g.:
#   Print mode: timed out after 7 polls (printed=3)
_PRINT_MODE_TIMEOUT_PATTERN = re.compile(
    r"Print mode: timed out after \d+ polls(?:\s*\(printed=\d+\))?", re.IGNORECASE
)


def agy_empty_output_reason(output: list[str], cli_log_path: Path | None = None) -> str | None:
    """Return AGY's actionable empty-output cause from output or its bounded log tail.

    Precedence (most specific / most actionable first):
      1. quota      - unambiguous stderr/API signal, existing behaviour kept as-is.
      2. auth       - unambiguous stderr signal, existing behaviour kept as-is.
      3. permission-auto-deny - a specific stderr message ("no output produced" +
         "permission" + "auto-denied") naming a concrete headless-mode limitation;
         checked ahead of the model pattern because it is at least as specific and
         names a directly actionable remediation (--dangerously-skip-permissions /
         permissions.allow).
      4. model      - existing behaviour kept as-is.
      5. print-mode-timeout - checked last: "Print mode: timed out after N polls" is a
         cli.log *symptom* of the polling loop giving up, which can co-occur with (and
         be caused by) any of the causes above, so it is only reported once none of the
         more specific causes matched.
    """
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
