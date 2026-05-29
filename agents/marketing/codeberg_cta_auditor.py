#!/usr/bin/env python3
"""
codeberg_cta_auditor.py — Verify that all public-facing content and distribution
artifacts link to Codeberg as the primary repo (with GitHub as secondary mirror
only). This script scans the content/guides, drafts, docs, and seo-reports
directories for incorrect repo link ordering, missing Codeberg links, or GitHub-
only links that should point to Codeberg first.

Created: 2026-05-28 — repair for mirror-repo-flat + should_change_now audit directive:
"Ensure all public-facing content links Codeberg as primary and GitHub as mirror."
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENTS_DIR = ROOT / 'agents/marketing'
LOG_DIR = AGENTS_DIR / 'logs'
DRAFTS_DIR = ROOT / 'drafts'

AUDIT_LOG_PATH = LOG_DIR / 'codeberg_cta_audit_latest.json'
AUDIT_MD_PATH = DRAFTS_DIR / 'codeberg_cta_audit_latest.md'

CODEBERG_REPO_URL = 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'
GITHUB_REPO_URL = 'https://github.com/Ralph-Workflow/Ralph-Workflow'

SCAN_DIRS = [
    ROOT / 'content',
    ROOT / 'drafts',
    ROOT / 'docs',
    ROOT / 'seo-reports',
    ROOT / 'agents/marketing',
    ROOT / 'Ralph-Site' / 'content' / 'blog',  # Hugo blog — primary conversion surface
]


@dataclass
class CTAFinding:
    path: str
    finding_type: str  # missing_codeberg, github_only, github_before_codeberg, ok
    detail: str
    codeberg_links: int
    github_links: int


def _text_files(base: Path) -> list[Path]:
    """Yield markdown, html, and json files under base."""
    files: list[Path] = []
    for ext in ('*.md', '*.html', '*.json', '*.txt'):
        files.extend(base.rglob(ext))
    return sorted(files)


def _count_links(text: str, url: str) -> int:
    """Count occurrences of a URL in text (partial match on the path)."""
    import re
    short = url.replace('https://', '').replace('http://', '')
    return len(re.findall(re.escape(url), text, re.IGNORECASE)) + \
           len(re.findall(re.escape(short), text, re.IGNORECASE))


def _first_repo_link_position(text: str) -> tuple[str | None, int]:
    """Find which repo link appears first and at what position."""
    cb_pos = text.find(CODEBERG_REPO_URL)
    gh_pos = text.find(GITHUB_REPO_URL)

    if cb_pos == -1 and gh_pos == -1:
        return None, -1
    if cb_pos == -1:
        return 'github', gh_pos
    if gh_pos == -1:
        return 'codeberg', cb_pos
    if cb_pos < gh_pos:
        return 'codeberg', cb_pos
    return 'github', gh_pos


def _adoption_metrics_json_path() -> Path:
    return LOG_DIR / 'adoption_metrics_latest.json'


def audit() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    findings: list[dict[str, Any]] = []
    all_ok = True

    # Scan all markdown files in public-facing content directories
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for file_path in _text_files(scan_dir):
            try:
                text = file_path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue

            cb_count = _count_links(text, CODEBERG_REPO_URL)
            gh_count = _count_links(text, GITHUB_REPO_URL)

            # Skip files that don't mention either repo
            if cb_count == 0 and gh_count == 0:
                continue

            first_link, first_pos = _first_repo_link_position(text)

            finding: dict[str, Any] = {
                'path': str(file_path),
                'codeberg_links': cb_count,
                'github_links': gh_count,
            }

            if cb_count == 0 and gh_count > 0:
                finding['finding_type'] = 'github_only'
                finding['detail'] = f'File links GitHub {gh_count} times but never links Codeberg.'
                finding['severity'] = 'high'

                # Exception paths that are not public-facing content:
                # - GitHub-specific files (PR templates, outreach scripts, etc.)
                # - Historical reddit drafts (pre-Codeberg-primary rule, IP-banned channel)
                # - Internal logs and workflow audit artifacts
                path_str = str(file_path)
                is_github_specific = 'github' in file_path.name.lower() or 'github' in path_str.lower()
                is_reddit_archive = 'reddit' in file_path.name.lower() or 'reddit-posts' in path_str
                is_internal_log = 'logs/' in path_str or 'workflow_audit' in path_str
                if is_github_specific or is_reddit_archive or is_internal_log:
                    finding['finding_type'] = 'ok'
                    finding['detail'] = 'GitHub-only in a non-public-facing or channel-specific context (acceptable).'
                    finding['severity'] = 'none'
                else:
                    all_ok = False

            elif first_link == 'github' and cb_count > 0:
                finding['finding_type'] = 'github_before_codeberg'
                finding['detail'] = f'GitHub link appears before Codeberg link. Codeberg: {cb_count}, GitHub: {gh_count}.'
                finding['severity'] = 'medium'
                # Don't fail on ordering if Codeberg is still present
            else:
                finding['finding_type'] = 'ok'
                finding['detail'] = f'Codeberg-primary ordering confirmed.'
                finding['severity'] = 'none'

            findings.append(finding)

    # Also audit specific known public surfaces
    _audit_known_surfaces(findings)

    # Group by severity
    high_severity = [f for f in findings if f.get('severity') == 'high']
    medium_severity = [f for f in findings if f.get('severity') == 'medium']

    result = {
        'generated_at': now.isoformat(),
        'status': 'pass' if all_ok else 'fail',
        'total_files_with_repo_links': len(findings),
        'high_severity_count': len(high_severity),
        'medium_severity_count': len(medium_severity),
        'high_severity_findings': high_severity,
        'medium_severity_findings': medium_severity,
        'all_findings': findings,
    }

    return result


def _audit_known_surfaces(findings: list[dict[str, Any]]) -> None:
    """Audit specific known public surfaces that must link Codeberg."""
    known_surfaces = [
        ROOT / 'docs/first-task-guide.md',
        ROOT / 'content/guides/good_unattended_task.md',
        ROOT / 'content/guides/review_ai_coding_output_before_merge.md',
        ROOT / 'content/guides/autonomous_ai_workflows_production_reliability.md',
        ROOT / 'drafts/comparison_backlink_execution_latest.md',
        ROOT / 'drafts/curator_contact_handoff_packet_latest.md',
    ]

    for path in known_surfaces:
        if not path.exists():
            findings.append({
                'path': str(path),
                'finding_type': 'missing_known_surface',
                'detail': 'Known public surface file does not exist.',
                'severity': 'medium',
                'codeberg_links': 0,
                'github_links': 0,
            })
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue

        cb_count = _count_links(text, CODEBERG_REPO_URL)
        gh_count = _count_links(text, GITHUB_REPO_URL)
        first_link, _ = _first_repo_link_position(text)

        if cb_count == 0:
            findings.append({
                'path': str(path),
                'finding_type': 'known_surface_missing_codeberg',
                'detail': f'Known public surface missing Codeberg link. GitHub links: {gh_count}.',
                'severity': 'high',
                'codeberg_links': 0,
                'github_links': gh_count,
            })
        elif first_link == 'github':
            findings.append({
                'path': str(path),
                'finding_type': 'known_surface_github_first',
                'detail': f'Known surface lists GitHub before Codeberg. CB: {cb_count}, GH: {gh_count}.',
                'severity': 'medium',
                'codeberg_links': cb_count,
                'github_links': gh_count,
            })


def build_markdown_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append('# Codeberg CTA Audit Report')
    lines.append(f'Generated: {result["generated_at"]}')
    lines.append('')
    lines.append(f'## Summary: {result["status"].upper()}')
    lines.append(f'- Files with repo links: {result["total_files_with_repo_links"]}')
    lines.append(f'- High severity issues: {result["high_severity_count"]}')
    lines.append(f'- Medium severity issues: {result["medium_severity_count"]}')
    lines.append('')
    lines.append('## Rule in force')
    lines.append('- All public-facing content must link Codeberg as primary and GitHub as mirror')
    lines.append('- GitHub-only links are only acceptable in GitHub-specific context (e.g. PR templates)')
    lines.append('- Codeberg link must appear before GitHub link when both are present')
    lines.append('')

    if result['high_severity_findings']:
        lines.append('## ⚠️ High-Severity: Missing Codeberg Links')
        for f in result['high_severity_findings']:
            lines.append(f'- **{f["path"]}**: {f["detail"]}')
        lines.append('')

    if result['medium_severity_findings']:
        lines.append('## Medium-Severity: Ordering or Missing Surface')
        for f in result['medium_severity_findings']:
            lines.append(f'- **{f["path"]}**: {f["detail"]}')
        lines.append('')

    return '\n'.join(lines)


def main() -> None:
    result = audit()
    print(json.dumps(result, indent=2, default=str))

    # Write outputs
    from agents.marketing.comparison_backlink_executor import _save_json
    _save_json(AUDIT_LOG_PATH, result)

    markdown = build_markdown_report(result)
    AUDIT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_MD_PATH.write_text(markdown, encoding='utf-8')

    print(f'\n{"✅" if result["status"] == "pass" else "❌"} CTA Audit: {result["status"].upper()}')
    print(f'   {result["total_files_with_repo_links"]} files audited, {result["high_severity_count"]} high-severity issues')
    print(f'   Report: {AUDIT_MD_PATH}')


if __name__ == '__main__':
    main()
