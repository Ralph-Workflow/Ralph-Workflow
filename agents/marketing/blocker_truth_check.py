#!/usr/bin/env python3
"""
Ground-truth blocker checker — independently verifies every claimed blocker
against external APIs so the system never runs on false-blocked intelligence.

Prevents class of bug: PyPI v0.8.8 was live for 24+ hours while 15+ artifacts
claimed it was blocked on PYPI_TOKEN. The auto-unblocker succeeded silently
but the monitor never cross-checked the external API.

Run: python3 agents/marketing/blocker_truth_check.py
Output: agents/marketing/logs/blocker_truth_latest.json
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
OUTPUT_PATH = os.path.join(LOG_DIR, "blocker_truth_latest.json")

def check_pypi():
    """Verify PyPI: is the latest version published? What's the README link?"""
    try:
        req = urllib.request.Request(
            "https://pypi.org/pypi/ralph-workflow/json",
            headers={"User-Agent": "RalphWorkflow-BlockerTruthCheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        
        version = data.get("info", {}).get("version", "unknown")
        upload_time = None
        if version in data.get("releases", {}):
            releases = data["releases"][version]
            if releases:
                upload_time = releases[-1].get("upload_time", None)
        
        project_urls = data.get("info", {}).get("project_urls", {})
        readme = data.get("info", {}).get("description", "")
        
        has_codeberg = "codeberg" in readme.lower()
        has_github = "github" in readme.lower()
        codeberg_is_primary = has_codeberg and (
            "codeberg is the primary" in readme.lower() or
            "github is the mirror" in readme.lower() or
            "github mirror" in readme.lower()
        )
        
        return {
            "status": "live",
            "version": version,
            "upload_time": upload_time,
            "has_codeberg_link": has_codeberg,
            "has_github_link": has_github,
            "codeberg_is_primary": codeberg_is_primary,
            "blocker": False,
            "note": f"v{version} is live on PyPI. Codeberg CTA present: {codeberg_is_primary}"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "blocker": True,
            "note": "Cannot reach PyPI API — treat as undetermined, not blocked"
        }

def check_so_api():
    """Verify StackExchange API is reachable and quota is healthy."""
    try:
        req = urllib.request.Request(
            "https://api.stackexchange.com/2.3/info?site=stackoverflow",
            headers={"User-Agent": "RalphWorkflow-BlockerTruthCheck/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        
        quota_remaining = data.get("quota_remaining", 0)
        quota_max = data.get("quota_max", 0)
        
        return {
            "status": "live",
            "quota_remaining": quota_remaining,
            "quota_max": quota_max,
            "quota_pct": round(quota_remaining / quota_max * 100, 1) if quota_max else 0,
            "blocker": False,
            "note": f"StackExchange API: {quota_remaining}/{quota_max} remaining"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "blocker": True,
            "note": "StackExchange API unreachable"
        }

def check_codeberg_repo():
    """Verify Codeberg repo adoption metrics via API."""
    try:
        req = urllib.request.Request(
            "https://codeberg.org/api/v1/repos/RalphWorkflow/Ralph-Workflow",
            headers={"User-Agent": "RalphWorkflow-BlockerTruthCheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        
        return {
            "status": "live",
            "stars": data.get("stars_count", 0),
            "watchers": data.get("watchers_count", 0),
            "forks": data.get("forks_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "blocker": False
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "blocker": False,
            "note": "Codeberg API unreachable — adoption metrics from cache"
        }

def check_github_mirror():
    """Verify GitHub mirror status."""
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/Ralph-Workflow/Ralph-Workflow",
            headers={"User-Agent": "RalphWorkflow-BlockerTruthCheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        
        return {
            "status": "live",
            "stars": data.get("stargazers_count", 0),
            "watchers": data.get("watchers_count", 0),
            "forks": data.get("forks_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "blocker": False
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "blocker": False,
            "note": "GitHub API unreachable"
        }

def check_blog_sitemap():
    """Verify blog is accessible and count posts via sitemap."""
    try:
        req = urllib.request.Request(
            "https://ralphworkflow.com/sitemap.xml",
            headers={"User-Agent": "RalphWorkflow-BlockerTruthCheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
        
        import re
        blog_urls = re.findall(r'https://ralphworkflow\.com/blog/[^<]+', body)
        all_urls = re.findall(r'<loc>([^<]+)</loc>', body)
        
        return {
            "status": "live",
            "total_urls": len(all_urls),
            "blog_urls": len(blog_urls),
            "blocker": False,
            "note": f"Sitemap: {len(all_urls)} URLs, {len(blog_urls)} blog posts"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "blocker": True,
            "note": "Cannot reach sitemap"
        }

def check_environment_blockers():
    """Check runtime environment blockers (non-API)."""
    blockers = {}
    
    # gh auth
    import subprocess
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=10)
        blockers["gh_auth"] = {
            "status": "live" if result.returncode == 0 else "blocked",
            "detail": result.stdout.strip()[:200] if result.returncode == 0 else result.stderr.strip()[:200],
            "blocker": result.returncode != 0
        }
    except FileNotFoundError:
        blockers["gh_auth"] = {"status": "not_installed", "detail": "gh CLI not found", "blocker": True}
    except Exception as e:
        blockers["gh_auth"] = {"status": "error", "detail": str(e), "blocker": True}
    
    # SMTP
    smtp_user = os.environ.get("SMTP_USER", "")
    blockers["smtp"] = {
        "status": "blocked" if not smtp_user else "configured",
        "blocker": not bool(smtp_user),
        "note": "SMTP_USER env var not set" if not smtp_user else "SMTP_USER configured"
    }
    
    # PYPI_TOKEN
    pypi_token = os.environ.get("PYPI_TOKEN", "")
    blockers["pypi_token"] = {
        "status": "blocked" if not pypi_token else "configured",
        "blocker": not bool(pypi_token),
        "note": "PYPI_TOKEN env var not set. (PyPI v0.8.8 IS live — this is checked separately by check_pypi())"
    }
    
    return blockers

def run_all_checks():
    """Run all ground-truth checks and aggregate results."""
    results = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": {}
    }
    
    results["checks"]["pypi_api"] = check_pypi()
    results["checks"]["stackexchange_api"] = check_so_api()
    results["checks"]["codeberg_repo"] = check_codeberg_repo()
    results["checks"]["github_mirror"] = check_github_mirror()
    results["checks"]["blog_sitemap"] = check_blog_sitemap()
    results["checks"]["environment"] = check_environment_blockers()
    
    # Summarize: which are actually blocked vs falsely reported
    actual_blockers = []
    false_blockers = []
    
    for check_name, check_data in results["checks"].items():
        if check_name == "environment":
            for env_key, env_data in check_data.items():
                if env_data.get("blocker"):
                    actual_blockers.append(f"env:{env_key}")
        elif check_data.get("blocker"):
            actual_blockers.append(check_name)
        elif check_data.get("status") == "live":
            false_blockers.append(check_name)
    
    results["summary"] = {
        "actual_blockers": actual_blockers,
        "verified_live": false_blockers,
        "total_checks": len(results["checks"]),
        "blocker_count": len(actual_blockers),
        "live_count": len(false_blockers)
    }
    
    # Cross-reference: if BLOCKER_ROI_SUMMARY still lists PyPI as blocked, flag it
    blocker_summary_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "BLOCKER_ROI_SUMMARY.md"
    )
    if os.path.exists(blocker_summary_path):
        with open(blocker_summary_path) as f:
            content = f.read()
        results["cross_ref"] = {
            "pypi_still_listed_as_blocked": "PYPI_TOKEN" in content and "v0.8.8 live" not in content,
            "blocker_summary_synced": "v0.8.8 live" in content or "RESOLVED" in content
        }
    
    return results

if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    results = run_all_checks()
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"Blocker truth check complete → {OUTPUT_PATH}")
    print(f"Blockers: {results['summary']['blocker_count']}")
    print(f"Verified live: {results['summary']['live_count']}")
    for b in results["summary"]["actual_blockers"]:
        print(f"  BLOCKED: {b}")
    for l in results["summary"]["verified_live"]:
        print(f"  LIVE: {l}")
    
    sys.exit(0 if results["summary"]["blocker_count"] == 0 else 1)
