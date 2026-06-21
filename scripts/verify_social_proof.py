#!/usr/bin/env python3
"""verify_social_proof.py — Ralph Workflow social-proof verifier.

This is the gate that stands between the project and a repeat of the 2026-06-11
SHOWCASE.md / README.md failure, where unsubstantiated "Built with Ralph" claims
and a stale "~1,300 installs/month" stat were published as social proof.

The script is intentionally simple: it scans ALL markdown files in the
repository for banned patterns. If a pattern matches, the script exits
non-zero and prints the offending file/line. Run it before any commit.
There is no scope carve-out. Every public-facing markdown file is in scope.
Fabrication is never acceptable anywhere.

If a legitimate claim is being blocked by a false-positive pattern, **update
the script**. Do not delete it. Do not weaken the gate. The gate is the only
thing standing between this project and a repeat of the failure.

Usage:
    ./scripts/verify_social_proof.py            # scan default paths
    ./scripts/verify_social_proof.py path/...   # scan given paths
    ./scripts/verify_social_proof.py --json     # machine-readable output

Exit codes:
    0  — no banned patterns found
    1  — banned pattern(s) found
    2  — internal error (could not read a file, bad args, etc.)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default: scan the entire repo. No whitelist, no carve-outs, no files that
# are "out of scope" for truth. Every public-facing markdown file is in scope.
# Fabrication is never acceptable anywhere — that's why there is no whitelist.
#
# The list of built-in exempt paths (scripts/, CHANGELOG.md, etc.) is in
# EXEMPT_FILES below — those are the ONLY files excluded, and only because
# they document the gate itself or are internal tooling.
DEFAULT_ROOTS: tuple[Path, ...] = (
    Path("."),
)

# Banned patterns. Each entry is (name, regex, severity, why).
#
# Severity is "hard" (always fails) or "soft" (fails unless a `verify:` line
# exists in the same logical block). Hard patterns are absolute fabrications.
# Soft patterns are claims that may be true but need an attached evidence line.
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
        "not Ralph Workflow. This claim has been published and is wrong.",
    ),
    (
        "stale-1300-installs",
        re.compile(
            r"(~|approx\.?|about|over|more than)?\s*1[,]?300\s+installs?(/|s|\s+a|\s+per)?\s*month",
            re.IGNORECASE,
        ),
        "hard",
        "STALE + WRONG. pepy.tech 2026-06-12 reports 4,047 in the last 30 "
        "days; pypistats.org reports 1,128. Use the sourced, date-stamped "
        "line in agents/marketing/RALPH_WORKFLOW_POSITIONING.md.",
    ),
    (
        "soft-x-credits-ralph-no-verify",
        # Catches: "<Project> credits Ralph Workflow", "X is built with Ralph",
        # "X uses Ralph", etc. as a social-proof-style claim in a hero or
        # social-proof line. NOT a hard fail if the same file has a
        # `verify:` annotation on the same line/row.
        re.compile(
            r"^\s*[\*\-]?\s*[A-Za-z][\w\-/]*\s+(?:credits?|uses|is\s+built\s+on|is\s+built\s+with|runs\s+on|is\s+powered\s+by)\s+Ralph[\s-]*Workflow",
            re.IGNORECASE | re.MULTILINE,
        ),
        "soft",
        "Claim about a project crediting/using Ralph Workflow must have a "
        "`verify: <evidence>` annotation in the same row. See SHOWCASE.md "
        "§ Evidence gate for the only accepted forms of evidence.",
    ),
    (
        "raw-star-or-download-count-no-source",
        # Catches bare counts like "9.3K★", "1,300 installs", "★12" etc.
        # that are not paired with a (source, date) annotation.
        #
        # Tight on purpose. We only fire when a count is *clearly* an
        # install/star/download/user number in a marketing context.
        re.compile(
            r"(?:"
            r"(?:\u2605\s*)?\b\d[\d,]*\.?\d*\s*[kKmM]?\s*\u2605\b"
            r"|\b\d[\d,]+(?:\.\d+)?\s*/\s*(?:month|mo|week|wk|day)\b"
            r"|\b\d[\d,]+(?:\.\d+)?\s+(?:installs?|downloads?|stars?|MAU|DAU)\b"
            r")"
        ),
        "soft",
        "Installer / star / download / user count must be paired with a "
        "(source, date) annotation, or be a shields.io badge image, or be "
        "inside a `verify:` block. Bare numbers rot into lies.",
    ),
    (
        "users-md-npm-package-claim",
        # Catches: "npm package `@org/pkg`" — npm claims in public-facing
        # markdown about external integrations. Fabricated npm packages were
        # the D91 failure mode that produced john-ezra/open-ralph (58a1d25e9).
        re.compile(
            r"npm\s+package\s+`@[\w\-]+/[\w\-]+`",
        ),
        "soft",
        "npm package claim in a public-facing markdown file must have a "
        "`verify:` annotation on the same line or within 7 lines confirming "
        "the package exists. Fabricated npm packages were the cause of the "
        "john-ezra/open-ralph fabrication (commit 58a1d25e9, 2026-06-21).",
    ),
)

# Files where pattern matching is intentionally relaxed (the verifier's own
# documentation, this script, the changelog, etc.).
EXEMPT_FILES: frozenset[Path] = frozenset(
    {
        Path("scripts/verify_social_proof.py"),
        Path("scripts/README.md"),
        Path("CHANGELOG.md"),
        Path("AGENTS.md"),
        Path("CLAUDE.md"),
    }
)

# Directories excluded from the default full-repo scan. These contain
# vendored/generated content that is not our public-facing copy.
# Hidden dirs like .git are excluded by the iterator, not this set.
EXEMPT_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".eggs",
        "site-packages",
    }
)

# Sections within a file that document the rejection list itself. The
# auto-rejection list in SHOWCASE.md, the "RETIRED CLAIMS" block in
# RALPH_WORKFLOW_POSITIONING.md, and similar sections are allowed to *name*
# the banned patterns so a human reader can see them. The gate's purpose is
# public copy, not its own docs.
DOCUMENTING_SECTION_HEADINGS: tuple[str, ...] = (
    "Auto-rejection patterns",
    "Evidence gate",
    "RETIRED CLAIMS",
    "Retracted",
)


def _is_evidence_annotated(file_text: str, line_no: int) -> bool:
    """True if the same logical block as `line_no` contains a `verify:` line."""
    lines = file_text.splitlines()
    lo = max(0, line_no - 7)
    hi = min(len(lines), line_no + 6)
    window = "\n".join(lines[lo:hi])
    return bool(re.search(r"verify:\s*\S", window))


def _is_documenting_section(file_text: str, line_no: int) -> bool:
    """True if `line_no` is in a section that documents the rejection list."""
    lines = file_text.splitlines()
    for i in range(line_no - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            if any(h in stripped for h in DOCUMENTING_SECTION_HEADINGS):
                return True
    return False


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    pattern: str
    severity: str
    file: str
    line_no: int
    line: str
    why: str


def scan(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [
            Finding(
                pattern="<io-error>",
                severity="hard",
                file=str(path),
                line_no=0,
                line="",
                why=f"Could not read file: {exc}",
            )
        ]

    findings: list[Finding] = []
    lines = text.splitlines()
    for name, pattern, severity, why in BANNED_PATTERNS:
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            # Documenting sections (the auto-rejection list itself) are
            # allowed to name the banned patterns.
            if _is_documenting_section(text, line_no):
                continue
            if severity == "soft" and _is_evidence_annotated(text, line_no):
                continue
            findings.append(
                Finding(
                    pattern=name,
                    severity=severity,
                    file=str(path),
                    line_no=line_no,
                    line=line.strip(),
                    why=why,
                )
            )
    return findings


def iter_paths(roots: Iterable[Path]) -> Iterable[Path]:
    """Yield every markdown file under each root, skipping exempt paths."""
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root in EXEMPT_FILES or root in seen:
                continue
            seen.add(root)
            yield root
            continue
        for path in root.rglob("*.md"):
            if path in EXEMPT_FILES or path in seen:
                continue
            # Skip vendored/generated content in excluded dirs
            if any(p.name in EXEMPT_DIRS for p in path.parents):
                continue
            seen.add(path)
            yield path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Ralph Workflow social-proof verifier (gate, do not weaken)."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to the public surfaces "
        "listed in scripts/verify_social_proof.py::DEFAULT_PATHS.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    args = parser.parse_args(argv)

    # Directories that are test fixtures / workflow output — not public copy
    global EXEMPT_DIRS
    _test_fixture_roots: set[str] = set()
    if not args.paths:
        repo = Path(".")
        for d in repo.iterdir():
            if d.is_dir() and d.name.startswith("wt-"):
                _test_fixture_roots.add(d.name)
    if _test_fixture_roots:
        EXEMPT_DIRS = frozenset(EXEMPT_DIRS | _test_fixture_roots)

    if args.paths:
        roots: list[Path] = list(args.paths)
    else:
        # Scan the entire repo. No carve-outs for "unimportant" files.
        # If a markdown file is public-facing, it's in scope.
        roots = [p for p in DEFAULT_ROOTS if p.exists()]

    paths_to_scan = list(iter_paths(roots))
    all_findings: list[Finding] = []
    for path in paths_to_scan:
        all_findings.extend(scan(path))

    if args.json:
        json.dump(
            {
                "scanned": [str(p) for p in paths_to_scan],
                "findings": [asdict(f) for f in all_findings],
                "ok": not all_findings,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        if not all_findings:
            print(
                f"OK: scanned {len(paths_to_scan)} files, "
                "no banned social-proof patterns found."
            )
        else:
            print(
                f"FAIL: {len(all_findings)} banned pattern(s) found.\n",
                file=sys.stderr,
            )
            for f in all_findings:
                print(
                    f"  [{f.severity.upper()}] {f.file}:{f.line_no} "
                    f"({f.pattern})",
                    file=sys.stderr,
                )
                if f.line:
                    print(f"    > {f.line}", file=sys.stderr)
                print(f"    why: {f.why}", file=sys.stderr)
                print(file=sys.stderr)
    return 0 if not all_findings else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
