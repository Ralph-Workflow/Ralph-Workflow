#!/usr/bin/env python3
"""
test_codeberg_cta_auditor.py — Tests for the CTA auditor.

Covers:
1. Every blog post under Ralph-Site/content/blog/ must have at least one
   Codeberg primary-repo link (codeberg.org/RalphWorkflow/Ralph-Workflow).
2. When both Codeberg and GitHub links appear, Codeberg must appear first.
3. Static CTA fixture: every blog file containing repo links must pass the
   ordering rule (portable enforcement, no magic file exclusions).
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
BLOG_DIR = ROOT / 'Ralph-Site' / 'content' / 'blog'

CODEBERG_REPO = 'codeberg.org/RalphWorkflow/Ralph-Workflow'
GITHUB_REPO = 'github.com/Ralph-Workflow/Ralph-Workflow'

# Blog files that intentionally don't link the repo (meta/index posts):
ALLOWED_NO_REPO_LINK: set[str] = set()
# GitHub-specific context files where github-only is acceptable:
ALLOWED_GITHUB_ONLY: set[str] = set()


def _blog_md_files() -> list[Path]:
    return sorted(BLOG_DIR.glob('*.md'))


class CodebergCTABlogCoverageTests(unittest.TestCase):
    """Every blog post must have at least one Codeberg primary link."""

    def test_all_blog_posts_have_codeberg_link(self):
        missing: list[str] = []
        for path in _blog_md_files():
            if path.name in ALLOWED_NO_REPO_LINK:
                continue
            text = path.read_text(encoding='utf-8')
            if CODEBERG_REPO not in text:
                missing.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            missing, [],
            f'Blog posts missing Codeberg primary repo link: {missing}\n'
            f'Hint: add a CTA block with codeberg.org/RalphWorkflow/Ralph-Workflow to each.'
        )

    def test_blog_posts_keep_codeberg_ahead_of_github(self):
        offenders: list[str] = []
        for path in _blog_md_files():
            if path.name in ALLOWED_GITHUB_ONLY:
                continue
            text = path.read_text(encoding='utf-8')
            cb_idx = text.find(CODEBERG_REPO)
            gh_idx = text.find(GITHUB_REPO)
            if gh_idx != -1 and (cb_idx == -1 or cb_idx > gh_idx):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            offenders, [],
            f'Blog posts with GitHub before Codeberg: {offenders}\n'
            f'Codeberg must appear before GitHub mirror in all blog posts.'
        )

    def test_codeberg_cta_auditor_passes(self):
        """The codeberg_cta_auditor must return status=pass."""
        from agents.marketing.codeberg_cta_auditor import audit
        result = audit()
        self.assertEqual(
            result['status'], 'pass',
            f'CTA auditor status={result["status"]!r}, '
            f'high_severity={result["high_severity_count"]}:\n'
            f'{result["high_severity_findings"]}'
        )


if __name__ == '__main__':
    unittest.main()
