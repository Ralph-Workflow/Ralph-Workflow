from __future__ import annotations

from ralph.contrib.cla import (
    CLA_CHECKBOX_LABEL,
    ClaCheckResult,
    evaluate_body,
    evaluate_codeberg_environment,
    evaluate_github_event,
)


def test_checked_cla_checkbox_satisfies_gate() -> None:
    body = f"""
## Checklist

- [x] {CLA_CHECKBOX_LABEL}
"""

    result = evaluate_body(body)

    assert result == ClaCheckResult(ok=True, message="CLA agreement checkbox is checked.")


def test_unchecked_cla_checkbox_fails_gate() -> None:
    body = f"""
## Checklist

- [ ] {CLA_CHECKBOX_LABEL}
"""

    result = evaluate_body(body)

    assert result.ok is False
    assert result.message == "Check the CLA agreement box in the pull request description."


def test_github_pull_request_event_uses_pull_request_body() -> None:
    event = {
        "pull_request": {
            "body": f"- [X] {CLA_CHECKBOX_LABEL}",
        },
    }

    result = evaluate_github_event(event_name="pull_request", event=event)

    assert result.ok is True


def test_github_non_pull_request_event_skips_gate() -> None:
    result = evaluate_github_event(event_name="push", event={})

    assert result == ClaCheckResult(
        ok=True,
        message="CLA check skipped for non-pull-request event.",
    )


def test_codeberg_pull_request_fetches_body_from_forge_url() -> None:
    fetched_urls: list[str] = []

    def fetch_json(url: str) -> object:
        fetched_urls.append(url)
        return {"body": f"- [x] {CLA_CHECKBOX_LABEL}"}

    result = evaluate_codeberg_environment(
        {
            "CI_PIPELINE_EVENT": "pull_request",
            "CI_FORGE_URL": "https://codeberg.org",
            "CI_REPO": "RalphWorkflow/Ralph-Workflow",
            "CI_COMMIT_PULL_REQUEST": "42",
        },
        fetch_json=fetch_json,
    )

    assert result.ok is True
    assert fetched_urls == [
        "https://codeberg.org/api/v1/repos/RalphWorkflow/Ralph-Workflow/pulls/42"
    ]


def test_codeberg_pull_request_metadata_event_enforces_cla() -> None:
    result = evaluate_codeberg_environment(
        {
            "CI_PIPELINE_EVENT": "pull_request_metadata",
            "CI_FORGE_URL": "https://codeberg.org",
            "CI_REPO": "RalphWorkflow/Ralph-Workflow",
            "CI_COMMIT_PULL_REQUEST": "42",
        },
        fetch_json=lambda _url: {"body": f"- [ ] {CLA_CHECKBOX_LABEL}"},
    )

    assert result.ok is False
    assert result.message == "Check the CLA agreement box in the pull request description."


def test_codeberg_pull_request_fails_closed_when_body_cannot_be_loaded() -> None:
    result = evaluate_codeberg_environment(
        {
            "CI_PIPELINE_EVENT": "pull_request",
            "CI_PIPELINE_FORGE_URL": "",
        },
        fetch_json=lambda _url: {},
    )

    assert result.ok is False
    assert result.message == "Could not load pull request body for CLA verification."
