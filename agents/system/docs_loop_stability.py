#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_jsonl(path: Path) -> list[dict]:
    entries: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return entries
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def assess_docs_loop_stability(
    latest_path: Path,
    history_path: Path,
    *,
    fallback_health_history: Path | None = None,
    lookback_hours: int = 24,
    min_recent_failures: int = 2,
    required_consecutive_passes_after_fail: int = 2,
) -> dict:
    result = {
        "ok": True,
        "reason": "docs verifier stability window is healthy",
        "recent_failures": 0,
        "consecutive_passes_since_last_fail": 0,
        "latest_verdict": None,
        "window_entries": 0,
        "no_progress_failures": 0,
        "fallback_docs_issue_runs": 0,
    }

    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        result.update({
            "ok": False,
            "reason": f"missing or unreadable docs verifier JSON artifact: {latest_path}",
        })
        return result

    latest_verdict = str(latest.get("verdict") or "").lower()
    result["latest_verdict"] = latest_verdict
    if latest_verdict != "pass":
        result.update({
            "ok": False,
            "reason": f"latest docs verifier verdict is not pass: {latest_verdict!r}",
        })
        return result

    entries = load_jsonl(history_path)
    if not entries:
        result.update({
            "ok": False,
            "reason": f"docs verifier history is missing or empty: {history_path}",
        })
        return result

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    window: list[dict] = []
    for entry in entries:
        checked_at = _parse_ts(entry.get("checked_at"))
        if checked_at and checked_at >= cutoff:
            window.append(entry)

    if not window:
        result.update({
            "ok": False,
            "reason": f"no docs verifier history entries found inside the last {lookback_hours}h window",
        })
        return result

    recent_failures = 0
    no_progress_failures = 0
    for entry in window:
        verdict = str(entry.get("verdict") or "").lower()
        stop_reason = str(entry.get("stop_reason") or "")
        if verdict != "pass":
            recent_failures += 1
            if "no repair" in stop_reason or "no progress" in stop_reason:
                no_progress_failures += 1

    consecutive_passes = 0
    saw_failure = False
    for entry in reversed(window):
        verdict = str(entry.get("verdict") or "").lower()
        if verdict == "pass" and not saw_failure:
            consecutive_passes += 1
            continue
        if verdict != "pass":
            saw_failure = True
            break

    result.update({
        "recent_failures": recent_failures,
        "consecutive_passes_since_last_fail": consecutive_passes,
        "window_entries": len(window),
        "no_progress_failures": no_progress_failures,
    })

    if recent_failures >= min_recent_failures and consecutive_passes < required_consecutive_passes_after_fail:
        result.update({
            "ok": False,
            "reason": (
                "docs verifier repeat-failure window is still open: "
                f"{recent_failures} failed runs in the last {lookback_hours}h, "
                f"only {consecutive_passes} consecutive pass(es) since the last fail"
            ),
        })
        return result

    if no_progress_failures >= 1 and consecutive_passes < required_consecutive_passes_after_fail:
        result.update({
            "ok": False,
            "reason": (
                "docs verifier previously reported no-repair/no-progress failures and has not yet earned enough clean passes: "
                f"no_progress_failures={no_progress_failures}, consecutive_passes_since_last_fail={consecutive_passes}"
            ),
        })
        return result

    if fallback_health_history is not None:
        health_entries = load_jsonl(fallback_health_history)
        docs_issue_runs = 0
        for entry in health_entries:
            checked_at = _parse_ts(entry.get("ts"))
            if not checked_at or checked_at < cutoff:
                continue
            issues = entry.get("issues") or []
            if any(
                "docs" in str(issue.get("name") or "").lower()
                for issue in issues
            ):
                docs_issue_runs += 1
        result["fallback_docs_issue_runs"] = docs_issue_runs
        if docs_issue_runs >= min_recent_failures and consecutive_passes < required_consecutive_passes_after_fail:
            result.update({
                "ok": False,
                "reason": (
                    "docs loop recent health history shows repeat docs failures and the verifier has not yet re-earned stability: "
                    f"docs_issue_runs={docs_issue_runs}, consecutive_passes_since_last_fail={consecutive_passes}"
                ),
            })
            return result

    return result
