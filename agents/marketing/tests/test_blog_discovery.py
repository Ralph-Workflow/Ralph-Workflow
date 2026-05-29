"""Test blog-post discovery fixes the OWNED_CONTENT_SOURCE_CANDIDATES blind spot.

Before 2026-05-29, the marketing loop could not see any of the 25 live
Ralph-Site blog posts because OWNED_CONTENT_SOURCE_CANDIDATES was hardcoded
to 4 guide paths.  This test verifies the fix.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path('/home/mistlight/.openclaw/workspace')
sys.path.insert(0, str(ROOT))

from agents.marketing.distribution_lane_selector import (
    _owned_content_source_candidates,
    _owned_content_publication_available,
    OWNED_CONTENT_GUIDE_PATHS,
    OWNED_CONTENT_BLOG_DIR,
)
from agents.marketing.run_posting import (
    crosspost_blog_content,
    already_posted_successfully,
    load_posted,
    digest_text,
)


class TestOwnedContentSourceCandidates:
    def test_blog_dir_included_when_present(self):
        """With 25 real blog posts, source candidates includes blog md files."""
        candidates = _owned_content_source_candidates()

        # Should include at least the blog posts
        blog_mds = [c for c in candidates if str(c).startswith(str(OWNED_CONTENT_BLOG_DIR))]
        assert len(blog_mds) > 0, "Blog directory should contribute source candidates"

    def test_guides_still_included(self):
        """Guide paths that exist should still be in candidates."""
        candidates = _owned_content_source_candidates()
        candidate_strs = {str(c) for c in candidates}
        for guide_path in OWNED_CONTENT_GUIDE_PATHS:
            if guide_path.exists():
                assert str(guide_path) in candidate_strs, (
                    f"Existing guide {guide_path} must be in candidates"
                )

    def test_result_includes_both_sources(self):
        """Candidates should come from both guides and blog."""
        candidates = _owned_content_source_candidates()
        guide_count = sum(
            1 for c in candidates
            if str(c) in {str(p) for p in OWNED_CONTENT_GUIDE_PATHS if p.exists()}
        )
        blog_count = sum(
            1 for c in candidates
            if str(c).startswith(str(OWNED_CONTENT_BLOG_DIR))
        )
        assert guide_count + blog_count == len(candidates), (
            "Every candidate should be traceable to either a guide or blog path"
        )

    def test_no_duplicate_candidates(self):
        """Every candidate path should be unique."""
        candidates = _owned_content_source_candidates()
        assert len(candidates) == len(set(candidates))

    def test_blog_candidates_are_sorted(self):
        """Blog candidates should be sorted for deterministic behaviour."""
        candidates = _owned_content_source_candidates()
        blog_candidates = [
            c for c in candidates
            if str(c).startswith(str(OWNED_CONTENT_BLOG_DIR))
        ]
        assert blog_candidates == sorted(blog_candidates)


class TestPublicationAvailable:
    def test_returns_bool(self):
        """_owned_content_publication_available returns a boolean."""
        result = _owned_content_publication_available()
        assert isinstance(result, bool)

    def test_more_candidates_than_old_hardcoded_list(self):
        """Dynamic discovery sees at least as many candidates as the old list."""
        candidates = _owned_content_source_candidates()
        old_constant_count = len([p for p in OWNED_CONTENT_GUIDE_PATHS if p.exists()])
        # Blog dir must add candidates beyond the old hardcoded list
        assert len(candidates) > old_constant_count, (
            f"Dynamic list ({len(candidates)}) should exceed old constant ({old_constant_count})"
        )


class TestCrosspostBlogContent:
    def test_dry_run_discovers_blog_posts(self):
        """Dry-run mode discovers blog posts and marks them as dry_run_skipped."""
        posted = load_posted()
        today = datetime.now().strftime("%Y-%m-%d")
        results = crosspost_blog_content(posted, today, dry_run=True)
        # At least some blog posts should be discoverable
        blog_files = list(OWNED_CONTENT_BLOG_DIR.glob('*.md'))
        assert len(blog_files) > 0, "Test requires blog directory with content"
        # Dry run should produce records for uncrossposted blogs
        assert len(results) > 0, "Dry-run should discover uncrossposted blog posts"

    def test_already_crossposted_skipped(self):
        """Posts already tracked with source_path are not re-crossposted."""
        posted = load_posted()
        # Artificially mark a blog post as already posted
        blog_files = list(OWNED_CONTENT_BLOG_DIR.glob('*.md'))
        if not blog_files:
            pytest.skip("No blog files to test with")
        source_str = str(blog_files[0])
        posted.setdefault("posts", []).append({
            "platform": "telegraph",
            "ok": True,
            "source_path": source_str,
        })
        today = datetime.now().strftime("%Y-%m-%d")
        results = crosspost_blog_content(posted, today, dry_run=True)
        already_marked = [r for r in results if r.get("source_path") == source_str]
        assert len(already_marked) == 0, "Already-crossposted blog should be skipped"

    def test_returns_list_of_records(self):
        """Returns a list of dict records."""
        posted = load_posted()
        today = datetime.now().strftime("%Y-%m-%d")
        results = crosspost_blog_content(posted, today, dry_run=True)
        assert isinstance(results, list)
        for record in results:
            assert isinstance(record, dict)
            assert "title" in record
            assert "platform" in record
            assert "source_path" in record
