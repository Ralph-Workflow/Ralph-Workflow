from __future__ import annotations

import http.client
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ralph.config import _env as config_env

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
    ok: bool
    message: str


def evaluate_body(body: str | None) -> ClaCheckResult:

    if not body:
        return ClaCheckResult(
            ok=False, message="Add the CLA agreement checkbox to the pull request description."
        )
    if _CHECKED_CLA_PATTERN.search(body):
        return ClaCheckResult(ok=True, message="CLA agreement checkbox is checked.")
    if _UNCHECKED_CLA_PATTERN.search(body):
        return ClaCheckResult(
            ok=False, message="Check the CLA agreement box in the pull request description."
        )
    return ClaCheckResult(
        ok=False, message="Add the CLA agreement checkbox to the pull request description."
    )


def evaluate_github_event(*, event_name: str | None, event: Mapping[str, object]) -> ClaCheckResult:

    if event_name != "pull_request":
        return ClaCheckResult(ok=True, message="CLA check skipped for non-pull-request event.")

    pull_request = event.get("pull_request")
    if not isinstance(pull_request, Mapping):
        return ClaCheckResult(
            ok=False, message="Could not load pull request body for CLA verification."
        )

    body = pull_request.get("body")
    return evaluate_body(body if isinstance(body, str) else None)


def evaluate_codeberg_environment(
    env: Mapping[str, str],
    *,
    fetch_json: Callable[[str], object] | None = None,
) -> ClaCheckResult:

    if env.get("CI_PIPELINE_EVENT") not in _CODEBERG_PULL_REQUEST_EVENTS:
        return ClaCheckResult(ok=True, message="CLA check skipped for non-pull-request event.")

    injected_body = env.get("RALPH_CLA_PR_BODY")
    if injected_body is not None:
        return evaluate_body(injected_body)

    api_url = _codeberg_pull_api_url_from_env(env)
    if api_url is None:
        return ClaCheckResult(
            ok=False, message="Could not load pull request body for CLA verification."
        )

    try:
        payload = (fetch_json or _fetch_json)(api_url)
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
        return ClaCheckResult(
            ok=False, message=f"Could not load pull request body for CLA verification: {exc}"
        )

    if not isinstance(payload, Mapping):
        return ClaCheckResult(
            ok=False, message="Could not load pull request body for CLA verification."
        )
    body = payload.get("body")
    return evaluate_body(body if isinstance(body, str) else None)


def evaluate_current_environment() -> ClaCheckResult:

    github_event_path = config_env.getenv("GITHUB_EVENT_PATH")
    if github_event_path:
        try:
            event = _read_json_file(Path(github_event_path))
        except (OSError, ValueError) as exc:
            return ClaCheckResult(ok=False, message=f"Could not load GitHub event payload: {exc}")
        return evaluate_github_event(
            event_name=config_env.getenv("GITHUB_EVENT_NAME"), event=event
        )

    return evaluate_codeberg_environment(config_env.os.environ)


def main() -> int:

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
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        msg = "Only https URLs are supported"
        raise ValueError(msg)
    conn = http.client.HTTPSConnection(parsed.netloc, timeout=_NETWORK_TIMEOUT_SECONDS)
    try:
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        conn.request("GET", path, headers={"Accept": "application/json"})
        response = conn.getresponse()
        payload: object = json.loads(response.read().decode("utf-8"))
        return payload
    finally:
        conn.close()


def _read_json_file(path: Path) -> Mapping[str, object]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError("event payload is not a JSON object")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
