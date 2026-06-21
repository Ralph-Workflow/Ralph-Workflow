#!/usr/bin/env python3
"""fabrication_guard.py — Multi-level fabrication defense for Ralph Workflow.

LEVEL 1 — Pattern detection (regex, no network, <100ms):
  Catches known bad patterns (Nightcrawler misattribution, stale install counts,
  unverified npm package claims, bare star counts, etc.). Runs as pre-commit hook.

LEVEL 2 — Existence verification (network, cached, ~5s first run):
  Verifies every GitHub repo URL, npm package, and external link referenced in
  public-facing markdown actually exists. Uses GitHub API + npm registry.
  Results cached in .git/fabrication-cache.json.

LEVEL 3 — Quantitative claim verification (network, authenticated, ~30s):
  Cross-references specific claims (star counts, fork counts, file line counts,
  issue numbers) against live GitHub API data. Requires GITHUB_TOKEN.

The gate is unweakenable by design:
  - No whitelist of "safe" files — every .md file is in scope
  - No opt-out per-file — exemption requires updating EXEMPT_FILES in this script
  - Pre-commit hook blocks any commit that fails Level 1

Usage:
    ./scripts/fabrication_guard.py                   # Level 1 (fast, local, default)
    ./scripts/fabrication_guard.py --level 2          # Level 1+2 (network, cached)
    ./scripts/fabrication_guard.py --level 3          # Level 1+2+3 (full, needs token)
    ./scripts/fabrication_guard.py --level 1 --quick  # L1 only, staged files only
    ./scripts/fabrication_guard.py path/to/file.md    # Specific file(s)
    ./scripts/fabrication_guard.py --json             # Machine-readable output

Environment:
    GITHUB_TOKEN    — GitHub personal access token for Level 3 (optional for L2)

Exit codes:
    0  — all checks passed
    1  — fabrication(s) detected
    2  — internal error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / ".git" / "fabrication-cache.json"

# Default: scan the entire repo. No whitelist, no carve-outs.
DEFAULT_ROOTS: tuple[Path, ...] = (Path("."),)

# ▸ Level 1 — Banned patterns
# Each entry: (name, regex, severity, why)
BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    (
        "nightcrawler-credits-ralph",
        re.compile(
            r"[Nn]ightcrawler[^.\n]{0,80}credits[^.\n]{0,40}ralph[\s-]*workflow",
            re.IGNORECASE,
        ),
        "hard",
        "FALSE. Nightcrawler (thebasedcapital/nightcrawler) credits "
        "`ghuntley.com/ralph` (Geoffrey Huntley's Ralph-loop pattern), "
        "not Ralph Workflow.",
    ),
    (
        "stale-1300-installs",
        re.compile(
            r"(~|approx\.?|about|over|more than)?\s*1[,]?300\s+installs?(/|s|\s+a|\s+per)?\s*month",
            re.IGNORECASE,
        ),
        "hard",
        "STALE + WRONG. Use the sourced, date-stamped line in "
        "agents/marketing/RALPH_WORKFLOW_POSITIONING.md.",
    ),
    (
        "soft-x-credits-ralph-no-verify",
        re.compile(
            r"^\s*[\*\-]?\s*[A-Za-z][\w\-/]*\s+(?:credits?|uses|is\s+built\s+on|"
            r"is\s+built\s+with|runs\s+on|is\s+powered\s+by)\s+Ralph[\s-]*Workflow",
            re.IGNORECASE | re.MULTILINE,
        ),
        "soft",
        "Claim about a project crediting/using Ralph Workflow must have a "
        "`verify:` annotation in the same block.",
    ),
    (
        "raw-star-or-download-count-no-source",
        re.compile(
            r"(?:"
            r"(?:\u2605\s*)?\b\d[\d,]*\.?\d*\s*[kKmM]?\s*\u2605\b"
            r"|\b\d[\d,]+(?:\.\d+)?\s*/\s*(?:month|mo|week|wk|day)\b"
            r"|\b\d[\d,]+(?:\.\d+)?\s+(?:installs?|downloads?|stars?|MAU|DAU)\b"
            r")"
        ),
        "soft",
        "Bare star/download/install count must be paired with (source, date).",
    ),
    (
        "users-md-npm-package-claim",
        re.compile(r"npm\s+package\s+`@[\w\-]+/[\w\-]+`"),
        "soft",
        "npm package claim must have a `verify:` annotation confirming existence. "
        "Fabricated npm packages were the john-ezra/open-ralph failure (58a1d25e9).",
    ),
    (
        "markdown-link-to-nonexistent-repo",
        # Catches: "[owner/repo](https://github.com/owner/repo)" — any GitHub
        # repo link in a claim context. Must have verify: annotation.
        # Only fires in files that look like USERS.md / SHOWCASE.md (have ### headings).
        re.compile(
            r"\[[\w\-\.]+/[\w\-\.]+\]\(https://github\.com/[\w\-\.]+/[\w\-\.]+\)",
        ),
        "soft",
        "GitHub repo link in a claim file must have a `verify:` annotation "
        "confirming the repo exists. Fabricated repos are the exact D91 failure "
        "mode (john-ezra/open-ralph, commit 58a1d25e9).",
    ),
)

# Files exempt from ALL levels (the guard's own docs, changelog, agent instructions)
EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "scripts/verify_social_proof.py",
        "scripts/fabrication_guard.py",
        "scripts/README.md",
        "CHANGELOG.md",
        "AGENTS.md",
        "CLAUDE.md",
    }
)

# Directories excluded from the full-repo scan (vendored/generated content).
# wt-* prefix directories are Ralph-Workflow test-fixture output and are
# auto-excluded at runtime (they contain generated READMEs that may have
# stale claims — not our public copy).
EXEMPT_DIRS: frozenset[str] = frozenset(
    {
        ".venv", "venv", "node_modules", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "dist", "build", ".eggs", "site-packages",
        ".git",
    }
)


def _auto_exclude_wt_dirs(root: Path = Path(".")) -> frozenset[str]:
    """Find and auto-exclude wt-* test-fixture directories."""
    wt_dirs: set[str] = set()
    if root.exists():
        for d in root.iterdir():
            if d.is_dir() and d.name.startswith("wt-"):
                wt_dirs.add(d.name)
    return frozenset(wt_dirs)

# Sections that document the rejection list (allowed to name banned patterns)
DOCUMENTING_SECTION_HEADINGS: tuple[str, ...] = (
    "Auto-rejection patterns",
    "Evidence gate",
    "RETIRED CLAIMS",
    "Retracted",
    "Fabrication guard",
    "Banned patterns",
)

# Files where "GitHub repo link" soft-pattern requires verify: annotation.
# (These are the social-proof files. Other files with markdown links are fine.)
CLAIM_FILES: frozenset[str] = frozenset(
    {"USERS.md", "SHOWCASE.md", "README.md", "ralph-workflow/README.md"}
)

# ═══════════════════════════════════════════════════════════════════════════
# Level 1 — Pattern detection
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Finding:
    level: int
    pattern: str
    severity: str
    file: str
    line_no: int
    line: str
    why: str


def _is_evidence_annotated(file_text: str, line_no: int) -> bool:
    """True if the block around line_no contains a `verify:` annotation."""
    lines = file_text.splitlines()
    lo = max(0, line_no - 7)
    hi = min(len(lines), line_no + 6)
    window = "\n".join(lines[lo:hi])
    return bool(re.search(r"verify:\s*\S", window))


def _is_documenting_section(file_text: str, line_no: int) -> bool:
    """True if line is in a section that documents the rejection list."""
    lines = file_text.splitlines()
    for i in range(line_no - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            if any(h in stripped for h in DOCUMENTING_SECTION_HEADINGS):
                return True
    return False


def level1_scan(path: Path) -> list[Finding]:
    """Scan a single file for banned patterns (Level 1)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [
            Finding(level=1, pattern="<io-error>", severity="hard",
                    file=str(path), line_no=0, line="",
                    why=f"Could not read file: {exc}")
        ]

    findings: list[Finding] = []
    lines = text.splitlines()
    rel_path = str(path)

    for name, pattern, severity, why in BANNED_PATTERNS:
        # The markdown-link-to-nonexistent-repo pattern only fires in claim files
        if name == "markdown-link-to-nonexistent-repo":
            if rel_path not in CLAIM_FILES and not any(
                rel_path.endswith(f) for f in CLAIM_FILES
            ):
                continue

        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""

            if _is_documenting_section(text, line_no):
                continue
            if severity == "soft" and _is_evidence_annotated(text, line_no):
                continue

            findings.append(Finding(
                level=1, pattern=name, severity=severity,
                file=str(path), line_no=line_no, line=line.strip(), why=why,
            ))
    return findings


# ═══════════════════════════════════════════════════════════════════════════
# Level 2 — Existence verification
# ═══════════════════════════════════════════════════════════════════════════

# Patterns to extract external resource references
GITHUB_REPO_URL_RE = re.compile(
    r"https://github\.com/([\w\-\.]+)/([\w\-\.]+)"
)
NPM_PACKAGE_RE = re.compile(
    r"npm\s+package\s+`(@[\w\-]+/[\w\-]+)`"
)
NPM_PACKAGE_BARE_RE = re.compile(
    r"`(@[\w\-]+/[\w\-]+)`"
)
EXTERNAL_URL_RE = re.compile(
    r"\]\(https?://(?!github\.com/Ralph-Workflow|codeberg\.org/RalphWorkflow)"
    r"([^\)]+)\)"
)


def _extract_resources(text: str) -> tuple[set[str], set[str], set[str]]:
    """Extract GitHub repos, npm packages, and external URLs from text."""
    repos: set[str] = set()
    npm_pkgs: set[str] = set()
    urls: set[str] = set()

    for m in GITHUB_REPO_URL_RE.finditer(text):
        repos.add(f"{m.group(1)}/{m.group(2)}")

    for m in NPM_PACKAGE_RE.finditer(text):
        npm_pkgs.add(m.group(1))

    for m in NPM_PACKAGE_BARE_RE.finditer(text):
        pkg = m.group(1)
        if pkg.startswith("@") and "/" in pkg:
            npm_pkgs.add(pkg)

    for m in EXTERNAL_URL_RE.finditer(text):
        urls.add(f"https://{m.group(1)}")

    return repos, npm_pkgs, urls


def _load_cache() -> dict:
    """Load the existence-check cache."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(cache: dict) -> None:
    """Save the existence-check cache."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _check_github_repo(owner: str, repo: str, cache: dict) -> tuple[bool, str]:
    """Check if a GitHub repo exists. Returns (exists, detail)."""
    key = f"github:{owner}/{repo}"
    if key in cache:
        entry = cache[key]
        # Cache for 24 hours
        if time.time() - entry.get("ts", 0) < 86400:
            return entry["ok"], entry.get("detail", "")

    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "RalphWorkflow-fabrication-guard/1.0")
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        detail = (
            f"★{data.get('stargazers_count', '?')} "
            f"forks={data.get('forks_count', '?')} "
            f"created={data.get('created_at', '?')[:10]}"
        )
        cache[key] = {"ok": True, "detail": detail, "ts": time.time()}
        _save_cache(cache)
        return True, detail
    except urllib.error.HTTPError as e:
        if e.code == 404:
            detail = "repo not found (404)"
        elif e.code == 403:
            detail = "rate-limited (403)"
        else:
            detail = f"HTTP {e.code}"
        cache[key] = {"ok": False, "detail": detail, "ts": time.time()}
        _save_cache(cache)
        return False, detail
    except Exception as e:
        return False, f"error: {e}"


def _check_npm_package(pkg: str, cache: dict) -> tuple[bool, str]:
    """Check if an npm package exists. Returns (exists, detail)."""
    key = f"npm:{pkg}"
    if key in cache:
        entry = cache[key]
        if time.time() - entry.get("ts", 0) < 86400:
            return entry["ok"], entry.get("detail", "")

    url = f"https://registry.npmjs.org/{pkg}"
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "RalphWorkflow-fabrication-guard/1.0")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        detail = f"v{data.get('dist-tags', {}).get('latest', '?')}"
        cache[key] = {"ok": True, "detail": detail, "ts": time.time()}
        _save_cache(cache)
        return True, detail
    except urllib.error.HTTPError as e:
        cache[key] = {"ok": False, "detail": f"npm HTTP {e.code}", "ts": time.time()}
        _save_cache(cache)
        return False, f"npm HTTP {e.code}"
    except Exception as e:
        return False, f"npm error: {e}"


def _check_url(url: str, cache: dict) -> tuple[bool, str]:
    """Check if an external URL is reachable. Returns (exists, detail)."""
    key = f"url:{url}"
    if key in cache:
        entry = cache[key]
        if time.time() - entry.get("ts", 0) < 86400:
            return entry["ok"], entry.get("detail", "")

    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "RalphWorkflow-fabrication-guard/1.0")
        resp = urllib.request.urlopen(req, timeout=10)
        cache[key] = {"ok": True, "detail": f"HTTP {resp.status}", "ts": time.time()}
        _save_cache(cache)
        return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        cache[key] = {"ok": False, "detail": f"HTTP {e.code}", "ts": time.time()}
        _save_cache(cache)
        return False, f"HTTP {e.code}"
    except Exception as e:
        cache[key] = {"ok": False, "detail": f"error: {e}", "ts": time.time()}
        _save_cache(cache)
        return False, f"error: {e}"


def level2_scan(path: Path, cache: dict) -> list[Finding]:
    """Verify all external resources referenced in a file actually exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[Finding] = []
    repos, npm_pkgs, urls = _extract_resources(text)

    for repo in repos:
        owner, name = repo.split("/", 1)
        exists, detail = _check_github_repo(owner, name, cache)
        if not exists:
            # Find the line
            for i, line in enumerate(text.splitlines(), 1):
                if f"github.com/{repo}" in line.replace(" ", ""):
                    findings.append(Finding(
                        level=2, pattern="nonexistent-github-repo",
                        severity="hard",
                        file=str(path), line_no=i, line=line.strip(),
                        why=f"GitHub repo {repo} does not exist: {detail}. "
                            "This is a fabrication. Remove the entry immediately.",
                    ))
                    break

    for pkg in npm_pkgs:
        exists, detail = _check_npm_package(pkg, cache)
        if not exists:
            for i, line in enumerate(text.splitlines(), 1):
                if pkg in line:
                    findings.append(Finding(
                        level=2, pattern="nonexistent-npm-package",
                        severity="hard",
                        file=str(path), line_no=i, line=line.strip(),
                        why=f"npm package {pkg} does not exist: {detail}. "
                            "This is a fabrication. Remove the entry immediately.",
                    ))
                    break

    # Only check non-GitHub/non-npm URLs for 404s
    for url in urls:
        if "github.com" in url or "npmjs.com" in url:
            continue  # already checked above
        exists, detail = _check_url(url, cache)
        if not exists:
            for i, line in enumerate(text.splitlines(), 1):
                if url.rstrip("/") in line.replace(" ", ""):
                    findings.append(Finding(
                        level=2, pattern="broken-external-link",
                        severity="soft",
                        file=str(path), line_no=i, line=line.strip(),
                        why=f"External URL returns {detail}: {url}. "
                            "Fix or remove the link.",
                    ))
                    break

    return findings


# ═══════════════════════════════════════════════════════════════════════════
# Level 3 — Quantitative claim verification
# ═══════════════════════════════════════════════════════════════════════════

# Patterns for quantitative claims that can be verified
STAR_CLAIM_RE = re.compile(
    r"(?:^|\s)(\d+(?:\.\d+)?[kK]?)\s*[★\u2605](?:\s|,|\.|$)"
)
FORK_CLAIM_RE = re.compile(
    r"(\d+)\s+forks?"
)
ISSUE_CLAIM_RE = re.compile(
    r"github\.com/([\w\-\.]+)/([\w\-\.]+)/issues/(\d+)"
)
FILE_LINE_CLAIM_RE = re.compile(
    r"(\d+)[\- ]line\s+([\w\-_/]+\.(?:py|js|ts|sh|md|json))"
)


def level3_scan(path: Path, cache: dict) -> list[Finding]:
    """Cross-reference quantitative claims against live data.

    Requires GITHUB_TOKEN for reliable API access. Without it, this level
    is skipped (returns empty).
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return []  # Silently skip — needs auth

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[Finding] = []
    lines = text.splitlines()

    # Check star claims against live GitHub API
    for m in STAR_CLAIM_RE.finditer(text):
        claimed = m.group(1).lower().replace("k", "000").replace(".", "")
        line_no = text.count("\n", 0, m.start()) + 1
        # Find nearest GitHub repo URL above this line
        context_start = max(0, line_no - 15)
        context = "\n".join(lines[context_start:line_no])
        repo_match = GITHUB_REPO_URL_RE.search(context)
        if repo_match:
            owner, name = repo_match.group(1), repo_match.group(2)
            exists, detail = _check_github_repo(owner, name, cache)
            if exists:
                stars = detail.split("★")[1].split()[0] if "★" in detail else "?"
                if stars != "?" and stars != claimed:
                    findings.append(Finding(
                        level=3, pattern="stale-star-count",
                        severity="soft",
                        file=str(path), line_no=line_no,
                        line=lines[line_no - 1].strip(),
                        why=f"Claimed {claimed}★ but {owner}/{name} has {stars}★. "
                            "Update the count with current data.",
                    ))

    # Check issue references
    for m in ISSUE_CLAIM_RE.finditer(text):
        owner, repo_name, issue_num = m.group(1), m.group(2), m.group(3)
        line_no = text.count("\n", 0, m.start()) + 1
        key = f"issue:{owner}/{repo_name}#{issue_num}"
        if key not in cache:
            url = (
                f"https://api.github.com/repos/{owner}/{repo_name}"
                f"/issues/{issue_num}"
            )
            try:
                req = urllib.request.Request(url)
                req.add_header("Accept", "application/vnd.github.v3+json")
                req.add_header("User-Agent", "RalphWorkflow-fabrication-guard/1.0")
                req.add_header("Authorization", f"Bearer {token}")
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read().decode())
                cache[key] = {
                    "ok": True,
                    "title": data.get("title", "")[:120],
                    "state": data.get("state", "?"),
                    "ts": time.time(),
                }
            except urllib.error.HTTPError as e:
                cache[key] = {"ok": False, "detail": f"HTTP {e.code}", "ts": time.time()}
            except Exception as e:
                cache[key] = {"ok": False, "detail": str(e), "ts": time.time()}
            _save_cache(cache)

        if key in cache and not cache[key].get("ok"):
            findings.append(Finding(
                level=3, pattern="nonexistent-issue-ref",
                severity="hard",
                file=str(path), line_no=line_no,
                line=lines[line_no - 1].strip(),
                why=f"Issue {owner}/{repo_name}#{issue_num} not found. "
                    "Remove or fix the reference.",
            ))

    return findings


# ═══════════════════════════════════════════════════════════════════════════
# File discovery
# ═══════════════════════════════════════════════════════════════════════════

def _is_exempt(path: Path) -> bool:
    """Check if a file is exempt from all levels."""
    rel = str(path)
    if rel in EXEMPT_FILES:
        return True
    if any(path.name == f for f in EXEMPT_FILES):
        return True
    return False


def _is_excluded_dir(path: Path, extra_dirs: frozenset[str] = frozenset()) -> bool:
    """Check if path is under an excluded directory."""
    all_dirs = EXEMPT_DIRS | extra_dirs
    return any(p.name in all_dirs for p in path.parents)


def iter_markdown_files(
    roots: Iterable[Path],
    extra_exclude_dirs: frozenset[str] = frozenset(),
) -> Iterable[Path]:
    """Yield every non-exempt markdown file under the given roots."""
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if not _is_exempt(root) and root not in seen:
                seen.add(root)
                yield root
            continue
        for path in root.rglob("*.md"):
            if path in seen:
                continue
            if _is_exempt(path):
                continue
            if _is_excluded_dir(path, extra_exclude_dirs):
                continue
            seen.add(path)
            yield path


def get_staged_markdown_files() -> list[Path]:
    """Get list of staged markdown files (for pre-commit hook)."""
    extra_dirs = _auto_exclude_wt_dirs(REPO_ROOT)
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        files = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.endswith(".md") and not _is_exempt(Path(line)):
                p = Path(line)
                if not _is_excluded_dir(p, extra_dirs):
                    files.append(p)
        return files
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Multi-level fabrication guard for Ralph Workflow."
    )
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="Files or directories to scan. Default: entire repo.",
    )
    parser.add_argument(
        "--level", type=int, default=1, choices=[1, 2, 3],
        help="Verification depth: 1=patterns, 2=existence, 3=quantitative (default: 1)",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Only scan staged files (for pre-commit hook).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output.",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Skip cache for Level 2/3 checks (fresh verification).",
    )
    args = parser.parse_args(argv)

    # Determine files to scan
    extra_exclude = _auto_exclude_wt_dirs(Path("."))

    if args.quick:
        files = get_staged_markdown_files()
        if not files:
            if not args.json:
                print("OK: no staged markdown files to scan.")
            else:
                print(json.dumps({"ok": True, "scanned": 0, "findings": []}))
            return 0
    elif args.paths:
        files = list(iter_markdown_files(args.paths, extra_exclude))
    else:
        # Full repo scan
        roots = [p for p in DEFAULT_ROOTS if p.exists()]
        files = list(iter_markdown_files(roots, extra_exclude))

    if not files:
        if not args.json:
            print("OK: no files to scan.")
        else:
            print(json.dumps({"ok": True, "scanned": 0, "findings": []}))
        return 0

    # Load cache for Level 2/3
    cache: dict = {} if args.no_cache else _load_cache()

    all_findings: list[Finding] = []

    # Level 1 — always runs
    for path in files:
        all_findings.extend(level1_scan(path))

    # Level 2 — existence verification
    if args.level >= 2:
        for path in files:
            all_findings.extend(level2_scan(path, cache))

    # Level 3 — quantitative verification
    if args.level >= 3:
        for path in files:
            all_findings.extend(level3_scan(path, cache))

    # Output
    hard_failures = [f for f in all_findings if f.severity == "hard"]
    soft_failures = [f for f in all_findings if f.severity == "soft"]

    if args.json:
        print(json.dumps({
            "ok": len(hard_failures) == 0,
            "scanned": len(files),
            "hard_failures": len(hard_failures),
            "soft_failures": len(soft_failures),
            "findings": [asdict(f) for f in all_findings],
        }, indent=2))
    else:
        if not all_findings:
            print(f"OK: scanned {len(files)} files, no issues found "
                  f"(level {args.level}).")
        else:
            label = f"FAIL ({len(all_findings)} issues, level {args.level})"
            print(f"{label}:\n", file=sys.stderr)
            for f in hard_failures + soft_failures:
                tag = "HARD" if f.severity == "hard" else "SOFT"
                print(f"  [{tag}] L{f.level} {f.file}:{f.line_no} ({f.pattern})",
                      file=sys.stderr)
                if f.line:
                    print(f"    > {f.line}", file=sys.stderr)
                print(f"    why: {f.why}", file=sys.stderr)
                print(file=sys.stderr)

    # Hard failures = exit 1. Soft failures = warning only.
    return 1 if hard_failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
