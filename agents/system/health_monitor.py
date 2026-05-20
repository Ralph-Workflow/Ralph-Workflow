#!/usr/bin/env python3
"""System health monitor - watches cron jobs, classifies failures, and applies safe automatic repairs."""

import subprocess
import json
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("/home/mistlight/.openclaw/workspace/agents/system/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "health_monitor.json"

MARKETING_LOG_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing/logs")

KEY_JOBS = {
    "marketing-workflow-audit",
    "competitor-analysis",
    "marketing-active-loop",
    "repo-adoption-tracker",
    "reddit-pipeline-watchdog",
    "system-health-monitor",
    "marketing-momentum-watchdog",
    "reddit-monitor",
}

JOB_NAME_TO_ID: dict[str, str] = {}


def run_openclaw(*args: str) -> tuple[int, str, str]:
    result = subprocess.run(["openclaw", *args], capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def get_cron_jobs():
    result = subprocess.run(
        ["openclaw", "cron", "list", "--json"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

def check_marketing_stuck():
    findings = []
    audit_path = MARKETING_LOG_DIR / "marketing_workflow_audit_latest.json"
    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text())
            failing_signals = audit.get("adoption_evaluation", {}).get("failing_signals", [])
            failing_tactics = audit.get("failing_tactics", [])
            if "primary_repo_flat" in failing_signals:
                findings.append({
                    "category": "flat_outcomes",
                    "detail": "Primary repo (Codeberg) adoption flat across measurement window",
                    "source": "marketing_workflow_audit_latest.json",
                    "signals": failing_signals,
                    "tactics": [t for t in failing_tactics if "flat" in t.lower()],
                })
            if "reddit_style_repetition" in failing_tactics:
                findings.append({
                    "category": "repetitive_tactic",
                    "detail": "Reddit body repetition risk detected - outreach patterns stale",
                    "source": "marketing_workflow_audit_latest.json",
                    "tactics": ["reddit_style_repetition"],
                })
        except (json.JSONDecodeError, KeyError):
            pass
    adoption_path = MARKETING_LOG_DIR / "adoption_metrics_latest.json"
    if adoption_path.exists():
        try:
            adoption = json.loads(adoption_path.read_text())
            evaluation = adoption.get("evaluation", {})
            failing_signals = evaluation.get("failing_signals", [])
            if "primary_repo_flat" in failing_signals:
                if not any(f["category"] == "flat_outcomes" for f in findings):
                    findings.append({
                        "category": "flat_outcomes",
                        "detail": "Primary repo (Codeberg) adoption flat - no delta in recent window",
                        "source": "adoption_metrics_latest.json",
                        "signals": failing_signals,
                    })
        except (json.JSONDecodeError, KeyError):
            pass
    sync_path = MARKETING_LOG_DIR / "ralph_sync_status.json"
    audit_path_repair = MARKETING_LOG_DIR / "marketing_workflow_audit_latest.json"
    if audit_path_repair.exists():
        try:
            audit = json.loads(audit_path_repair.read_text())
            repair_actions = audit.get("repair_actions", [])
            failing_tactics = audit.get("failing_tactics", [])
            for ra in repair_actions:
                findings.append({
                    "category": "repair_needed",
                    "detail": f"REPAIR {ra['failure_type']}: {ra['action']}",
                    "source": "marketing_workflow_audit_latest.json",
                    "suggested_repair": ra["action"],
                    "repair_priority": ra.get("priority", 9),
                    "kill_condition": ra.get("kill_condition", ""),
                    "success_metric": ra.get("success_metric", ""),
                    "affected_tactics": failing_tactics,
                })
        except (json.JSONDecodeError, KeyError):
            pass

    if sync_path.exists():
        try:
            sync = json.loads(sync_path.read_text())
            conditions = sync.get("conditions", {})
            if conditions.get("destructive_sync_layout"):
                findings.append({
                    "category": "sync_layout_issue",
                    "detail": "Destructive sync layout detected - potential data loss risk",
                    "source": "ralph_sync_status.json",
                })
        except (json.JSONDecodeError, KeyError):
            pass
    return findings

def apply_safe_repairs(issues, jobs_payload):
    repairs = []
    jobs = jobs_payload.get("jobs", [])
    by_name = {job.get("name"): job for job in jobs}

    for job in jobs:
        if job.get("id") and job.get("name"):
            JOB_NAME_TO_ID[job["name"]] = job["id"]

    for issue in issues:
        name = issue.get("name", "")
        category = issue.get("category", "")
        job_id = issue.get("job_id")

        if job_id == "__marketing__":
            continue

        if category in {"timeout", "timeout_risk"} and job_id:
            job = by_name.get(name, {})
            payload = job.get("payload", {}) or {}
            timeout = int(payload.get("timeoutSeconds") or 0)
            desired = min(max(timeout * 2, 600), 900) if timeout else 600
            if desired > timeout:
                code, out, err = run_openclaw("cron", "edit", job_id, "--timeout-seconds", str(desired))
                repairs.append({
                    "type": "increase_timeout",
                    "job": name,
                    "from": timeout,
                    "to": desired,
                    "ok": code == 0,
                    "detail": (out or err).strip()[:300],
                })
            if name in {"competitor-analysis", "marketing-active-loop", "marketing-workflow-audit"}:
                code, out, err = run_openclaw("cron", "run", job_id)
                detail = (out or err).strip()[:300]
                repairs.append({
                    "type": "rerun_job",
                    "job": name,
                    "ok": code == 0 or 'already-running' in detail,
                    "detail": detail,
                })

    marketing_issue_names = {issue.get("name") for issue in issues if issue.get("job_id") == "__marketing__"}
    if marketing_issue_names & {"marketing:flat_outcomes", "marketing:repair_needed", "marketing:repetitive_tactic"}:
        active = by_name.get("marketing-active-loop", {})
        state = active.get("state", {})
        running_at = state.get("runningAtMs")
        last_run_at = state.get("lastRunAtMs") or 0
        now_ms = int(time.time() * 1000)
        stale_enough = (now_ms - last_run_at) > 10 * 60 * 1000
        if not running_at and stale_enough and active.get("id"):
            code, out, err = run_openclaw("cron", "run", active["id"])
            detail = (out or err).strip()[:300]
            repairs.append({
                "type": "trigger_marketing_active_loop",
                "job": "marketing-active-loop",
                "ok": code == 0 or 'already-running' in detail,
                "detail": detail,
            })

    return repairs


def main():
    jobs = get_cron_jobs()
    if not jobs or "jobs" not in jobs:
        print("Could not fetch cron jobs")
        sys.exit(1)

    issues = []
    for job in jobs.get("jobs", []):
        name = job.get("name", "unnamed")
        state = job.get("state", {})
        consecutive_errors = state.get("consecutiveErrors", 0)
        last_error = state.get("lastError") or state.get("lastDiagnosticSummary") or ""
        payload = job.get("payload", {}) or {}
        timeout = payload.get("timeoutSeconds", 0)
        last_duration = state.get("lastDurationMs", 0)
        enabled = job.get("enabled", True)
        last_status = state.get("lastStatus", "ok")
        error_reason = state.get("lastErrorReason", "")
        is_key = name in KEY_JOBS

        if not enabled:
            continue

        if is_key and error_reason == "timeout" and consecutive_errors >= 1 and last_error:
            issues.append({
                "job_id": job["id"],
                "name": name,
                "category": "timeout",
                "consecutive_errors": consecutive_errors,
                "last_status": last_status,
                "last_error": last_error,
                "timeout_seconds": timeout,
                "last_duration_ms": last_duration,
                "likely_cause": diagnose_issue(timeout, last_duration, last_error),
            })
            continue

        if is_key and timeout > 0 and last_duration > 0:
            ratio = last_duration / (timeout * 1000)
            if ratio >= 0.85 and error_reason != "timeout":
                issues.append({
                    "job_id": job["id"],
                    "name": name,
                    "category": "timeout_risk",
                    "consecutive_errors": consecutive_errors,
                    "last_status": last_status,
                    "last_error": last_error,
                    "timeout_seconds": timeout,
                    "last_duration_ms": last_duration,
                    "duration_ratio": round(ratio, 3),
                    "likely_cause": diagnose_issue(timeout, last_duration, last_error),
                })
                continue

        if consecutive_errors >= 2:
            issues.append({
                "job_id": job["id"],
                "name": name,
                "category": "error_streak",
                "consecutive_errors": consecutive_errors,
                "last_status": last_status,
                "last_error": last_error,
                "timeout_seconds": timeout,
                "last_duration_ms": last_duration,
                "likely_cause": diagnose_issue(timeout, last_duration, last_error),
            })

    marketing_findings = check_marketing_stuck()

    all_issues = list(issues)
    for finding in marketing_findings:
        issue = {
            "job_id": "__marketing__",
            "name": f"marketing:{finding['category']}",
            "category": finding["category"],
            "consecutive_errors": 0,
            "last_status": "stuck",
            "last_error": finding["detail"],
            "timeout_seconds": 0,
            "last_duration_ms": 0,
            "likely_cause": f"{finding['category'].upper()}: {finding['detail']}",
            "source": finding.get("source", ""),
            "signals": finding.get("signals", []),
            "tactics": finding.get("tactics", []),
        }
        if finding.get("suggested_repair"):
            issue["suggested_repair"] = finding["suggested_repair"]
            issue["repair_priority"] = finding.get("repair_priority", 9)
            issue["kill_condition"] = finding.get("kill_condition", "")
            issue["success_metric"] = finding.get("success_metric", "")
            issue["affected_tactics"] = finding.get("affected_tactics", [])
        all_issues.append(issue)

    repairs = apply_safe_repairs(all_issues, jobs)

    log_entry = {
        "ts": datetime.now().isoformat(),
        "jobs_checked": len(jobs.get("jobs", [])),
        "issues_found": len(all_issues),
        "issues": all_issues,
        "marketing_findings": marketing_findings,
        "repairs_attempted": repairs,
    }

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    if all_issues:
        real_issues = [i for i in all_issues if i.get("job_id") != "__marketing__"]
        mkt_findings = [i for i in all_issues if i.get("job_id") == "__marketing__"]
        print(f"\u26a0\ufe0f  SYSTEM HEALTH: {len(real_issues)} job issue(s) + {len(mkt_findings)} marketing finding(s):")
        for issue in real_issues:
            ratio_str = ""
            if issue.get("duration_ratio"):
                ratio_str = f" ({issue['duration_ratio']*100:.0f}% of timeout)"
            print(f"  [{issue['category']}] {issue['name']}: {issue.get('consecutive_errors', 0)} errors, {issue.get('likely_cause', '')}{ratio_str}")
            print(f"    Duration: {issue['last_duration_ms']}ms / timeout: {issue['timeout_seconds']}s")
        for finding in mkt_findings:
            detail = finding.get("likely_cause", finding.get("last_error", ""))
            repair = finding.get("suggested_repair", "")
            print(f"  [{finding['category']}] {finding['name']}: {detail}")
            if repair:
                print(f"    REPAIR: {repair[:200]}")
        if repairs:
            print("  [auto_repair] attempted:")
            for repair in repairs:
                print(f"    - {repair['type']} on {repair['job']}: {'ok' if repair['ok'] else 'failed'}")
    else:
        print("\u2705 System health OK - no issues detected")

    return 1 if all_issues else 0

def diagnose_issue(timeout_seconds, last_duration_ms, last_error):
    error_lower = (last_error or "").lower()

    if last_error == "cron: job execution timed out":
        if timeout_seconds and last_duration_ms > (timeout_seconds * 1000 * 0.9):
            return f"TIMEOUT_TOO_TIGHT: script took {last_duration_ms}ms but limit was {timeout_seconds}s - needs more headroom"
        if not timeout_seconds:
            return f"TIMEOUT: script timed out after {last_duration_ms}ms and timeout config could not be read"
        return "TIMEOUT: script is hanging, check for infinite loops or network calls"

    if "cron classifier: denial token" in error_lower:
        return "CLASSIFIER_FALSE_POSITIVE: job likely succeeded but used blocked summary wording"

    if "connection" in error_lower or "network" in error_lower:
        return "NETWORK_ERROR: check API credentials and connectivity"

    if "auth" in error_lower or "permission" in error_lower:
        return "AUTH_ERROR: credentials may have expired, been revoked, or permissions are insufficient"

    if last_duration_ms < 5000 and last_error:
        return f"IMMEDIATE_FAIL: script crashing early - {last_error[:100]}"

    if timeout_seconds and last_duration_ms > (timeout_seconds * 1000 * 0.9):
        return f"PRE_TIMEOUT_RISK: script at {last_duration_ms}ms / {timeout_seconds}s limit - duration exceeds 90%"

    return f"UNKNOWN: {last_error[:100] if last_error else 'no error details'}"

if __name__ == "__main__":
    sys.exit(main())
