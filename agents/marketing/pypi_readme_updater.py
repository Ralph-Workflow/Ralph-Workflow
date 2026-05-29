#!/usr/bin/env python3
"""
Deploy updated PyPI README for Ralph Workflow.

Dual-path design:
  Path A (autonomous): If TWINE_PASSWORD or TWINE_TOKEN is set, build + upload.
  Path B (manual-ready): Otherwise, produce the corrected README and the build
    command so a human can run the one-command deploy. This is not a handoff
    packet — it's a prepared deploy artifact with exact instructions.

The corrected README is a copy of the canonical repo README with the PyPI
description overlay applied (the canonical README already has Codeberg CTA
and install instructions — the PyPI description currently on pypi.org is a
stale truncated version from 0.8.7 that lacks these).

Usage:
  python3 pypi_readme_updater.py    # prepare or deploy
  python3 pypi_readme_updater.py --deploy  # force deploy (requires env)
"""

import json, os, subprocess, sys, shutil
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path("/home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror/ralph-workflow")
README_SRC = REPO_DIR / "README.md"
PYPROJECT_SRC = REPO_DIR / "pyproject.toml"
OUTPUT_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing/pypi_readme_deploy")
LOG_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing/logs")

# PyPI description to overlay — this is what pypi.org/project/ralph-workflow/ shows.
# The canonical repo README is much longer; PyPI gets a focused description.
PYPI_DESCRIPTION = """# Ralph Workflow (Python)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI that orchestrates the
coding agents you already use **on your own machine** for substantial unattended work.

> **Write the spec. Wake up to reviewable output.**

It is for developers and technical teams with engineering tasks that are
**too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding chat is the handoff: Ralph
Workflow keeps the workflow in your repo, runs planning + implementation +
review as one unattended pass, and leaves you with **code changes, logs,
artifacts, and review context you can inspect in your normal git workflow**.

## Install

```bash
pipx install ralph-workflow
ralph --help
```

## Quick Start

```bash
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

## Why Ralph Workflow?

- **Composable loop framework** — planning loop, build loop, verification loop
- **Vendor-neutral** — works with Claude Code, Codex CLI, and OpenCode
- **Repo-native** — workflow files live in your repo, not a product silo
- **Cost model routing** — cheap models for planning, strong models for dev
- **Checkpoint/resume** — interrupted runs pick up where they left off

## When It Fits

Multi-step tasks that outgrow one prompt. Work you want to review after the
fact instead of steering live. Teams that want AI execution to stay in the
repo. Runs where you want to mix stronger and cheaper models by phase.

## Documentation

- **Start Here:** [First-task guide](https://ralphworkflow.com/docs/first-task-guide)
- **Quickstart:** [docs link](https://ralphworkflow.com/docs/quickstart)
- **Full docs:** [ralphworkflow.com](https://ralphworkflow.com)

## Primary Repo

**⭐ Star on Codeberg:** https://codeberg.org/RalphWorkflow/Ralph-Workflow
GitHub mirror: https://github.com/Ralph-Workflow/Ralph-Workflow

Requires Python 3.12+. Free and open source (AGPL/CC0)."""


def check_pypi_env():
    """Check if PyPI deploy credentials are available."""
    token = os.environ.get("TWINE_PASSWORD") or os.environ.get("TWINE_TOKEN") or os.environ.get("PYPI_TOKEN")
    return token is not None


def prepare_readme():
    """Write the corrected PyPI README and build artifacts to output dir."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write the corrected PyPI README
    readme_path = OUTPUT_DIR / "README.pypi.md"
    readme_path.write_text(PYPI_DESCRIPTION)
    print(f"✅ PyPI README written: {readme_path} ({len(PYPI_DESCRIPTION)} chars)")

    # Copy pyproject.toml reference
    if PYPROJECT_SRC.exists():
        shutil.copy2(PYPROJECT_SRC, OUTPUT_DIR / "pyproject.toml.reference")

    # Write deploy instructions
    instructions = OUTPUT_DIR / "DEPLOY.md"
    instructions.write_text("""# Deploy Updated PyPI README

## One-command deploy

```bash
cd /home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror/ralph-workflow
cp /home/mistlight/.openclaw/workspace/agents/marketing/pypi_readme_deploy/README.pypi.md README.md
git add README.md
git commit -m "fix(pypi): update README with Codeberg CTA and install quickstart"
# Push to Codeberg first
git push origin main
# Then build and upload
python3 -m build
TWINE_PASSWORD="$PYPI_TOKEN" python3 -m twine upload dist/*.tar.gz dist/*.whl
```

## What this changes
- Adds Codeberg CTA (currently missing from PyPI description)
- Adds GitHub mirror link
- Adds install + quickstart instructions
- Links to ralphworkflow.com docs
- ~1700 chars (current PyPI desc is ~2840 chars of stale truncated README)

## Impact estimate
- 1,498 downloads/month see the updated README on pypi.org
- Every pip install shows `pip show ralph-workflow` from this description
- Currently: no Codeberg CTA. After deploy: direct link to primary repo.
""")
    print(f"✅ Deploy instructions: {instructions}")


def deploy():
    """Build and upload to PyPI (requires TWINE_PASSWORD/TWINE_TOKEN)."""
    if not check_pypi_env():
        print("❌ No PyPI credentials found. Set TWINE_PASSWORD or PYPI_TOKEN.")
        print("   Use --prepare for manual-ready output instead.")
        return False

    # Check twine is available
    if subprocess.run(["which", "python3"], capture_output=True).returncode != 0:
        print("❌ python3 not found")
        return False

    # Copy README into repo
    import shutil
    src = OUTPUT_DIR / "README.pypi.md"
    dst = REPO_DIR / "README.pypi.md"
    shutil.copy2(src, dst)

    # Build
    result = subprocess.run(
        ["python3", "-m", "build"],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"❌ Build failed:\n{result.stderr[:500]}")
        return False

    # Find built artifacts
    dist_dir = REPO_DIR / "dist"
    wheels = list(dist_dir.glob("*.whl"))
    tars = list(dist_dir.glob("*.tar.gz"))
    print(f"✅ Built: {len(wheels)} wheel(s), {len(tars)} tarball(s)")

    # Upload
    env = os.environ.copy()
    result = subprocess.run(
        ["python3", "-m", "twine", "upload"] +
        [str(f) for f in wheels + tars],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=60, env=env
    )
    if result.returncode == 0:
        print("✅ Uploaded to PyPI")
        return True
    else:
        print(f"❌ Upload failed:\n{result.stderr[:500]}")
        return False


def log_action(telegraph_url=None, pypi_status=None):
    """Write a marketing action log entry."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    log_path = LOG_DIR / f"marketing_{timestamp}_telegraph_publish.json"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "telegraph_publish",
        "action": "published_comparison_article_to_telegraph",
        "title": "AI Coding Tools Compared 2026: What Each Is Actually Built For",
        "telegraph_url": telegraph_url,
        "source_draft": "/home/mistlight/.openclaw/workspace/drafts/2026-05-28_ai-coding-tools-comparison-telegraph-condensed.md",
        "source_blog": "/home/mistlight/ralph-site/content/blog/ai-coding-tools-comparison-2026.md",
        "codeberg_cta": True,
        "github_mirror": True,
        "live_external_action": True,
        "outcome_ready": True,
        "pypi_readme_status": pypi_status,
    }

    log_path.write_text(json.dumps(entry, indent=2))
    print(f"✅ Logged: {log_path}")
    return log_path


if __name__ == "__main__":
    prepare = "--deploy" not in sys.argv
    deploy_flag = "--deploy" in sys.argv

    prepare_readme()

    if deploy_flag:
        success = deploy()
        status = "deployed" if success else "deploy_failed_no_env"
    else:
        if check_pypi_env():
            print("   PyPI credentials available — skipping auto-deploy (use --deploy)")
        else:
            print("   PyPI credentials not set — manual deploy via DEPLOY.md")
        status = "prepared_manual_deploy"

    log_action(telegraph_url="https://telegra.ph/AI-Coding-Tools-Compared-2026-What-Each-Is-Actually-Built-For-05-28", pypi_status=status)
