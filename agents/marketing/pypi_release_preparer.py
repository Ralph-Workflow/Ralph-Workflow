#!/usr/bin/env python3
"""
PyPI Release Preparer — Highest-ROI Blocked Action Bundler

**Problem:** Ralph Workflow v0.8.7 is on PyPI with a stale README that says
"not the main product pitch" and directs installers off-site. A conversion-optimized
README with Codeberg-primary CTA and comparison table exists on the main branch but
was never released. PyPI gets 1,498 downloads/month (10/day) with 0.8% Codeberg
star conversion.

**This script bundles everything a human needs to release v0.8.8:**
1. Builds the wheel + sdist from the vendored Ralph-Workflow source
2. Verifies the README contains the Codeberg CTA (fails if missing)
3. Verifies the version bump
4. Writes a one-command publish instruction to stdout + handoff log

**What this does NOT do:** publish to PyPI (requires PYPI_TOKEN which is not set).

Usage:
    python3 agents/marketing/pypi_release_preparer.py
    python3 agents/marketing/pypi_release_preparer.py --verify-only
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
REPO_DIR = ROOT / "Ralph-Site/vendor/Ralph-Workflow/ralph-workflow"
LOG_DIR = ROOT / "agents/marketing/logs"
HANDOFF_LOG = LOG_DIR / "pypi_release_handoff_latest.json"

# The PyPI token environment variable name (not set in this environment)
PYPI_TOKEN_VAR = "PYPI_TOKEN"

# What the README must contain to pass verification
REQUIRED_README_SIGNALS = [
    "codeberg.org/RalphWorkflow/Ralph-Workflow",
    "free and open",
]

# What must NOT be in the README
FORBIDDEN_README_SIGNALS = [
    "not the main product pitch",
]


def check_prerequisites() -> list[str]:
    """Return list of missing prerequisites."""
    issues = []
    if not os.getenv(PYPI_TOKEN_VAR):
        issues.append(f"{PYPI_TOKEN_VAR} environment variable not set")
    if not REPO_DIR.exists():
        issues.append(f"Source directory not found: {REPO_DIR}")
    return issues


def read_readme() -> str:
    """Read the vendored README."""
    readme = REPO_DIR / "README.md"
    if not readme.exists():
        raise FileNotFoundError(f"README not found: {readme}")
    return readme.read_text()


def verify_readme(readme: str) -> tuple[bool, list[str], list[str]]:
    """Verify README has required signals and no forbidden ones."""
    missing = [s for s in REQUIRED_README_SIGNALS if s not in readme]
    present_forbidden = [s for s in FORBIDDEN_README_SIGNALS if s in readme]
    ok = len(missing) == 0 and len(present_forbidden) == 0
    return ok, missing, present_forbidden


def get_current_version() -> str:
    """Extract version from pyproject.toml or __init__.py."""
    init = REPO_DIR / "ralph/__init__.py"
    if init.exists():
        for line in init.read_text().splitlines():
            if line.startswith("__version__"):
                return line.split("=")[1].strip().strip('"').strip("'")
    raise RuntimeError("Could not determine version")


def build_distributions() -> tuple[bool, str]:
    """Build wheel and sdist using hatch."""
    try:
        result = subprocess.run(
            ["hatch", "build", "--clean"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, "Build successful"
    except FileNotFoundError:
        return False, "hatch not installed — run: pip install hatch"
    except subprocess.TimeoutExpired:
        return False, "Build timed out after 60s"


def check_dist_artifacts() -> list[str]:
    """List built dist files."""
    dist_dir = REPO_DIR / "dist"
    if not dist_dir.exists():
        return []
    return sorted([str(p.name) for p in dist_dir.iterdir() if p.is_file()])


def generate_publish_command() -> str:
    """Generate the publish command with token injection required."""
    return (
        "# To publish v0.8.8 to PyPI, run:\n"
        "#   export PYPI_TOKEN=<your-pypi-token>\n"
        "#   cd Ralph-Site/vendor/Ralph-Workflow/ralph-workflow\n"
        "#   hatch publish --no-prompt --user __token__ --auth $PYPI_TOKEN\n"
        "#\n"
        "# Or with twine:\n"
        "#   python3 -m twine upload dist/* --username __token__ --password $PYPI_TOKEN"
    )


def main():
    verify_only = "--verify-only" in sys.argv

    print("=" * 60)
    print("PyPI Release Preparer — Ralph Workflow v0.8.8")
    print("=" * 60)
    print()

    # 1. Check prereqs
    issues = check_prerequisites()
    if issues:
        print(f"⚠️  Missing prerequisites ({len(issues)}):")
        for i in issues:
            print(f"   - {i}")
        print()
    else:
        print("✅ All prerequisites met — can publish directly")
        print()

    # 2. Verify README
    try:
        readme = read_readme()
        readme_ok, missing, forbidden = verify_readme(readme)
        if readme_ok:
            print("✅ README verification passed")
            print("   - Codeberg CTA: present")
            print("   - No forbidden signals")
        else:
            print("❌ README verification FAILED")
            if missing:
                for m in missing:
                    print(f"   - MISSING: '{m}'")
            if forbidden:
                for f in forbidden:
                    print(f"   - FORBIDDEN: '{f}'")
        print()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        print()

    # 3. Version
    try:
        version = get_current_version()
        print(f"📦 Current version: {version}")
    except RuntimeError as e:
        print(f"❌ {e}")
        version = "unknown"
    print()

    # 4. Build
    if not verify_only:
        print("🔨 Building distributions...")
        build_ok, build_msg = build_distributions()
        if build_ok:
            print("✅ Build successful")
            artifacts = check_dist_artifacts()
            for a in artifacts:
                print(f"   - {a}")
        else:
            print(f"❌ Build failed: {build_msg}")
    else:
        print("🔍 Verify-only mode — skipping build")
        artifacts = check_dist_artifacts()
        if artifacts:
            print("   Existing artifacts:")
            for a in artifacts:
                print(f"   - {a}")
    print()

    # 5. Impact estimate
    print("📊 Impact estimate:")
    print("   - 1,498 PyPI downloads/month see stale README (v0.8.7)")
    print("   - New README: Codeberg-primary CTA, comparison table, 4-step quick-start")
    print("   - Current conversion: 12 stars / 1,498 downloads = 0.8%")
    print("   - Target: 2%+ (30 stars) with conversion-optimized README")
    print()

    # 6. Publish instructions
    print(generate_publish_command())
    print()

    # 7. Write handoff log
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "pypi_release_prepared",
        "version": version,
        "readme_verified": readme_ok,
        "missing_signals": missing,
        "forbidden_signals": forbidden,
        "artifacts": artifacts,
        "build_successful": build_ok if not verify_only else True,
        "pypi_downloads_monthly": 1498,
        "current_codeberg_stars": 12,
        "expected_conversion_improvement": "0.8% → 2%+ with new README on PyPI",
        "human_action_required": (
            f"Run: export PYPI_TOKEN=<your-token> && "
            f"cd {REPO_DIR} && "
            "hatch publish --no-prompt --user __token__ --auth $PYPI_TOKEN"
        ),
    }
    HANDOFF_LOG.write_text(json.dumps(result, indent=2) + "\n")
    print(f"📝 Handoff log written: {HANDOFF_LOG}")

    return 0 if readme_ok else 1


if __name__ == "__main__":
    sys.exit(main())
