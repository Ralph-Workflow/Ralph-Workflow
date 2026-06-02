#!/usr/bin/env python3
"""
Repo Conversion Optimizer — the ONLY autonomous lane that directly targets the primary metric.

PyPI delivers 127 downloads/day. Codeberg gets 0 stars. Conversion is broken.
This lane monitors the repo conversion surface (README, PyPI page) via Codeberg API
and proposals measurable optimizations.

Cron: Weekly (Sun 08:00) — repo README changes are high-impact, low-frequency.
"""

from __future__ import annotations
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / 'agents' / 'marketing' / 'logs' / 'repo_conversion_latest.json'

# The Ralph-Workflow repo is on Codeberg. We fetch via their raw API.
CODEBERG_README_RAW = "https://codeberg.org/RalphWorkflow/Ralph-Workflow/raw/branch/main/README.md"
CODEBERG_API = "https://codeberg.org/api/v1/repos/RalphWorkflow/Ralph-Workflow"
PYPI_API = "https://pypi.org/pypi/ralph-workflow/json"
PYPI_PAGE = "https://pypi.org/project/ralph-workflow/"

CONVERSION_ELEMENTS = [
    # (identifier, check_pattern, description)
    ("star_cta", r"⭐|star|give (?:us )?a star|star this repo|star_it|stargazer", "Explicit star/watch CTA"),
    ("codeberg_primary", r"Codeberg.*primary|primary repo.*Codeberg|Codeberg-first", "Codeberg-as-primary messaging"),
    ("github_mirror", r"GitHub.*mirror|mirror.*GitHub", "GitHub-as-mirror messaging"),
    ("quickstart", r"pipx install|pip install|docker run|git clone", "Install/quickstart path"),
    ("what_is_it", r"operating system for autonomous coding|composable loop framework|AI orchestrator", "Core positioning statement"),
    ("first_task", r"first task|first run|get started|quickstart|try it|example|start here", "First-task guidance"),
    ("pain_frames", r"too big to babysit|too risky to trust|walk away.*come back|wake up to", "Pain-led positioning"),
    ("proof_asset", r"example|before.*after|real.*task|overnight|case study", "Proof/demo assets"),
    ("foss_clarity", r"free and open.source|FOSS|MIT|Apache|AGPL|GPL|free software", "License/free clarity"),
]


def _fetch_text(url: str, timeout: int = 15) -> Optional[str]:
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RepoConversionOptimizer/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except Exception as e:
        return None


def _check_text(text: str, label: str) -> dict:
    if not text:
        return {"label": label, "exists": False, "issues": ["NO_CONTENT_FETCHED"]}

    elements = {}
    for ident, pattern, desc in CONVERSION_ELEMENTS:
        found = bool(re.search(pattern, text, re.IGNORECASE))
        elements[ident] = {"present": found, "description": desc}

    issues = []
    for ident, data in elements.items():
        if not data["present"]:
            issues.append(f"MISSING: {data['description']} ({ident})")

    return {
        "label": label,
        "exists": True,
        "size_bytes": len(text),
        "elements": elements,
        "issues": issues,
        "ok": len(issues) == 0,
    }


def _check_pypi() -> dict:
    raw = _fetch_text(PYPI_API)
    if not raw:
        return {"ok": False, "error": "PyPI API fetch failed"}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON decode: {e}"}

    info = data.get("info", {})
    desc = (info.get("description") or "")
    project_urls = info.get("project_urls") or {}

    codeberg_refs = desc.lower().count("codeberg")
    pypi_good = codeberg_refs >= 1

    return {
        "ok": True,
        "version": info.get("version"),
        "codeberg_references": codeberg_refs,
        "codeberg_in_project_urls": any("codeberg" in str(v).lower() for v in project_urls.values()),
        "pypi_ready": pypi_good,
        "issues": [] if pypi_good else ["PyPI description has no Codeberg reference"],
    }


def main() -> int:
    now = datetime.now(timezone.utc)

    print("=== Repo Conversion Optimizer ===")

    # Fetch Codeberg README
    readme_text = _fetch_text(CODEBERG_README_RAW)
    cb_readme = _check_text(readme_text or "", "Codeberg README (live)")

    # Check PyPI
    pypi = _check_pypi()

    # Check live Codeberg metrics
    cb_raw = _fetch_text(CODEBERG_API)
    cb = {"stars": -1, "watchers": -1}
    if cb_raw:
        try:
            data = json.loads(cb_raw)
            cb = {
                "stars": data.get("stars_count", -1),
                "watchers": data.get("watchers_count", -1),
                "forks": data.get("forks_count", -1),
                "description": data.get("description", ""),
            }
        except Exception:
            pass

    all_issues = cb_readme.get("issues", [])
    conversion_available = len(all_issues) > 0

    result = {
        "generated_at": now.isoformat(),
        "codeberg_metrics": cb,
        "codeberg_readme": cb_readme,
        "pypi": pypi,
        "total_issues": len(all_issues),
        "conversion_optimizations_available": conversion_available,
        "actions_taken": [],
    }

    if conversion_available:
        result["recommended_actions"] = all_issues

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(result, indent=2))

    # Print summary
    print(f"  Codeberg: {cb.get('stars')}⭐ {cb.get('watchers')} watchers {cb.get('forks')} forks")
    print(f"  README: {len(all_issues)} conversion elements missing")
    print(f"  PyPI: {'OK' if pypi.get('pypi_ready') else 'ISSUES'}")

    if all_issues:
        print(f"\n  Conversion gaps:")
        for i in all_issues:
            print(f"    - {i}")
        print(f"\n  Log → {LOG_PATH}")

    if "NO_CONTENT_FETCHED" in str(all_issues):
        print(f"\n  ⚠ Could not fetch README from Codeberg API. Check connectivity.")
        return 1

    if all_issues and "NO_CONTENT_FETCHED" not in str(all_issues):
        print(f"\n  🎯 These are fixable conversion surface improvements. Each gap = lost star opportunity.")
    else:
        print(f"  ✅ Conversion surface fully optimized.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
