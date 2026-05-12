#!/usr/bin/env python3
"""Blocked-channel unblocker.

Purpose:
- maintain a registry of blocked channels
- run legitimate unblock checks
- learn which allowed actions help unblock channels
- recommend the next best manual or technical step

This agent does NOT attempt to evade bot protections, create synthetic identities,
or bypass site policies.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path("/home/mistlight/.openclaw/workspace/agents/unblocker")
LOG_DIR = BASE_DIR / "logs"
REGISTRY_FILE = BASE_DIR / "BLOCKED_CHANNELS.json"
RESULTS_FILE = LOG_DIR / "unblock_results.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_ACTIONS = {
    "check_public_read_access",
    "check_auth_status",
    "prepare_manual_account_setup",
    "prepare_manual_api_key_request",
    "prepare_manual_token_upgrade",
    "document_community_requirements",
    "document_policy_requirements",
    "verify_browser_readiness",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


@dataclass
class AttemptResult:
    action: str
    status: str
    summary: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))


def save_registry(registry: dict[str, Any]) -> None:
    registry["updated_at"] = now_iso()
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def command_output(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def http_status(url: str) -> str:
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "8", url]
    result = command_output(cmd, timeout=12)
    return result.stdout.strip() or "000"


def check_public_read_access(channel: dict[str, Any]) -> AttemptResult:
    target_urls = {
        "devto": "https://dev.to",
        "twitter": "https://x.com",
        "reddit": "https://www.reddit.com/r/programming",
        "hackernews": "https://news.ycombinator.com",
        "lobsters": "https://lobste.rs",
        "producthunt": "https://www.producthunt.com",
        "github-write": "https://api.github.com/rate_limit",
    }
    url = target_urls.get(channel["id"])
    if not url:
        return AttemptResult("check_public_read_access", "skipped", "No public URL configured.", {})
    code = http_status(url)
    status = "useful" if code in {"200", "301", "302"} else "blocked"
    return AttemptResult(
        "check_public_read_access",
        status,
        f"Public access check returned HTTP {code} for {url}.",
        {"url": url, "http_code": code},
    )


def check_auth_status(channel: dict[str, Any]) -> AttemptResult:
    if channel["id"] == "github-write":
        gh = shutil.which("gh")
        if not gh:
            return AttemptResult("check_auth_status", "blocked", "GitHub CLI not installed.", {"tool": "gh"})
        result = command_output([gh, "auth", "status"], timeout=15)
        text = (result.stdout + "\n" + result.stderr).strip()
        if "Logged in" in text or "logged into" in text:
            status = "partial"
            if "Token scopes" in text or "scopes" in text:
                status = "useful"
            return AttemptResult("check_auth_status", status, "GitHub auth status checked.", {"output": text[:500]})
        return AttemptResult("check_auth_status", "blocked", "GitHub auth not ready.", {"output": text[:500]})

    if channel["id"] == "twitter":
        return AttemptResult(
            "check_auth_status",
            "blocked",
            "No approved Twitter auth path configured. Requires manual user-owned session/API route.",
            {},
        )

    if channel["id"] == "devto":
        return AttemptResult(
            "check_auth_status",
            "blocked",
            "No dev.to API key configured.",
            {},
        )

    return AttemptResult("check_auth_status", "skipped", "No automated auth status check for this channel.", {})


def verify_browser_readiness(channel: dict[str, Any]) -> AttemptResult:
    browsers = ["firefox", "chromium", "chromium-browser", "google-chrome", "brave-browser"]
    found = [name for name in browsers if shutil.which(name)]
    if found:
        return AttemptResult(
            "verify_browser_readiness",
            "useful",
            f"Desktop browser available: {found[0]}.",
            {"available_browsers": found, "notes": channel.get("browser_requirements", {}).get("extension_notes", [])},
        )
    return AttemptResult(
        "verify_browser_readiness",
        "blocked",
        "No supported desktop browser found for manual auth/setup tasks.",
        {"checked": browsers},
    )


def prepare_manual_account_setup(channel: dict[str, Any]) -> AttemptResult:
    steps = channel.get("manual_prerequisites", [])
    return AttemptResult(
        "prepare_manual_account_setup",
        "useful",
        "Manual account setup checklist prepared.",
        {"steps": steps},
    )


def prepare_manual_api_key_request(channel: dict[str, Any]) -> AttemptResult:
    return AttemptResult(
        "prepare_manual_api_key_request",
        "useful",
        "Prepared API key request checklist for user completion.",
        {"steps": channel.get("manual_prerequisites", [])},
    )


def prepare_manual_token_upgrade(channel: dict[str, Any]) -> AttemptResult:
    return AttemptResult(
        "prepare_manual_token_upgrade",
        "useful",
        "Prepared token upgrade checklist for user completion.",
        {"steps": channel.get("manual_prerequisites", [])},
    )


def document_community_requirements(channel: dict[str, Any]) -> AttemptResult:
    return AttemptResult(
        "document_community_requirements",
        "useful",
        "Documented that this channel requires genuine manual participation before promotion.",
        {"blockers": channel.get("blockers", []), "manual_prerequisites": channel.get("manual_prerequisites", [])},
    )


def document_policy_requirements(channel: dict[str, Any]) -> AttemptResult:
    return AttemptResult(
        "document_policy_requirements",
        "useful",
        "Documented policy-sensitive prerequisites and manual steps.",
        {
            "blockers": channel.get("blockers", []),
            "forbidden_actions": channel.get("forbidden_actions", []),
            "manual_prerequisites": channel.get("manual_prerequisites", []),
        },
    )


ACTION_HANDLERS = {
    "check_public_read_access": check_public_read_access,
    "check_auth_status": check_auth_status,
    "prepare_manual_account_setup": prepare_manual_account_setup,
    "prepare_manual_api_key_request": prepare_manual_api_key_request,
    "prepare_manual_token_upgrade": prepare_manual_token_upgrade,
    "document_community_requirements": document_community_requirements,
    "document_policy_requirements": document_policy_requirements,
    "verify_browser_readiness": verify_browser_readiness,
}


def score_action_history(attempt_history: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    score: dict[str, dict[str, int]] = defaultdict(lambda: {"useful": 0, "blocked": 0, "skipped": 0})
    for attempt in attempt_history:
        action = attempt.get("action")
        status = attempt.get("status")
        if action and status in score[action]:
            score[action][status] += 1
    return dict(score)


def choose_next_actions(channel: dict[str, Any]) -> list[str]:
    allowed = [a for a in channel.get("allowed_actions", []) if a in ALLOWED_ACTIONS]
    preferred = [a for a in channel.get("preferred_next_actions", []) if a in allowed]
    history = score_action_history(channel.get("attempt_history", []))

    # simple learning rule: prefer actions with useful results; defer repeatedly blocked actions
    ranked = []
    for action in preferred or allowed:
        stats = history.get(action, {"useful": 0, "blocked": 0, "skipped": 0})
        value = (stats["useful"] * 3) - (stats["blocked"] * 2) - stats["skipped"]
        ranked.append((value, action))
    ranked.sort(key=lambda item: (-item[0], (preferred or allowed).index(item[1]) if item[1] in (preferred or allowed) else 999))

    selected = [action for _, action in ranked[:3]]
    return selected or allowed[:3]


def run_channel(channel: dict[str, Any]) -> dict[str, Any]:
    actions = choose_next_actions(channel)
    attempts: list[dict[str, Any]] = []
    for action in actions:
        handler = ACTION_HANDLERS[action]
        result = handler(channel)
        attempt = result.to_dict()
        attempt["timestamp"] = now_iso()
        attempts.append(attempt)
        channel.setdefault("attempt_history", []).append(attempt)

    useful = [a for a in attempts if a["status"] == "useful"]
    blocked = [a for a in attempts if a["status"] == "blocked"]
    if useful and not blocked:
        recommendation = "continue_legitimate_unblock"
    elif blocked and not useful:
        recommendation = "needs_manual_input"
    else:
        recommendation = "mixed_progress"

    return {
        "channel_id": channel["id"],
        "name": channel["name"],
        "recommendation": recommendation,
        "attempts": attempts,
        "next_actions": choose_next_actions(channel),
    }


def summarize_registry(registry: dict[str, Any]) -> dict[str, Any]:
    channels = registry.get("channels", [])
    summary = {"blocked": 0, "partial": 0, "ready": 0}
    for channel in channels:
        status = channel.get("status", "blocked")
        if status in summary:
            summary[status] += 1
        else:
            summary["blocked"] += 1
    return summary


def main() -> int:
    registry = load_registry()
    run_started = now_iso()
    results = []
    for channel in registry.get("channels", []):
        if channel.get("status") != "blocked":
            continue
        results.append(run_channel(channel))

    payload = {
        "timestamp": run_started,
        "summary": summarize_registry(registry),
        "results": results,
    }
    RESULTS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    save_registry(registry)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
