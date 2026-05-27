#!/usr/bin/env python3
"""System health monitor - watches cron jobs, classifies failures, and applies safe automatic repairs."""

import subprocess
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from docs_loop_stability import assess_docs_loop_stability
from incidents import incident_escalations, owner_action_recent, record_owner_action

LOG_DIR = Path("/home/mistlight/.openclaw/workspace/agents/system/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LATEST_FILE = LOG_DIR / "health_monitor_latest.json"
HISTORY_FILE = LOG_DIR / "health_monitor.jsonl"
ROOT = Path("/home/mistlight/.openclaw/workspace")
GATEWAY_JOBS_FILE = Path("/home/mistlight/.openclaw/cron/jobs.json")
ARCHITECTURE_VERIFIER_SCRIPT = ROOT / "agents/system/agent_architecture_verifier.py"
ARCHITECTURE_INDEPENDENT_VERIFY_SCRIPT = ROOT / "agents/system/agent_architecture_independent_verify.py"
DOCS_VERIFIER_SCRIPT = ROOT / "agents/docs_quality/ralph_docs_verify.py"
DOCS_VERIFIER_JSON = ROOT / "agents/docs_quality/ralph_verifier_latest.json"
DOCS_VERIFIER_HISTORY = ROOT / "agents/docs_quality/ralph_verifier_history.jsonl"
MIRROR_SYNC_SCRIPT = ROOT / "agents/marketing/sync_to_github.sh"
CODEBERG_REMOTE = "git@codeberg.org:RalphWorkflow/Ralph-Workflow.git"
GITHUB_REMOTE = "git@github.com:Ralph-Workflow/Ralph-Workflow.git"

KEY_JOBS = {
    "marketing-research-daily",
    "marketing-daily",
    "marketing-workflow-audit",
    "competitor-analysis",
    "marketing-active-loop",
    "repo-adoption-tracker",
    "reddit-pipeline-watchdog",
    "reddit-monitor",
    "apollo-channel-monitor",
    "content-generator",
    "content-poster",
    "Push research findings to git repo",
    "codeberg-github-mirror-sync",
    "blocked-channel-recovery",
    "agent-architecture-watchdog",
    "ralph-workflow-docs-verifier-supervisor",
    "system-health-monitor",
    "marketing-momentum-watchdog",
    "ralph-site-owner-loop",
    "backlink-tracker",
}

ESCALATION_OWNER_JOBS = {
    "docs": "ralph-workflow-docs-verifier-supervisor",
    "marketing": "marketing-active-loop",
    "site": "ralph-site-owner-loop",
    "architecture": "agent-architecture-watchdog",
    "health": "system-health-monitor",
}

OWNER_ACTION_COOLDOWN_MINUTES = {
    'marketing': 240,
    'docs': 120,
    'site': 180,
    'architecture': 180,
    'health': 60,
    None: 180,
}

REVIEW_FINDING_FIELDS = (
    "mustFix",
    "blockers",
    "remaining_blockers",
    "actionItems",
    "holds",
    "followups",
    "unresolvedFindings",
)

JOB_NAME_TO_ID: dict[str, str] = {}
ARTIFACT_CHECKS = [
    {
        "name": "agent_architecture_json",
        "path": ROOT / "agents/system/logs/agent_architecture_latest.json",
        "max_age_minutes": 480,
    },
    {
        "name": "agent_architecture_verifier",
        "path": ROOT / "agents/system/logs/agent_architecture_verifier_latest.md",
        "max_age_minutes": 480,
        "must_contain": "Status: independently verified pass",
    },
    {
        "name": "docs_verifier",
        "path": ROOT / "agents/docs_quality/ralph_verifier_latest.md",
        "max_age_minutes": 240,
        "must_contain": "Status: independently verified pass",
    },
    {
        "name": "shared_market_intelligence",
        "path": ROOT / "agents/marketing/logs/market_intelligence_latest.json",
        "max_age_minutes": 720,
    },
]

JSON_ARTIFACT_CHECKS = [
    {
        "name": "marketing_independent_verification",
        "path": ROOT / "agents/marketing/logs/marketing_loop_independent_verification.json",
        "max_age_minutes": 240,
        "required_verdict": "pass",
        "issue_category": "loop_verification_fail",
        "summary_key": "summary",
    },
    {
        "name": "docs_agentic_review",
        "path": ROOT / "agents/docs_quality/ralph_agentic_latest.json",
        "max_age_minutes": 240,
        "required_status": "pass",
        "required_bool_fields": {
            "loopHealthy": True,
            "shouldUserNeedToRepeatThis": False,
        },
        "required_empty_lists": ["mustFix"],
        "required_criteria_pass": True,
        "issue_category": "loop_verification_fail",
        "summary_key": "summary",
    },
    {
        "name": "market_intelligence_consumption",
        "path": ROOT / "agents/marketing/logs/market_intelligence_consumption_latest.json",
        "max_age_minutes": 720,
        "required_runtime_consumers": [
            "agents/marketing/run.py",
            "agents/marketing/reddit_monitor.py",
        ],
        "allowed_consumer_statuses": ["loaded", "skipped"],
    },
]

SOFT_OFFER_RED_FLAG_PATTERNS = [
    "if you want",
    "if you'd like",
    "if you would like",
    "if preferred",
    "if that helps",
    "let me know and i can",
    "i can turn this into",
]

SOFT_OFFER_GUARD_ARTIFACTS = [
    ROOT / "agents/docs_quality/ralph_latest.md",
    ROOT / "agents/docs_quality/ralph_agentic_latest.md",
    ROOT / "agents/docs_quality/ralph_agentic_latest.json",
    ROOT / "agents/docs_quality/ralph_verifier_latest.md",
    ROOT / "agents/docs_quality/ralph_verifier_latest.json",
    ROOT / "agents/docs_quality/ralph_editorial_latest.md",
    ROOT / "agents/system/logs/agent_architecture_verifier_latest.md",
    ROOT / "agents/system/logs/agent_architecture_independent_verification.json",
]


def load_history_entries(path: Path, *, limit: int = 50) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    entries: list[dict] = []
    for line in lines[-limit:]:
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


def run_owner_job(job_name: str) -> tuple[bool, str]:
    job_id = JOB_NAME_TO_ID.get(job_name)
    if not job_id:
        return False, f"owner job id not found for {job_name}"
    code, out, err = run_openclaw("cron", "run", job_id)
    detail = (out or err).strip()[:300]
    ok = code == 0 or "already-running" in detail or '"ok": true' in detail
    return ok, detail


def owner_job_for_issue(issue: dict) -> str | None:
    domain = issue.get('owner_domain')
    if domain in ESCALATION_OWNER_JOBS:
        return ESCALATION_OWNER_JOBS[domain]
    name = str(issue.get('name') or '').lower()
    if 'docs' in name:
        return ESCALATION_OWNER_JOBS['docs']
    if 'marketing' in name:
        return ESCALATION_OWNER_JOBS['marketing']
    if any(token in name for token in ('seo', 'site', 'backlink', 'search')):
        return ESCALATION_OWNER_JOBS['site']
    if 'architecture' in name:
        return ESCALATION_OWNER_JOBS['architecture']
    if 'health' in name:
        return ESCALATION_OWNER_JOBS['health']
    return None


def owner_action_cooldown_minutes(issue: dict) -> int:
    return OWNER_ACTION_COOLDOWN_MINUTES.get(issue.get('owner_domain'), OWNER_ACTION_COOLDOWN_MINUTES[None])


def escalation_blockers(issue: dict) -> list[str]:
    name = str(issue.get('name') or '')
    last_error = str(issue.get('last_error') or '')
    likely_cause = str(issue.get('likely_cause') or '')
    combined = f"{name}\n{last_error}\n{likely_cause}".lower()
    blockers: list[str] = []
    if 'marketing_independent_verification' in combined or 'marketing independent verification' in combined:
        blockers.append('marketing_independent_verification')
    return blockers


def run_openclaw(*args: str) -> tuple[int, str, str]:
    result = subprocess.run(["openclaw", *args], capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def is_gateway_restart_interrupt(last_error: str) -> bool:
    return "job interrupted by gateway restart" in (last_error or "").lower()


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


def age_minutes(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 60.0


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def slugify_issue_text(text: str, limit: int = 48) -> str:
    cleaned = ''.join(ch.lower() if ch.isalnum() else '-' for ch in text)
    while '--' in cleaned:
        cleaned = cleaned.replace('--', '-')
    cleaned = cleaned.strip('-')
    return (cleaned[:limit] or 'finding').strip('-')


def owner_domain_for_path(path: Path) -> str:
    path_text = str(path)
    if "/agents/docs_quality/" in path_text:
        return "docs"
    if "/agents/system/" in path_text:
        return "architecture"
    if "/agents/marketing/" in path_text:
        return "marketing"
    return "health"



def extract_review_findings(check_name: str, path: Path, payload: dict) -> list[dict]:
    findings: list[dict] = []
    for field in REVIEW_FINDING_FIELDS:
        value = payload.get(field)
        if not value:
            continue
        if not isinstance(value, list):
            value = [value]
        for item in value:
            item_text = str(item).strip()
            if not item_text:
                continue
            findings.append({
                "job_id": "__artifacts__",
                "name": f"{check_name}_{field}_{slugify_issue_text(item_text)}",
                "category": "review_followup_required",
                "path": str(path),
                "last_error": item_text,
                "likely_cause": f"Reviewer left unresolved required follow-up item in {field}: {item_text}",
            })
    return findings


def git_remote_head(remote: str) -> str | None:
    try:
        with TemporaryDirectory(prefix="health-monitor-git-") as tmp:
            proc = subprocess.run(
                ["git", "ls-remote", remote, "refs/heads/main"],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return None
    return line[0].split()[0]


def mirror_sync_is_healthy() -> tuple[bool, str]:
    codeberg_head = git_remote_head(CODEBERG_REMOTE)
    github_head = git_remote_head(GITHUB_REMOTE)
    if not codeberg_head or not github_head:
        return False, "could not read one or both remote HEADs"
    if codeberg_head != github_head:
        return False, f"remote HEAD mismatch: codeberg={codeberg_head} github={github_head}"
    return True, f"remote HEADs match at {codeberg_head}"


def inspect_soft_offer_phrase_guard() -> list[dict]:
    issues: list[dict] = []
    for path in SOFT_OFFER_GUARD_ARTIFACTS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        for pattern in SOFT_OFFER_RED_FLAG_PATTERNS:
            idx = lowered.find(pattern)
            if idx == -1:
                continue
            line_no = text[:idx].count("\n") + 1
            issues.append({
                "job_id": "__artifacts__",
                "name": f"soft_offer_phrase_guard_{path.stem}",
                "category": "review_followup_required",
                "path": str(path),
                "line": line_no,
                "last_error": f"red-flag phrase family detected: {pattern}",
                "likely_cause": "A monitored assistant-facing artifact used soft-offer wording that should count as a process failure, not acceptable normal output.",
                "owner_domain": owner_domain_for_path(path),
            })
            break
    return issues



def inspect_artifacts() -> list[dict]:
    issues: list[dict] = []
    for check in ARTIFACT_CHECKS:
        path = check["path"]
        if not path.exists():
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": "missing_artifact",
                "path": str(path),
                "likely_cause": "Required runtime artifact is missing",
            })
            continue

        age = age_minutes(path)
        if age > check["max_age_minutes"]:
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": "stale_artifact",
                "path": str(path),
                "artifact_age_minutes": round(age, 2),
                "max_age_minutes": check["max_age_minutes"],
                "likely_cause": "Required runtime artifact is stale",
            })
            continue

        must_contain = check.get("must_contain")
        if must_contain:
            text = path.read_text(encoding="utf-8", errors="replace")
            if must_contain not in text:
                issues.append({
                    "job_id": "__artifacts__",
                    "name": check["name"],
                    "category": "artifact_contract_fail",
                    "path": str(path),
                    "required_text": must_contain,
                    "likely_cause": "Verifier artifact exists but does not show required pass contract",
                })

    json_payloads: dict[str, dict] = {}
    for check in JSON_ARTIFACT_CHECKS:
        path = check["path"]
        if not path.exists():
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": "missing_artifact",
                "path": str(path),
                "likely_cause": "Required runtime artifact is missing",
            })
            continue

        age = age_minutes(path)
        if age > check["max_age_minutes"]:
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": "stale_artifact",
                "path": str(path),
                "artifact_age_minutes": round(age, 2),
                "max_age_minutes": check["max_age_minutes"],
                "likely_cause": "Required runtime artifact is stale",
            })
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": "artifact_parse_fail",
                "path": str(path),
                "likely_cause": f"Required JSON artifact is unreadable: {exc}",
            })
            continue

        json_payloads[check["name"]] = payload

        required_verdict = check.get("required_verdict")
        verdict = payload.get("verdict")
        if required_verdict and verdict != required_verdict:
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": check.get("issue_category", "artifact_contract_fail"),
                "path": str(path),
                "verdict": verdict,
                "required_verdict": required_verdict,
                "last_error": payload.get(check.get("summary_key", "summary"), ""),
                "likely_cause": payload.get(check.get("summary_key", "summary"), "Required verifier artifact did not pass"),
            })
            issues.extend(extract_review_findings(check["name"], path, payload))
            continue

        required_status = check.get("required_status")
        if required_status and payload.get("status") != required_status:
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": check.get("issue_category", "artifact_contract_fail"),
                "path": str(path),
                "last_error": f"status={payload.get('status')!r}",
                "likely_cause": payload.get(check.get("summary_key", "summary"), f"Required status {required_status!r} not met"),
            })
            issues.extend(extract_review_findings(check["name"], path, payload))
            continue

        required_bool_fields = check.get("required_bool_fields") or {}
        bool_failures = [
            f"{field}={payload.get(field)!r} expected {expected!r}"
            for field, expected in required_bool_fields.items()
            if payload.get(field) is not expected
        ]
        if bool_failures:
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": check.get("issue_category", "artifact_contract_fail"),
                "path": str(path),
                "last_error": "; ".join(bool_failures),
                "likely_cause": payload.get(check.get("summary_key", "summary"), "Required boolean artifact contract failed"),
            })
            continue

        required_empty_lists = check.get("required_empty_lists") or []
        list_failures = []
        unresolved_list_items: list[tuple[str, str]] = []
        for field in required_empty_lists:
            value = payload.get(field) or []
            if not isinstance(value, list):
                value = [value]
            if any(str(item).strip() for item in value):
                list_failures.append(f"{field} has unresolved entries")
                for item in value:
                    item_text = str(item).strip()
                    if item_text:
                        unresolved_list_items.append((field, item_text))
        if list_failures:
            issues.append({
                "job_id": "__artifacts__",
                "name": check["name"],
                "category": check.get("issue_category", "artifact_contract_fail"),
                "path": str(path),
                "last_error": "; ".join(list_failures),
                "likely_cause": payload.get(check.get("summary_key", "summary"), "Required list emptiness contract failed"),
            })
            for field, item_text in unresolved_list_items:
                issues.append({
                    "job_id": "__artifacts__",
                    "name": f"{check['name']}_{field}_{slugify_issue_text(item_text)}",
                    "category": "review_followup_required",
                    "path": str(path),
                    "last_error": item_text,
                    "likely_cause": f"Reviewer left unresolved required follow-up item in {field}: {item_text}",
                })
            continue

        if check.get("required_criteria_pass"):
            criteria = payload.get("criteria") or {}
            failing_criteria = sorted(
                key for key, value in criteria.items() if value != "pass"
            )
            if failing_criteria:
                issues.append({
                    "job_id": "__artifacts__",
                    "name": check["name"],
                    "category": check.get("issue_category", "artifact_contract_fail"),
                    "path": str(path),
                    "last_error": ", ".join(failing_criteria),
                    "likely_cause": payload.get(check.get("summary_key", "summary"), "Criteria pass contract failed"),
                })
                continue

        required_runtime_consumers = check.get("required_runtime_consumers") or []
        if required_runtime_consumers:
            consumers = payload.get("consumers", {}) or {}
            allowed_statuses = set(check.get("allowed_consumer_statuses") or ["loaded"])
            for consumer_name in required_runtime_consumers:
                consumer_payload = consumers.get(consumer_name)
                if not consumer_payload:
                    issues.append({
                        "job_id": "__artifacts__",
                        "name": check["name"],
                        "category": "artifact_contract_fail",
                        "path": str(path),
                        "last_error": consumer_name,
                        "likely_cause": f"Required runtime consumer proof is missing for {consumer_name}",
                    })
                    continue
                status = consumer_payload.get("status")
                if status not in allowed_statuses:
                    issues.append({
                        "job_id": "__artifacts__",
                        "name": check["name"],
                        "category": "artifact_contract_fail",
                        "path": str(path),
                        "last_error": f"{consumer_name}:{status}",
                        "likely_cause": f"Runtime consumer proof for {consumer_name} has unacceptable status {status}",
                    })

    docs_stability = assess_docs_loop_stability(
        DOCS_VERIFIER_JSON,
        DOCS_VERIFIER_HISTORY,
        fallback_health_history=HISTORY_FILE,
    )
    if not docs_stability.get("ok"):
        issues.append({
            "job_id": "__artifacts__",
            "name": "docs_verifier_stability",
            "category": "loop_verification_fail",
            "path": str(DOCS_VERIFIER_JSON),
            "last_error": docs_stability.get("reason", ""),
            "likely_cause": docs_stability.get("reason", ""),
            "recent_failures": docs_stability.get("recent_failures", 0),
            "consecutive_passes_since_last_fail": docs_stability.get("consecutive_passes_since_last_fail", 0),
        })

    architecture_payload = load_json(ROOT / "agents/system/logs/agent_architecture_latest.json")
    live_jobs = get_cron_jobs() or {"jobs": []}
    persisted_jobs = load_json(GATEWAY_JOBS_FILE) or {"jobs": []}
    if architecture_payload:
        audit_metadata = architecture_payload.get("audit_metadata", {}) or {}
        live_job_list = live_jobs.get("jobs", []) or []
        expected_live_jobs = len(live_job_list)
        expected_live_enabled = sum(1 for job in live_job_list if job.get("enabled", True))
        expected_live_disabled = sum(1 for job in live_job_list if not job.get("enabled", True))
        expected_disabled_names = sorted(job.get("name") for job in live_job_list if not job.get("enabled", True) and job.get("name"))

        mismatches = []
        if audit_metadata.get("live_jobs_checked") != expected_live_jobs:
            mismatches.append(f"live_jobs_checked={audit_metadata.get('live_jobs_checked')} expected {expected_live_jobs}")
        if audit_metadata.get("live_jobs_enabled") != expected_live_enabled:
            mismatches.append(f"live_jobs_enabled={audit_metadata.get('live_jobs_enabled')} expected {expected_live_enabled}")
        if audit_metadata.get("live_jobs_disabled") != expected_live_disabled:
            mismatches.append(f"live_jobs_disabled={audit_metadata.get('live_jobs_disabled')} expected {expected_live_disabled}")
        reported_disabled_names = sorted(audit_metadata.get("disabled_job_names") or [])
        if reported_disabled_names != expected_disabled_names:
            mismatches.append(f"disabled_job_names={reported_disabled_names} expected {expected_disabled_names}")

        if mismatches:
            persisted_disabled_names = sorted(
                job.get("name")
                for job in (persisted_jobs.get("jobs", []) or [])
                if not job.get("enabled", True) and job.get("name")
            )
            issues.append({
                "job_id": "__artifacts__",
                "name": "agent_architecture_json",
                "category": "artifact_contract_fail",
                "path": str(ROOT / "agents/system/logs/agent_architecture_latest.json"),
                "last_error": "; ".join(mismatches),
                "likely_cause": (
                    "Architecture audit metadata disagrees with live Gateway cron topology. "
                    f"Live disabled jobs={expected_disabled_names}, persisted historical disabled jobs={persisted_disabled_names}"
                ),
            })

    issues.extend(inspect_soft_offer_phrase_guard())

    verifier = subprocess.run(
        ["python3", str(ARCHITECTURE_VERIFIER_SCRIPT)],
        capture_output=True,
        text=True,
    )
    if verifier.returncode != 0:
        detail = (verifier.stdout or verifier.stderr).strip()
        issues.append({
            "job_id": "__artifacts__",
            "name": "agent_architecture_verifier_runtime",
            "category": "artifact_contract_fail",
            "path": str(ARCHITECTURE_VERIFIER_SCRIPT),
            "last_error": detail[:500],
            "likely_cause": "Live architecture verifier currently fails; stale or contradictory architecture signoff must not be treated as healthy",
        })
    return issues

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

        if category == "artifact_contract_fail" and name == "agent_architecture_verifier_runtime":
            code, out, err = subprocess.run(
                ["python3", str(ARCHITECTURE_INDEPENDENT_VERIFY_SCRIPT)],
                capture_output=True,
                text=True,
            ).returncode, None, None
            repairs.append({
                "type": "rerun_independent_architecture_verification",
                "job": name,
                "ok": code == 0,
                "detail": "python3 agents/system/agent_architecture_independent_verify.py",
            })
            verifier = subprocess.run(
                ["python3", str(ARCHITECTURE_VERIFIER_SCRIPT)],
                capture_output=True,
                text=True,
            )
            repairs.append({
                "type": "rerun_architecture_verifier",
                "job": name,
                "ok": verifier.returncode == 0,
                "detail": ((verifier.stdout or verifier.stderr).strip())[:300],
            })
            continue

        if category == "loop_verification_fail" and name == "docs_agentic_review":
            verifier = subprocess.run(
                ["python3", str(DOCS_VERIFIER_SCRIPT)],
                capture_output=True,
                text=True,
            )
            repairs.append({
                "type": "rerun_docs_verifier",
                "job": name,
                "ok": verifier.returncode == 0,
                "detail": ((verifier.stdout or verifier.stderr).strip())[:300],
            })
            continue

        if category == "loop_verification_fail" and name == "docs_verifier_stability":
            verifier = subprocess.run(
                ["python3", str(DOCS_VERIFIER_SCRIPT)],
                capture_output=True,
                text=True,
            )
            repairs.append({
                "type": "rerun_docs_verifier_for_stability",
                "job": name,
                "ok": verifier.returncode == 0,
                "detail": ((verifier.stdout or verifier.stderr).strip())[:300],
            })
            continue

        if category == "review_followup_required":
            owner_job = owner_job_for_issue(issue)
            blockers = escalation_blockers(issue)
            if owner_job:
                cooldown_minutes = owner_action_cooldown_minutes(issue)
                recent, recent_action = owner_action_recent(
                    issue,
                    action_type='immediate_review_followup_owner_action',
                    cooldown_minutes=cooldown_minutes,
                )
                if recent:
                    repairs.append({
                        "type": "immediate_review_followup_owner_action_cooldown",
                        "job": name,
                        "ok": True,
                        "detail": f"owner={owner_job}; cooldown_minutes={cooldown_minutes}; last_action_at={recent_action.get('at')}"[:300],
                    })
                    continue
                ok, detail = run_owner_job(owner_job)
                repairs.append({
                    "type": "immediate_review_followup_owner_action",
                    "job": name,
                    "ok": ok,
                    "detail": f"owner={owner_job}; {detail}"[:300],
                })
                record_owner_action(
                    issue,
                    action_type='immediate_review_followup_owner_action',
                    ok=ok,
                    detail=detail,
                    outcome='blocked_external' if blockers else 'no_progress',
                    blocked_by=blockers,
                )
                if ok:
                    continue

        if category == "escalation_required":
            owner_job = owner_job_for_issue(issue)
            blockers = escalation_blockers(issue)
            if owner_job:
                cooldown_minutes = owner_action_cooldown_minutes(issue)
                recent, recent_action = owner_action_recent(
                    issue,
                    action_type='owner_loop_escalation',
                    cooldown_minutes=cooldown_minutes,
                )
                if recent:
                    repairs.append({
                        "type": "owner_loop_escalation_cooldown",
                        "job": name,
                        "ok": True,
                        "detail": f"owner={owner_job}; cooldown_minutes={cooldown_minutes}; last_action_at={recent_action.get('at')}"[:300],
                    })
                    continue
                ok, detail = run_owner_job(owner_job)
                outcome = 'blocked_external' if blockers else ('resolved' if ok else 'no_progress')
                repairs.append({
                    "type": "owner_loop_escalation",
                    "job": name,
                    "ok": ok,
                    "detail": f"owner={owner_job}; {detail}"[:300],
                })
                record_owner_action(issue, action_type='owner_loop_escalation', ok=ok, detail=detail, outcome=outcome, blocked_by=blockers)
                if ok:
                    continue

            if "docs" in name:
                verifier = subprocess.run(
                    ["python3", str(DOCS_VERIFIER_SCRIPT)],
                    capture_output=True,
                    text=True,
                )
                repairs.append({
                    "type": "direct_docs_escalation",
                    "job": name,
                    "ok": verifier.returncode == 0,
                    "detail": ((verifier.stdout or verifier.stderr).strip())[:300],
                })
                record_owner_action(issue, action_type='direct_docs_escalation', ok=verifier.returncode == 0, detail=((verifier.stdout or verifier.stderr).strip())[:300], outcome='resolved' if verifier.returncode == 0 else 'no_progress')
                continue

            if "architecture" in name:
                verifier = subprocess.run(
                    ["python3", str(ARCHITECTURE_INDEPENDENT_VERIFY_SCRIPT)],
                    capture_output=True,
                    text=True,
                )
                repairs.append({
                    "type": "direct_architecture_escalation",
                    "job": name,
                    "ok": verifier.returncode == 0,
                    "detail": ((verifier.stdout or verifier.stderr).strip())[:300],
                })
                architecture_blockers = escalation_blockers(issue)
                record_owner_action(issue, action_type='direct_architecture_escalation', ok=verifier.returncode == 0, detail=((verifier.stdout or verifier.stderr).strip())[:300], outcome='blocked_external' if architecture_blockers else ('resolved' if verifier.returncode == 0 else 'no_progress'), blocked_by=architecture_blockers)
                continue

        if category == "live_error" and name == "codeberg-github-mirror-sync":
            direct = subprocess.run(
                ["bash", str(MIRROR_SYNC_SCRIPT)],
                capture_output=True,
                text=True,
            )
            healthy, proof = mirror_sync_is_healthy()
            repairs.append({
                "type": "direct_mirror_sync_verification",
                "job": name,
                "ok": direct.returncode == 0 and healthy,
                "detail": (proof + (f"; script_output={(direct.stdout or direct.stderr).strip()[:180]}" if (direct.stdout or direct.stderr).strip() else ""))[:300],
            })
            continue

        if category in {"timeout", "timeout_risk"} and job_id and job_id != "__artifacts__":
            job = by_name.get(name, {})
            payload = job.get("payload", {}) or {}
            timeout = int(payload.get("timeoutSeconds") or 0)
            desired = min(max(timeout * 2, 600), 3600) if timeout else 600
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

    return repairs


def refresh_resolved_issues(existing_issues, repairs, jobs):
    if not repairs:
        return existing_issues

    refreshed_jobs = get_cron_jobs()
    if refreshed_jobs and "jobs" in refreshed_jobs:
        jobs = refreshed_jobs

    refreshed_job_issues = []
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

        if is_gateway_restart_interrupt(last_error) and consecutive_errors <= 1:
            continue

        if not enabled:
            continue

        if is_key and error_reason == "timeout" and consecutive_errors >= 1 and last_error:
            refreshed_job_issues.append({
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

        if is_key and last_status == "error" and consecutive_errors >= 1 and last_error:
            if name == "codeberg-github-mirror-sync":
                healthy, proof = mirror_sync_is_healthy()
                if healthy:
                    continue
                refreshed_job_issues.append({
                    "job_id": job["id"],
                    "name": name,
                    "category": "live_error",
                    "consecutive_errors": consecutive_errors,
                    "last_status": last_status,
                    "last_error": last_error,
                    "timeout_seconds": timeout,
                    "last_duration_ms": last_duration,
                    "likely_cause": proof,
                })
                continue
            likely_cause = diagnose_issue(timeout, last_duration, last_error)
            if likely_cause.startswith("CLASSIFIER_FALSE_POSITIVE:"):
                continue
            refreshed_job_issues.append({
                "job_id": job["id"],
                "name": name,
                "category": "live_error",
                "consecutive_errors": consecutive_errors,
                "last_status": last_status,
                "last_error": last_error,
                "timeout_seconds": timeout,
                "last_duration_ms": last_duration,
                "likely_cause": likely_cause,
            })
            continue

        if is_key and timeout > 0 and last_duration > 0:
            ratio = last_duration / (timeout * 1000)
            if ratio >= 0.85 and error_reason != "timeout" and not is_gateway_restart_interrupt(last_error):
                refreshed_job_issues.append({
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
            refreshed_job_issues.append({
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

    refreshed_artifact_issues = inspect_artifacts()
    return refreshed_job_issues + refreshed_artifact_issues


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

        if is_gateway_restart_interrupt(last_error) and consecutive_errors <= 1:
            continue

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

        if is_key and last_status == "error" and consecutive_errors >= 1 and last_error:
            likely_cause = diagnose_issue(timeout, last_duration, last_error)
            category = "resolved_wrapper_error" if likely_cause.startswith("CLASSIFIER_FALSE_POSITIVE:") else "live_error"
            issues.append({
                "job_id": job["id"],
                "name": name,
                "category": category,
                "consecutive_errors": consecutive_errors,
                "last_status": last_status,
                "last_error": last_error,
                "timeout_seconds": timeout,
                "last_duration_ms": last_duration,
                "likely_cause": likely_cause,
            })
            continue

        if is_key and timeout > 0 and last_duration > 0:
            ratio = last_duration / (timeout * 1000)
            if ratio >= 0.85 and error_reason != "timeout" and not is_gateway_restart_interrupt(last_error):
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

    for issue in issues:
        if issue.get("name") == "codeberg-github-mirror-sync" and issue.get("category") == "live_error":
            healthy, proof = mirror_sync_is_healthy()
            if healthy:
                issue["category"] = "resolved_wrapper_error"
                issue["likely_cause"] = f"WRAPPER_FALSE_NEGATIVE: {proof}"

    issues = [issue for issue in issues if issue.get("category") != "resolved_wrapper_error"]
    all_issues = list(issues) + inspect_artifacts()
    all_issues.extend(incident_escalations(all_issues))
    repairs = apply_safe_repairs(all_issues, jobs)
    all_issues = refresh_resolved_issues(all_issues, repairs, jobs)
    all_issues.extend(incident_escalations(all_issues))

    log_entry = {
        "ts": datetime.now().isoformat(),
        "jobs_checked": len(jobs.get("jobs", [])),
        "issues_found": len(all_issues),
        "issues": all_issues,
        "repairs_attempted": repairs,
    }

    LATEST_FILE.write_text(json.dumps(log_entry, indent=2) + "\n", encoding="utf-8")
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    if all_issues:
        print(f"\u26a0\ufe0f  SYSTEM HEALTH: {len(all_issues)} job issue(s):")
        for issue in all_issues:
            ratio_str = ""
            if issue.get("duration_ratio"):
                ratio_str = f" ({issue['duration_ratio']*100:.0f}% of timeout)"
            print(f"  [{issue['category']}] {issue['name']}: {issue.get('consecutive_errors', 0)} errors, {issue.get('likely_cause', '')}{ratio_str}")
            if "last_duration_ms" in issue or "timeout_seconds" in issue:
                print(f"    Duration: {issue.get('last_duration_ms', 'n/a')}ms / timeout: {issue.get('timeout_seconds', 'n/a')}s")
            elif issue.get("path"):
                print(f"    Artifact: {issue['path']}")
        if repairs:
            print("  [auto_repair] attempted:")
            for repair in repairs:
                print(f"    - {repair['type']} on {repair['job']}: {'ok' if repair['ok'] else 'failed'}")
    else:
        print("\u2705 System health OK - no issues detected")

    return 1 if all_issues else 0

def diagnose_issue(timeout_seconds, last_duration_ms, last_error):
    error_lower = (last_error or "").lower()

    if is_gateway_restart_interrupt(last_error):
        return "GATEWAY_RESTART_INTERRUPT: transient scheduler interruption; rerun or wait for next scheduled execution before treating as a real job fault"

    if last_error == "cron: job execution timed out":
        if timeout_seconds and last_duration_ms > (timeout_seconds * 1000 * 0.9):
            return f"TIMEOUT_TOO_TIGHT: script took {last_duration_ms}ms but limit was {timeout_seconds}s - needs more headroom"
        if not timeout_seconds:
            return f"TIMEOUT: script timed out after {last_duration_ms}ms and timeout config could not be read"
        return "TIMEOUT: script is hanging, check for infinite loops or network calls"

    if "cron classifier: denial token" in error_lower:
        return "CLASSIFIER_FALSE_POSITIVE: job likely succeeded but used blocked summary wording"

    if "usage limit exceeded" in error_lower or "rate limit" in error_lower or "token plan" in error_lower:
        return "RATE_LIMIT: upstream model/provider quota exhausted; reroute to another model/provider or wait for reset"

    if "overloaded" in error_lower or "temporarily overloaded" in error_lower:
        return "SERVICE_OVERLOADED: upstream AI service was overloaded; rerun or add retry/backoff if persistent"

    if "context overflow" in error_lower or "prompt too large" in error_lower:
        return "CONTEXT_OVERFLOW: prompt/runtime context exceeded safe model limit; reduce prompt size or split work"

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
