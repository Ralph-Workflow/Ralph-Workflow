"""Validate CLA agreement state for Codeberg and GitHub pull requests."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from http.client import HTTPResponse

CLA_CHECKBOX_LABEL = (
    "I have read and agree to the Contributor License Agreement in CLA.md, including "
    "the grant that allows Ralph Workflow to distribute my contribution under "
    "AGPL-3.0-or-later and commercial licenses."
)

_CHECKED_CLA_PATTERN = re.compile(
    rf"(?im)^\s*[-*]\s*\[[xX]\]\s*{re.escape(CLA_CHECKBOX_LABEL)}\s*$"
)
_UNCHECKED_CLA_PATTERN = re.compile(
    rf"(?im)^\s*[-*]\s*\[\s\]\s*{re.escape(CLA_CHECKBOX_LABEL)}\s*$"
)
_CODEBERG_PULL_URL_PATTERN = re.compile(
    r"^https://codeberg\.org/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pulls/(?P<number>\d+)(?:\b|/)?"
)
_NETWORK_TIMEOUT_SECONDS = 15.0
_CODEBERG_PULL_REQUEST_EVENTS = frozenset({"pull_request", "pull_request_metadata"})


@dataclass(frozen=True)
class ClaCheckResult:
    """Outcome of a contributor license agreement verification."""

    ok: bool
    message: str


def evaluate_body(body: str | None) -> ClaCheckResult:
    """Return whether a pull request body contains the checked CLA line."""

    if not body:
        return ClaCheckResult(
            False, "Add the CLA agreement checkbox to the pull request description."
        )
    if _CHECKED_CLA_PATTERN.search(body):
        return ClaCheckResult(True, "CLA agreement checkbox is checked.")
    if _UNCHECKED_CLA_PATTERN.search(body):
        return ClaCheckResult(False, "Check the CLA agreement box in the pull request description.")
    return ClaCheckResult(False, "Add the CLA agreement checkbox to the pull request description.")


def evaluate_github_event(*, event_name: str | None, event: Mapping[str, object]) -> ClaCheckResult:
    """Evaluate a GitHub Actions webhook payload and skip non-PR events."""

    if event_name != "pull_request":
        return ClaCheckResult(True, "CLA check skipped for non-pull-request event.")

    pull_request = event.get("pull_request")
    if not isinstance(pull_request, Mapping):
        return ClaCheckResult(False, "Could not load pull request body for CLA verification.")

    body = pull_request.get("body")
    return evaluate_body(body if isinstance(body, str) else None)


def evaluate_codeberg_environment(
    env: Mapping[str, str],
    *,
    fetch_json: Callable[[str], object] | None = None,
) -> ClaCheckResult:
    """Evaluate Woodpecker/Codeberg PR metadata, fetching the PR body when needed."""

    if env.get("CI_PIPELINE_EVENT") not in _CODEBERG_PULL_REQUEST_EVENTS:
        return ClaCheckResult(True, "CLA check skipped for non-pull-request event.")

    injected_body = env.get("RALPH_CLA_PR_BODY")
    if injected_body is not None:
        return evaluate_body(injected_body)

    api_url = _codeberg_pull_api_url_from_env(env)
    if api_url is None:
        return ClaCheckResult(False, "Could not load pull request body for CLA verification.")

    try:
        payload = (fetch_json or _fetch_json)(api_url)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return ClaCheckResult(
            False, f"Could not load pull request body for CLA verification: {exc}"
        )

    if not isinstance(payload, Mapping):
        return ClaCheckResult(False, "Could not load pull request body for CLA verification.")
    body = payload.get("body")
    return evaluate_body(body if isinstance(body, str) else None)


def evaluate_current_environment() -> ClaCheckResult:
    """Evaluate the current CI environment as GitHub Actions or Woodpecker."""

    github_event_path = os.environ.get("GITHUB_EVENT_PATH")
    if github_event_path:
        try:
            event = _read_json_file(Path(github_event_path))
        except (OSError, ValueError) as exc:
            return ClaCheckResult(False, f"Could not load GitHub event payload: {exc}")
        return evaluate_github_event(event_name=os.environ.get("GITHUB_EVENT_NAME"), event=event)

    return evaluate_codeberg_environment(os.environ)


def main() -> int:
    """Run the CLA gate for the current environment and return a shell exit code."""

    result = evaluate_current_environment()
    stream = sys.stdout if result.ok else sys.stderr
    print(result.message, file=stream)
    return 0 if result.ok else 1


def _codeberg_pull_api_url(forge_url: str) -> str | None:
    match = _CODEBERG_PULL_URL_PATTERN.match(forge_url)
    if match is None:
        return None
    owner = match.group("owner")
    repo = match.group("repo")
    number = match.group("number")
    return f"https://codeberg.org/api/v1/repos/{owner}/{repo}/pulls/{number}"


def _codeberg_pull_api_url_from_env(env: Mapping[str, str]) -> str | None:
    forge_root = env.get("CI_FORGE_URL", "").rstrip("/")
    repo = env.get("CI_REPO", "")
    number = env.get("CI_COMMIT_PULL_REQUEST", "")
    if forge_root and repo and number.isdecimal():
        return f"{forge_root}/api/v1/repos/{repo}/pulls/{number}"
    return _codeberg_pull_api_url(env.get("CI_PIPELINE_FORGE_URL", ""))


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    response = cast(
        "HTTPResponse",
        urllib.request.urlopen(request, timeout=_NETWORK_TIMEOUT_SECONDS),
    )
    try:
        return cast("object", json.loads(response.read().decode("utf-8")))
    finally:
        response.close()


def _read_json_file(path: Path) -> Mapping[str, object]:
    payload = cast("object", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(payload, Mapping):
        raise ValueError("event payload is not a JSON object")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
