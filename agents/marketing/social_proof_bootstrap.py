#!/usr/bin/env python3
"""
social_proof_bootstrap.py — Autonomous trust-signal construction agent.

Created: 2026-06-02 (audit #20)
Triggered by: 19 audits with zero Codeberg delta. Social proof identified as
the conversion bottleneck, but no autonomous agent existed to BUILD it — only
agents that AUDIT it (repo_conversion_optimizer reads, doesn't write).

This agent:
1. Audits ALL public trust surfaces (Codeberg README, PyPI, docs footer, blog
   CTAs, /compare page, /install page, /start page)
2. Identifies which surfaces have NO Codeberg star/fork/watch CTAs
3. Produces concrete, deployable changes to add CTAs
4. Commits + pushes changes when possible
5. Reports what changed, what's saturated (no gaps), and what's human-gated

Design principles:
- WRITE-first, not read-only. The old optimizer only checked; this builds.
- Whole-surface aware. Trust signals on docs pages don't help if docs have no CTA.
- Autonomous where possible; clear handoff where human-gated.

Repo surfaces reachable:
- Codeberg repo: README.md, AGENTS.md (via Codeberg API → git push)
- Ralph-Site: docs footer template, blog CTA partial, layout files (via git@git.sellogic.ai)
- PyPI: currently read-only (token missing), monitor-only
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

SCRIPT_NAME = Path(__file__).name
ROOT = Path("/home/mistlight/.openclaw/workspace")
LOG_DIR = ROOT / "agents/marketing/logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

STATUS_PATH = LOG_DIR / "social_proof_bootstrap_latest.json"
SITE_REPO = ROOT / "Ralph-Site"  # corrected 2026-06-03: was stale tmp/ralph-site-remote-check clone

CODEBERG_REPO = "RalphWorkflow/Ralph-Workflow"
CODEBERG_API = f"https://codeberg.org/api/v1/repos/{CODEBERG_REPO}"
PYPI_PROJECT = "ralph-workflow"
PYPI_API = f"https://pypi.org/pypi/{PYPI_PROJECT}/json"

# Public surface URLs to audit
AUDIT_SURFACES = [
    {
        "name": "codeberg_readme",
        "url": f"https://codeberg.org/{CODEBERG_REPO}",
        "api": CODEBERG_API,
        "description": "Codeberg repo README — primary conversion surface",
    },
    {
        "name": "pypi_page",
        "url": f"https://pypi.org/project/{PYPI_PROJECT}/",
        "api": PYPI_API,
        "description": "PyPI project page — 1,329 downloads/month pass through here",
    },
    {
        "name": "docs_footer",
        "url": "https://ralphworkflow.com/docs/",
        "local_path": SITE_REPO / "docs/sphinx_overrides/_themes/ralph-docs/page.html",
        "description": "Sphinx docs footer — controls all 49 docs pages",
    },
    {
        "name": "blog_cta",
        "url": "https://ralphworkflow.com/blog/",
        "local_paths": [
            SITE_REPO / "app/views/blog/_blog_repo_cta.html.erb",
            SITE_REPO / "app/views/blog/show.html.erb",
            SITE_REPO / "app/views/layouts/blog.html.erb",
        ],
        "description": "Blog CTA partial — appears on all 44 blog posts",
    },
    {
        "name": "compare_page",
        "url": "https://ralphworkflow.com/compare",
        "local_paths": [
            SITE_REPO / "app/views/pages/compare.html.erb",
        ],
        "description": "/compare page — competitive positioning surface",
    },
    {
        "name": "install_page",
        "url": "https://ralphworkflow.com/install",
        "local_paths": [
            SITE_REPO / "app/views/pages/install.html.erb",
        ],
        "description": "/install page — last surface before pipx install",
    },
    {
        "name": "start_page",
        "url": "https://ralphworkflow.com/start",
        "local_paths": [
            SITE_REPO / "app/views/pages/start.html.erb",
        ],
        "description": "/start page — onboarding entry point",
    },
    {
        "name": "homepage",
        "url": "https://ralphworkflow.com/",
        "local_paths": [
            SITE_REPO / "app/views/pages/home.html.erb",
            SITE_REPO / "app/views/layouts/application.html.erb",
        ],
        "description": "Homepage — highest-traffic page",
    },
    {
        "name": "contact_page",
        "url": "https://ralphworkflow.com/contact",
        "local_paths": [
            SITE_REPO / "app/views/pages/contact.html.erb",
        ],
        "description": "/contact page — inquiry surface",
    },
    {
        "name": "privacy_page",
        "url": "https://ralphworkflow.com/privacy",
        "local_paths": [
            SITE_REPO / "app/views/pages/privacy.html.erb",
        ],
        "description": "/privacy page — often linked from repo",
    },
    {
        "name": "terms_page",
        "url": "https://ralphworkflow.com/terms",
        "local_paths": [
            SITE_REPO / "app/views/pages/terms.html.erb",
        ],
        "description": "/terms page — often linked from repo",
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_url(url: str, timeout: int = 15) -> tuple[int, str]:
    """Fetch a URL, return (status_code, text)."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "RalphWorkflow-SocialProof-Bootstrap/1.0"})
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)


def _check_cta_presence(text: str) -> dict[str, Any]:
    """Check a text body for Codeberg CTAs."""
    tl = text.lower()
    cta_types = {
        "codeberg_star": "star" in tl and "codeberg" in tl,
        "codeberg_fork": "fork" in tl and "codeberg" in tl,
        "codeberg_watch": "watch" in tl and "codeberg" in tl,
        "codeberg_mention": "codeberg" in tl,
        "github_mirror_mention": ("github" in tl and ("mirror" in tl or "github.com/ralph-workflow" in tl)),
        "pip_install_cta": "pip install" in tl or "pipx install" in tl,
        "star_emoji": "⭐" in text or "★" in text,
        "repo_cta_any": (
            ("star" in tl and "repo" in tl)
            or ("check out" in tl and "codeberg" in tl)
            or ("visit" in tl and "codeberg" in tl)
        ),
    }
    present = [k for k, v in cta_types.items() if v]
    missing_ctas = [k for k, v in cta_types.items() if not v]
    return {
        "cta_types_found": present,
        "cta_types_missing": missing_ctas,
        "total_cta_types": len(cta_types),
        "cta_types_present": len(present),
        "coverage_pct": round(len(present) / max(len(cta_types), 1) * 100, 1),
        "all_critical_present": cta_types["codeberg_star"] and cta_types["codeberg_mention"],
    }


def audit_codeberg_readme() -> dict[str, Any]:
    """Audit Codeberg repo README for trust signals."""
    result = {
        "surface": "codeberg_readme",
        "timestamp": _now_iso(),
        "reachable": False,
        "cta_analysis": {},
        "gaps": [],
    }

    # Fetch repo API
    try:
        r = requests.get(CODEBERG_API, timeout=15, headers={"Accept": "application/json"})
        if r.status_code == 200:
            repo = r.json()
            result["reachable"] = True
            result["stars"] = repo.get("stars_count", 0)
            result["watchers"] = repo.get("watchers_count", 0)
            result["forks"] = repo.get("forks_count", 0)
            result["description"] = repo.get("description", "")
        else:
            result["error"] = f"API returned {r.status_code}"
    except Exception as e:
        result["error"] = str(e)

    # Fetch raw README
    try:
        raw_url = f"https://codeberg.org/{CODEBERG_REPO}/raw/branch/main/README.md"
        r = requests.get(raw_url, timeout=15)
        if r.status_code == 200:
            readme = r.text
            result["cta_analysis"] = _check_cta_presence(readme)
            result["readme_length"] = len(readme)

            # Check specific trust signals
            signals = {
                "pypi_downloads_badge": "pypi/dm/ralph-workflow" in readme or "pepy.tech" in readme,
                "docker_quickstart": "docker" in readme.lower() and ("docker run" in readme.lower() or "docker pull" in readme.lower()),
                "example_output": "example" in readme.lower() and ("output" in readme.lower() or "review" in readme.lower()),
                "comparison_links": "compare" in readme.lower() or "vs-" in readme.lower(),
                "docs_link": "ralphworkflow.com/docs" in readme,
                "install_quick": "pipx install" in readme or "pip install" in readme,
                "star_cta_explicit": "⭐" in readme or "star this repo" in readme.lower() or "give it a star" in readme.lower(),
                "performance_evidence": "overnight" in readme.lower() or "while you sleep" in readme.lower(),
            }
            result["trust_signals"] = signals
            missing_signals = [k for k, v in signals.items() if not v]
            result["gaps"] = missing_signals
        else:
            result["error"] = result.get("error", "") + f" README fetch: {r.status_code}"
    except Exception as e:
        result["error"] = result.get("error", "") + f" README: {e}"

    return result


def audit_docs_footer() -> dict[str, Any]:
    """Audit Sphinx docs footer template for repo CTAs."""
    result = {
        "surface": "docs_footer",
        "timestamp": _now_iso(),
        "template_exists": False,
        "template_path": str(SITE_REPO / "docs/sphinx_overrides/_themes/ralph-docs/page.html"),
        "has_codeberg_cta": False,
        "has_star_cta": False,
        "pages_controlled": 49,
        "gaps": [],
    }

    page_html = SITE_REPO / "docs/sphinx_overrides/_themes/ralph-docs/page.html"
    if not page_html.exists():
        result["error"] = "Template not found in local checkout"
        return result

    result["template_exists"] = True
    content = page_html.read_text(encoding="utf-8")

    # Check for existing CTAs in footer
    tl = content.lower()
    result["has_codeberg_cta"] = "codeberg" in tl
    result["has_star_cta"] = "star" in tl and "codeberg" in tl
    result["has_fork_cta"] = "fork" in tl and "codeberg" in tl
    result["has_footer_section"] = "ralph-page-footer" in tl

    # Check live docs page
    status, html = _fetch_url("https://ralphworkflow.com/docs/")
    if status == 200:
        tl2 = html.lower()
        result["live_has_codeberg"] = "codeberg" in tl2
        result["live_has_star"] = ("star" in tl2 and "codeberg" in tl2) or "⭐" in html

    if not result["has_codeberg_cta"]:
        result["gaps"].append("docs_footer_missing_codeberg_cta")
    if not result["has_star_cta"]:
        result["gaps"].append("docs_footer_missing_star_cta")

    return result


def audit_pypi_page() -> dict[str, Any]:
    """Audit PyPI page for trust signals."""
    result = {
        "surface": "pypi_page",
        "timestamp": _now_iso(),
        "reachable": False,
        "cta_analysis": {},
        "gaps": [],
    }

    try:
        r = requests.get(PYPI_API, timeout=15, headers={"Accept": "application/json"})
        if r.status_code == 200:
            data = r.json()
            result["reachable"] = True
            info = data.get("info", {})
            result["version"] = info.get("version", "unknown")
            result["description_preview"] = (info.get("description", "") or "")[:500]
            result["project_urls"] = info.get("project_urls", {})
            result["has_codeberg_in_urls"] = any("codeberg" in str(v).lower() for v in result["project_urls"].values())
            result["has_github_in_urls"] = any("github" in str(v).lower() for v in result["project_urls"].values())
            result["downloads_last_month"] = info.get("downloads", {}).get("last_month", "unknown")

            desc = info.get("description", "") or ""
            result["cta_analysis"] = _check_cta_presence(desc)
    except Exception as e:
        result["error"] = str(e)

    return result


def audit_site_surface(name: str, url: str, local_paths: list[Path]) -> dict[str, Any]:
    """Audit a Ralph-Site page for trust signals."""
    result = {
        "surface": name,
        "url": url,
        "timestamp": _now_iso(),
        "reachable": False,
        "has_codeberg_cta": False,
        "has_star_cta": False,
        "local_files_present": [],
        "local_files_missing": [],
        "gaps": [],
    }

    # Live check
    status, html = _fetch_url(url)
    if status == 200:
        result["reachable"] = True
        tl = html.lower()
        result["has_codeberg_cta"] = "codeberg" in tl
        result["has_star_cta"] = ("star" in tl and "codeberg" in tl) or "⭐" in html
        result["has_fork_cta"] = "fork" in tl and "codeberg" in tl
        result["page_size"] = len(html)
        result["title"] = ""
        import re
        tm = re.search(r"<title>([^<]+)</title>", html)
        if tm:
            result["title"] = tm.group(1).strip()

    # Local file check for editability
    for path in local_paths:
        if path.exists():
            result["local_files_present"].append(str(path))
        else:
            result["local_files_missing"].append(str(path))

    if not result["has_codeberg_cta"]:
        result["gaps"].append(f"{name}_missing_codeberg_cta")
    if not result["has_star_cta"]:
        result["gaps"].append(f"{name}_missing_star_cta")

    return result


def _git_pull_site_repo() -> bool:
    """Pull latest from Ralph-Site repo."""
    if not SITE_REPO.exists():
        return False
    try:
        subprocess.run(
            ["git", "-C", str(SITE_REPO), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=30,
        )
        return True
    except Exception:
        return False


def _git_commit_and_push(message: str, files: list[Path]) -> dict[str, Any]:
    """Commit and push changes to Ralph-Site repo."""
    result = {"committed": False, "pushed": False, "message": message, "files": [str(f) for f in files]}

    try:
        # Pull first
        subprocess.run(
            ["git", "-C", str(SITE_REPO), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=30,
        )

        # Check if there are changes
        status = subprocess.run(
            ["git", "-C", str(SITE_REPO), "diff", "--quiet"],
            capture_output=True, text=True, timeout=10,
        )
        if status.returncode == 0:
            result["note"] = "No changes to commit"
            return result

        # Stage files
        for f in files:
            if f.exists():
                subprocess.run(
                    ["git", "-C", str(SITE_REPO), "add", str(f.relative_to(SITE_REPO))],
                    capture_output=True, text=True, timeout=10,
                )

        # Commit
        commit = subprocess.run(
            ["git", "-C", str(SITE_REPO), "commit", "-m", message],
            capture_output=True, text=True, timeout=10,
        )
        if commit.returncode == 0:
            result["committed"] = True
            result["commit_output"] = commit.stdout.strip()

            # Push
            push = subprocess.run(
                ["git", "-C", str(SITE_REPO), "push", "origin", "main"],
                capture_output=True, text=True, timeout=60,
            )
            if push.returncode == 0:
                result["pushed"] = True
                result["push_output"] = push.stdout.strip()
            else:
                result["push_error"] = push.stderr.strip()
        else:
            result["commit_error"] = commit.stderr.strip()

    except Exception as e:
        result["error"] = str(e)

    return result


def inject_docs_footer_cta() -> dict[str, Any]:
    """
    Inject Codeberg star/fork/watch CTAs into the Sphinx docs footer template.
    This single change affects all 49 docs pages.

    Strategy: Add a "Powered by open source" footer block below the next/prev
    navigation that includes a Codeberg repo CTA with star/fork/watch links.
    The block uses the existing ralph-docs design language.
    """
    result = {
        "action": "inject_docs_footer_cta",
        "timestamp": _now_iso(),
        "template": str(SITE_REPO / "docs/sphinx_overrides/_themes/ralph-docs/page.html"),
        "applied": False,
    }

    page_html = SITE_REPO / "docs/sphinx_overrides/_themes/ralph-docs/page.html"
    if not page_html.exists():
        result["error"] = "Template not found"
        return result

    content = page_html.read_text(encoding="utf-8")

    # Check if CTA already present
    if "ralph-repo-cta" in content or "codeberg_repo_cta" in content.lower():
        result["already_present"] = True
        result["note"] = "Docs footer CTA already injected"
        return result

    # The CTA block goes after the ralph-page-footer nav and before the closing </footer>
    # Inline styles used because this Sphinx theme has no standalone CSS file
    # (CSS is Capistrano-assembled from _static/ at deploy time).
    cta_block = """
      {#- ── Codeberg repo CTA ─────────────────────────────────────────── #}
      <div style="margin-top:2.5rem;padding-top:1.5rem;border-top:1px solid var(--color-border-muted, #3a3d4a)">
        <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:0.75rem">
          <p style="margin:0;font-size:0.825rem;color:var(--color-fg-muted, #888ea6);line-height:1.5">
            <strong>Ralph Workflow</strong> is free and open source.
            Hosted on <a href="https://codeberg.org/RalphWorkflow/Ralph-Workflow" target="_blank" rel="noopener noreferrer" style="color:var(--color-accent-fg, #60a5fa);text-decoration:none;font-weight:600">Codeberg</a>
            &middot; mirrored on <a href="https://github.com/Ralph-Workflow/Ralph-Workflow" target="_blank" rel="noopener noreferrer" style="color:var(--color-accent-fg, #60a5fa);text-decoration:none;font-weight:600">GitHub</a>
          </p>
          <div style="display:flex;gap:0.5rem">
            <a href="https://codeberg.org/RalphWorkflow/Ralph-Workflow" target="_blank" rel="noopener noreferrer" title="Star on Codeberg" style="display:inline-flex;align-items:center;gap:0.35rem;padding:0.4rem 0.8rem;border:1px solid var(--color-border-default, #4a4d5a);border-radius:6px;font-size:0.75rem;font-weight:500;color:var(--color-fg-default, #e6e7eb);text-decoration:none;background:transparent;transition:border-color 0.15s,color 0.15s" onmouseover="this.style.borderColor='var(--color-accent-fg, #60a5fa)';this.style.color='var(--color-accent-fg, #60a5fa)'" onmouseout="this.style.borderColor='var(--color-border-default, #4a4d5a)';this.style.color='var(--color-fg-default, #e6e7eb)'">
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 22 12 18.56 5.82 22 7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
              <span>Star</span>
            </a>
            <a href="https://codeberg.org/RalphWorkflow/Ralph-Workflow" target="_blank" rel="noopener noreferrer" title="Fork on Codeberg" style="display:inline-flex;align-items:center;gap:0.35rem;padding:0.4rem 0.8rem;border:1px solid var(--color-border-default, #4a4d5a);border-radius:6px;font-size:0.75rem;font-weight:500;color:var(--color-fg-default, #e6e7eb);text-decoration:none;background:transparent;transition:border-color 0.15s,color 0.15s" onmouseover="this.style.borderColor='var(--color-accent-fg, #60a5fa)';this.style.color='var(--color-accent-fg, #60a5fa)'" onmouseout="this.style.borderColor='var(--color-border-default, #4a4d5a)';this.style.color='var(--color-fg-default, #e6e7eb)'">
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M6 9a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0v-1a3 3 0 0 0-3-3z"/><path d="M18 9a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0v-1a3 3 0 0 0-3-3z"/><path d="M12 5v3"/><path d="M9 12H6"/><path d="M18 12h-3"/></svg>
              <span>Fork</span>
            </a>
            <a href="https://codeberg.org/RalphWorkflow/Ralph-Workflow" target="_blank" rel="noopener noreferrer" title="Watch on Codeberg" style="display:inline-flex;align-items:center;gap:0.35rem;padding:0.4rem 0.8rem;border:1px solid var(--color-border-default, #4a4d5a);border-radius:6px;font-size:0.75rem;font-weight:500;color:var(--color-fg-default, #e6e7eb);text-decoration:none;background:transparent;transition:border-color 0.15s,color 0.15s" onmouseover="this.style.borderColor='var(--color-accent-fg, #60a5fa)';this.style.color='var(--color-accent-fg, #60a5fa)'" onmouseout="this.style.borderColor='var(--color-border-default, #4a4d5a)';this.style.color='var(--color-fg-default, #e6e7eb)'">
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              <span>Watch</span>
            </a>
          </div>
        </div>
      </div>
      {#- ── End Codeberg repo CTA ─────────────────────────────────────── #}
"""

    # Find the closing </footer> tag in the ralph-page-footer block
    # Insert the CTA block right before </footer>
    footer_close = "</footer>"
    if footer_close not in content:
        result["error"] = "Could not find </footer> in template"
        return result

    new_content = content.replace(footer_close, cta_block + "\n      " + footer_close, 1)
    page_html.write_text(new_content, encoding="utf-8")

    # No CSS file to inject — inline styles used in template above.
    result["applied"] = True
    result["files_modified"] = [str(page_html)]
    return result


def run(
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Main entry point.

    Args:
        dry_run: Audit only, don't make changes
        force: Skip regeneration guard
        now: Override current time
    """
    now = now or datetime.now(timezone.utc)
    report = {
        "generated_at": now.isoformat(),
        "agent": "social_proof_bootstrap",
        "dry_run": dry_run,
        "audit_results": [],
        "actions_taken": [],
        "codeberg_primary": f"https://codeberg.org/{CODEBERG_REPO}",
        "summary": {},
    }

    # ── Regeneration guard ──
    if not force and STATUS_PATH.exists():
        try:
            prev = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            prev_ts = prev.get("generated_at", "")
            if prev_ts:
                prev_dt = datetime.fromisoformat(prev_ts)
                age_hours = (now - prev_dt).total_seconds() / 3600
                if age_hours < 6:
                    report["regeneration_guard"] = "skipped"
                    report["reason"] = f"Previous run {age_hours:.1f}h ago"
                    STATUS_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    return report
        except Exception:
            pass

    # ── AUDIT PHASE ──
    # 1. Codeberg README
    readme_audit = audit_codeberg_readme()
    report["audit_results"].append(readme_audit)

    # 2. Docs footer
    docs_audit = audit_docs_footer()
    report["audit_results"].append(docs_audit)

    # 3. PyPI page
    pypi_audit = audit_pypi_page()
    report["audit_results"].append(pypi_audit)

    # 4-11. Site surfaces
    for surface in AUDIT_SURFACES:
        if "local_path" in surface:
            paths = [surface["local_path"]] if not isinstance(surface["local_path"], list) else surface["local_path"]
            site_audit = audit_site_surface(
                surface["name"],
                surface["url"],
                [Path(p) for p in paths],
            )
            report["audit_results"].append(site_audit)
        elif "local_paths" in surface:
            site_audit = audit_site_surface(
                surface["name"],
                surface["url"],
                [Path(p) for p in surface["local_paths"]],
            )
            report["audit_results"].append(site_audit)

    # ── ACTION PHASE ──
    if not dry_run:
        # Action 1: Docs footer CTA (highest leverage — 49 pages)
        if not docs_audit.get("has_star_cta"):
            footer_result = inject_docs_footer_cta()
            if footer_result.get("applied") and not footer_result.get("already_present"):
                report["actions_taken"].append(footer_result)

                # Commit and push
                git_result = _git_commit_and_push(
                    "trust(docs): add Codeberg star/fork/watch CTA to docs footer",
                    [SITE_REPO / "docs/sphinx_overrides/_themes/ralph-docs/page.html"]
                    + list(SITE_REPO.glob("docs/sphinx_overrides/_themes/ralph-docs/**/*.css")),
                )
                report["actions_taken"].append({"git_result": git_result})
        elif docs_audit.get("already_present"):
            report["actions_taken"].append({
                "action": "docs_footer_cta",
                "status": "already_present",
                "note": "CTA already injected by prior run",
            })

    # ── SUMMARY ──
    total_gaps = sum(len(a.get("gaps", [])) for a in report["audit_results"])
    surfaces_with_gaps = [a["surface"] for a in report["audit_results"] if a.get("gaps")]
    surfaces_reachable = [a["surface"] for a in report["audit_results"] if a.get("reachable", True)]
    actions_executed = len(report["actions_taken"])

    # ── ralph star CLI gate (not a CTA surface, but the highest-ROI conversion asset) ──
    ralph_star_cli_paths = [
        Path("/home/mistlight/Ralph-Workflow/ralph-workflow/ralph/cli/commands/star.py"),
        Path("/home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror/ralph-workflow/ralph/cli/commands/star.py"),
    ]
    ralph_star_deployed = any(p.exists() for p in ralph_star_cli_paths)

    report["summary"] = {
        "total_surfaces_audited": len(report["audit_results"]),
        "surfaces_with_gaps": len(surfaces_with_gaps),
        "total_gaps_found": total_gaps,
        "surfaces_reachable": len(surfaces_reachable),
        "actions_executed": actions_executed,
        "gap_surfaces": surfaces_with_gaps,
        "ralph_star_cli_deployed": ralph_star_deployed,
    }

    STATUS_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Social proof bootstrap agent for RalphWorkflow")
    parser.add_argument("--dry-run", action="store_true", help="Audit only, don't make changes")
    parser.add_argument("--force", action="store_true", help="Skip regeneration guard")
    parser.add_argument("--status", action="store_true", help="Show last run status")
    args = parser.parse_args()

    if args.status:
        if STATUS_PATH.exists():
            prev = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            print(json.dumps(prev, indent=2, ensure_ascii=False))
        else:
            print("No previous run found.")
        sys.exit(0)

    result = run(dry_run=args.dry_run, force=args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
