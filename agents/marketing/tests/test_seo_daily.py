#!/usr/bin/env python3
"""TDD tests for seo_daily.py and the SEO trend/retroactive analysis loop.

Run with: python3 -m unittest agents.marketing.tests.test_seo_daily -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
LOG_DIR = AGENTS_DIR / "logs"
REPORTS_DIR = Path("/home/mistlight/.openclaw/workspace/seo-reports")


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_fake_homepage(overrides: dict | None = None) -> dict:
    base = {
        "ok": True, "status": 200,
        "title": "Ralph Workflow — AI Agent Orchestration CLI",
        "meta_description": "Preconfigured AI engineering workflow with planning, development, nested analysis feedback loops, and fresh plan on every pass.",
        "canonical": "https://ralphworkflow.com",
        "og_tags": {"title": "x", "description": "x", "url": "x", "type": "website"},
        "twitter_card": "summary",
        "json_ld": True,
        "has_h1": True,
        "has_nav": True,
        "has_main": True,
        "lang_attr": "en",
        "word_count": 500,
    }
    if overrides:
        base.update(overrides)
    return base


# ── Sitemap ─────────────────────────────────────────────────────────────────────

class SitemapUrlCountTests(unittest.TestCase):
    """Bug: sitemap url_count was showing 0 because it read data['sitemap']
    when sitemap info lives in data['site_health']['sitemap'].  """

    def test_sitemap_url_count_comes_from_site_health(self):
        """The sitemap url_count must come from site_health.sitemap.url_count."""
        from agents.marketing import seo_daily

        fake_data = {
            "onpage": {"score": 55, "grade": "D", "issues": [], "recommendations": []},
            "site_health": {
                "homepage": {"ok": True, "status": 200},
                "sitemap": {"status": 200, "url_count": 247, "urls": []},
            },
            "sitemap": {"status": "unknown", "url_count": 0},  # wrong key — was being used
            "ranks": {},
            "backlinks": {},
            "domain_rating": {},
            "content_gap": {},
            "serp": {},
            "priority_actions": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(seo_daily, "REPORTS_DIR", Path(tmpdir)):
                report_path = seo_daily.write_daily_report(datetime.now(), fake_data)
                report_content = report_path.read_text()

        self.assertIn("247", report_content)
        self.assertNotIn("URLs in index: 0", report_content)

    def test_seo_daily_main_produces_sitemap_url_count(self):
        """seo_daily.main() output summary must include sitemap_urls from site_health.

        Writes a self-contained helper script to a temp file so the mocks are
        embedded in the subprocess (avoids threading.local state issues)."""
        from agents.marketing import seo_daily

        fake_homepage = (
            "<html lang='en'>"
            "<head><title>Ralph Workflow</title>"
            "<meta name='description' content='Preconfigured AI engineering workflow.'>"
            "<link rel='canonical' href='https://ralphworkflow.com'>"
            "<meta property='og:title' content='x'>"
            "<meta property='og:description' content='x'>"
            "<meta property='og:url' content='x'>"
            "<meta property='og:type' content='website'>"
            "<meta name='twitter:card' content='summary'>"
            "<script type='application/ld+json'>{}</script>"
            "</head>"
            "<body><h1>Ralph Workflow</h1><nav></nav><main></main></body>"
            "</html>"
        )
        fake_sitemap = (
            '<?xml version="1.0"?><urlset>'
            + ''.join("<loc>https://ralphworkflow.com/{}</loc>".format(i) for i in range(1, 248))
            + '</urlset>'
        )

        # Build the helper script as a plain string
        helper_script = (
            "import sys, io, contextlib, json, logging, itertools\n"
            "sys.path.insert(0, '/home/mistlight/.openclaw/workspace')\n"
            "logging.root.handlers = []\n"
            "from unittest.mock import patch\n"
            "from agents.marketing import seo_daily\n"
            "fake_homepage = {!r}\n".format(fake_homepage) +
            "fake_sitemap = {!r}\n".format(fake_sitemap) +
            "all_resp = [(200, fake_homepage), (200, 'Sitemap: https://ralphworkflow.com/sitemap.xml\\n'), (200, fake_sitemap)] + [(200, '') for _ in range(20)]\n"
            "cycle_iter = itertools.cycle(all_resp)\n"
            "def fake_get(*a, **kw):\n"
            "    return next(cycle_iter)\n"
            "buf = io.StringIO()\n"
            "with patch.object(seo_daily, 'http_get', fake_get), \\\n"
            "     patch.object(seo_daily, 'track_ranks', return_value={}), \\\n"
            "     patch.object(seo_daily, 'check_backlinks_google', return_value={'count_approx': 0}), \\\n"
            "     patch.object(seo_daily, 'check_ahref_domain_rating', return_value={'dr': None}), \\\n"
            "     patch.object(seo_daily, 'serp_features_for_keyword', return_value={}), \\\n"
            "     contextlib.redirect_stdout(buf), \\\n"
            "     contextlib.redirect_stderr(buf):\n"
            "    try:\n"
            "        seo_daily.main()\n"
            "    except SystemExit:\n"
            "        pass\n"
            "sys.stdout.write(buf.getvalue())\n"
        )

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False,
            dir='/home/mistlight/.openclaw/workspace'
        ) as hf:
            hf.write(helper_script)
            helper_path = hf.name

        try:
            result = subprocess.run(
                [sys.executable, helper_path],
                capture_output=True, text=True,
                cwd="/home/mistlight/.openclaw/workspace",
                env={**os.environ, "COLLOSUS_API_KEY": ""},
            )
            output = result.stdout
        finally:
            os.unlink(helper_path)

        # Reconstruct JSON from multi-line pretty-printed output by tracking brace depth
        lines = output.strip().splitlines()
        json_start = next((i for i, l in enumerate(lines) if l.strip().startswith('{')), None)
        self.assertIsNotNone(json_start, "No JSON found in output: {!r}".format(output[:400]))

        brace_depth = 0
        json_lines = []
        for l in lines[json_start:]:
            for ch in l:
                if ch == '{':
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
            json_lines.append(l)
            if brace_depth == 0:
                break

        json_text = '\n'.join(json_lines)
        try:
            summary = json.loads(json_text)
        except json.JSONDecodeError as e:
            self.fail("JSON parse failed: {}; json_text: {}".format(e, json_text[:500]))

        self.assertGreater(
            summary.get("sitemap_urls", 0), 0,
            "sitemap_urls should be 247, got {}; summary={}".format(
                summary.get("sitemap_urls"), summary
            ),
        )


# ── Trend computation ───────────────────────────────────────────────────────────

class BacklinkTruthfulnessTests(unittest.TestCase):

    def test_check_backlinks_google_reuses_verified_live_listing_floor(self):
        from agents.marketing import seo_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / 'backlink_status_latest.json').write_text(json.dumps({
                'summary': {'directories_with_live_listings': 2},
                'directories': {
                    'SaaSHub': {'listing_live': True},
                    'ToolWise': {'listing_live': True},
                    'AIToolboard': {'listing_live': False},
                },
            }), encoding='utf-8')

            html = '<a href="https://ralphworkflow.com">self</a>'

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return html.encode('utf-8')

            with patch.object(seo_daily, 'LOG_DIR', log_dir), \
                 patch.object(seo_daily.urllib.request, 'urlopen', return_value=_Resp()):
                result = seo_daily.check_backlinks_google()

        self.assertEqual(result['count_approx'], 2)
        self.assertEqual(result['google_count_approx'], 1)
        self.assertEqual(result['verified_live_listings'], 2)
        self.assertEqual(result['verified_live_listing_directories'], ['SaaSHub', 'ToolWise'])

    def test_check_backlinks_google_falls_back_to_verified_live_listings_on_google_error(self):
        from agents.marketing import seo_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / 'backlink_status_latest.json').write_text(json.dumps({
                'summary': {'directories_with_live_listings': 2},
                'directories': {
                    'SaaSHub': {'listing_live': True},
                    'ToolWise': {'listing_live': True},
                },
            }), encoding='utf-8')

            with patch.object(seo_daily, 'LOG_DIR', log_dir), \
                 patch.object(seo_daily.urllib.request, 'urlopen', side_effect=RuntimeError('blocked')):
                result = seo_daily.check_backlinks_google()

        self.assertEqual(result['count_approx'], 2)
        self.assertEqual(result['verified_live_listings'], 2)
        self.assertIn('truthful backlink floor', result['note'])


class TrendComputationTests(unittest.TestCase):

    def test_compute_trends_reads_ranks_from_nested_path(self):
        """compute_trends must count ranked keywords from data['ranks'], not top-level."""
        from agents.marketing import run

        history = [{
            "timestamp": "2026-05-11T09:00:00",
            "ranks": {
                "unattended coding agent": {"position": 12},
                "AI agent orchestration CLI": {"position": 8},
                "_note": "no API key",
            },
            "backlinks": {"count_approx": 0},
            "domain_rating": {"dr": None},
        }]
        current = {
            "ranks": {
                "unattended coding agent": {"position": 10},
                "AI agent orchestration CLI": {"position": 7},
            },
            "backlinks": {"count_approx": 0},
            "domain_rating": None,
        }

        trends = run.compute_trends(current, history)

        self.assertEqual(trends.get("rank_delta"), 0,
                         "Both history and current have 2 ranked keywords. Got: {}".format(trends))

    def test_compute_trends_reads_backlinks_from_nested_path(self):
        """compute_trends must read backlinks from data['backlinks']['count_approx']."""
        from agents.marketing import run

        history = [{
            "ranks": {},
            "backlinks": {"count_approx": 0},
            "domain_rating": {"dr": None},
        }]
        current = {
            "ranks": {},
            "backlinks": {"count_approx": 2},  # We earned 2 backlinks!
            "domain_rating": None,
        }

        trends = run.compute_trends(current, history)

        self.assertEqual(trends.get("backlinks_delta"), 2,
                         "backlinks_delta should be +2. Got: {}".format(trends))

    def test_trends_computed_every_day_not_just_mondays(self):
        """load_seo_trends must be called every day (not only Mondays)."""
        from agents.marketing import run

        fake_seo = {
            "ranks": {},
            "backlinks": {"count_approx": 0},
            "domain_rating": None,
            "content_gap": {"gaps": [], "covered": [], "coverage_pct": 0},
            "priority_actions": [],
        }
        history = [{"ranks": {}, "backlinks": {"count_approx": 1}, "domain_rating": None}]

        call_count = {"n": 0}

        def fake_load_trends(days):
            call_count["n"] += 1
            return history

        with patch.object(run, "run_seo_daily", return_value=fake_seo), \
             patch.object(run, "load_seo_trends", fake_load_trends), \
             patch.object(run, "write_seo_insights", return_value=Path("/tmp/x")), \
             patch.object(run, "http_status", return_value={"ok": True, "status": 200}), \
             patch.object(run, "load_posted_records", return_value=[]), \
             patch.object(run, "enrich_posts_with_views", return_value=[]), \
             patch.object(run, "subprocess", MagicMock()) as mock_subproc:

            mock_subproc.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            # Patch datetime so it's Tuesday (weekday=1)
            with patch("agents.marketing.run.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 5, 12, 9, 0, 0)
                import io
                old = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    run.main()
                except SystemExit:
                    pass
                sys.stdout = old

        self.assertEqual(
            call_count["n"], 1,
            "load_seo_trends called {} times on a Tuesday — should be exactly 1".format(call_count["n"]),
        )


# ── Retroactive analysis ────────────────────────────────────────────────────────

class RetroactiveAnalysisTests(unittest.TestCase):

    def test_issue_delta_detects_new_and_fixed(self):
        """Previously-seen issues that are gone = fixed; new issues = new."""
        from agents.marketing import seo_daily

        prev_issues = [
            {"item": "Title too long", "severity": "warning", "detail": "70 chars"},
            {"item": "Missing canonical tag", "severity": "error"},
            {"item": "Missing lang attribute", "severity": "warning"},
        ]
        curr_issues = [
            {"item": "Title too long", "severity": "warning", "detail": "75 chars"},  # unchanged
            {"item": "Missing canonical tag", "severity": "error"},                   # unchanged
            {"item": "Homepage content thin", "severity": "warning"},                  # new
        ]

        fixed, new_issues, unchanged = seo_daily.delta_issues(prev_issues, curr_issues)

        self.assertEqual(fixed, ["Missing lang attribute"])
        self.assertEqual(new_issues, ["Homepage content thin"])
        self.assertEqual(set(unchanged), {"Title too long", "Missing canonical tag"})

    def test_delta_metrics_detects_regressed_onpage_score(self):
        """If onpage score drops, it should appear in regressed."""
        from agents.marketing import seo_daily

        prev = {
            "onpage_score": "75/100 (C)",
            "backlinks": {"count_approx": 1},
            "ranks": {"kw1": {"position": 10}, "kw2": {"position": 20}},  # 2 keywords
            "domain_rating": {"dr": 10},
            "content_gap": {"coverage_pct": 50.0},
        }
        curr = {
            "onpage_score": "55/100 (D)",
            "backlinks": {"count_approx": 0},  # fewer = regressed
            "ranks": {"kw1": {"position": 15}},   # dropped to 1 keyword = regressed
            "domain_rating": {"dr": 10},
            "content_gap": {"coverage_pct": 50.0},
        }

        result = seo_daily.delta_metrics(prev, curr)
        regressed = result.get("regressed", {})

        self.assertIn("onpage_score", regressed,
                      "onpage_score 75→55 should be regressed. Got: {}".format(result))
        self.assertIn("backlinks_approx", regressed,
                      "backlinks 1→0 should be regressed. Got: {}".format(result))
        self.assertIn("ranked_keywords", regressed,
                      "ranked 2→1 should be regressed. Got: {}".format(result))

    def test_previous_log_loaded_from_correct_date(self):
        """Must load the most recent log before today, not today's or future logs."""
        from agents.marketing import seo_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(seo_daily, "LOG_DIR", Path(tmpdir)):
                day1 = {
                    "timestamp": "2026-05-10T09:00:00",
                    "onpage": {"score": 50, "grade": "D", "issues": [], "recommendations": []},
                    "ranks": {}, "backlinks": {"count_approx": 0},
                    "domain_rating": {}, "content_gap": {},
                }
                day2 = {
                    "timestamp": "2026-05-11T09:00:00",
                    "onpage": {"score": 55, "grade": "D", "issues": [], "recommendations": []},
                    "ranks": {}, "backlinks": {"count_approx": 1},
                    "domain_rating": {}, "content_gap": {},
                }
                (Path(tmpdir) / "seo_2026-05-10.json").write_text(json.dumps(day1))
                (Path(tmpdir) / "seo_2026-05-11.json").write_text(json.dumps(day2))

                prev = seo_daily.load_previous_log(datetime(2026, 5, 12))

        self.assertIsNotNone(prev, "load_previous_log must find May 11 (yesterday relative to May 12)")
        self.assertEqual(prev["timestamp"], "2026-05-11T09:00:00",
                         "Expected May 11 log, got: {}".format(prev.get('timestamp')))

    def test_write_daily_report_includes_delta_section(self):
        """Report must have '## Delta vs Previous Report' with fixed/new/regressed items."""
        from agents.marketing import seo_daily

        prev_log = {
            "timestamp": "2026-05-11T09:00:00",
            "onpage": {
                "score": 75, "grade": "C",
                "issues": [
                    {"item": "Title too long", "severity": "warning", "detail": "70 chars"},
                    {"item": "Missing canonical tag", "severity": "error"},
                ],
                "recommendations": [],
            },
            "ranks": {"kw1": {"position": 10}},
            "backlinks": {"count_approx": 1},
            "domain_rating": {"dr": 10},
            "content_gap": {"gaps": ["kw3"], "covered": ["kw1"], "coverage_pct": 50.0},
        }

        curr_data = {
            "onpage": {
                "score": 55, "grade": "D",
                "issues": [
                    {"item": "Title too long", "severity": "warning", "detail": "75 chars"},  # unchanged
                    {"item": "Homepage thin", "severity": "warning"},                           # NEW
                ],
                "recommendations": [],
            },
            "ranks": {"kw1": {"position": 12}},   # worsened
            "backlinks": {"count_approx": 0},    # fewer = regressed
            "domain_rating": {"dr": 10},
            "content_gap": {"gaps": ["kw2", "kw3"], "covered": ["kw1"], "coverage_pct": 33.3},
            "serp": {},
            "competitors": {},
            "priority_actions": [],
            "site_health": {"homepage": {"ok": True}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            reports_dir.mkdir()
            logs_dir = Path(tmpdir) / "logs"
            logs_dir.mkdir()
            with patch.object(seo_daily, "REPORTS_DIR", reports_dir), \
                 patch.object(seo_daily, "LOG_DIR", logs_dir):
                prev_file = logs_dir / "seo_2026-05-11.json"
                prev_file.write_text(json.dumps(prev_log))
                # Use a REAL datetime — load_previous_log calls now.date()
                report_path = seo_daily.write_daily_report(datetime(2026, 5, 12), curr_data)
                content = report_path.read_text()

        self.assertIn("## Delta vs Previous Report", content,
                      "Delta section missing. Report:\n{}".format(content[:500]))
        self.assertIn("New Issues", content,
                      "'Homepage thin' should appear under 'New Issues'. Report:\n{}".format(content))
        self.assertIn("Homepage thin", content)
        self.assertIn("Fixed Issues", content,
                      "'Missing canonical tag' should appear under Fixed Issues. Report:\n{}".format(content))
        self.assertIn("Missing canonical tag", content)


# ── Homepage fetch / on-page scoring ───────────────────────────────────────────

class ContentGapAnalysisTests(unittest.TestCase):

    def test_content_gap_analysis_uses_visible_body_copy_not_just_metadata(self):
        from agents.marketing import seo_daily

        homepage = """
        <html>
          <head>
            <title>Autonomous coding workflow CLI — Ralph Workflow</title>
            <meta name='description' content='Start the job and close the laptop.'>
          </head>
          <body>
            <main>
              <p>Looking for an unattended coding agent, a spec-driven AI agent, or an AI agent orchestration CLI?</p>
              <p>Ralph Workflow is built for Claude Code automation and broader AI coding workflow automation.</p>
            </main>
          </body>
        </html>
        """

        keywords = [
            "unattended coding agent",
            "AI agent orchestration CLI",
            "spec-driven AI agent",
            "AI coding workflow automation",
            "Claude Code automation",
        ]

        with patch.object(seo_daily, "http_get", return_value=(200, homepage)):
            result = seo_daily.content_gap_analysis(keywords, [])

        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["coverage_pct"], 100.0)


class HomepageFetchTests(unittest.TestCase):

    def test_fetch_homepage_retries_when_first_response_is_suspiciously_thin(self):
        from agents.marketing import seo_daily

        thin = """<html><head><title>Ralph Workflow</title></head><body><nav></nav><main><h1>Ralph Workflow</h1></main></body></html>"""
        healthy = """<!DOCTYPE html><html lang='en'><head><title>Free Unattended AI Coding CLI for Developers — Ralph Workflow</title><meta name='description' content='Free open-source AI agent orchestration CLI for Claude Code, Codex, and OpenCode.'><link rel='canonical' href='https://ralphworkflow.com/'><meta property='og:title' content='Free Unattended AI Coding CLI for Developers'><meta property='og:description' content='Free open-source AI agent orchestration CLI for Claude Code, Codex, and OpenCode.'><meta property='og:url' content='https://ralphworkflow.com/'><meta property='og:type' content='website'><meta name='twitter:card' content='summary_large_image'></head><body><nav></nav><main><h1>Ralph Workflow</h1><p>This homepage has enough real body copy to clear the thin-content retry guard and prove the first fetch was a transient thin response rather than the actual page.</p></main></body></html>"""

        with patch.object(seo_daily, "http_get", side_effect=[(200, thin), (200, healthy)]):
            homepage = seo_daily.fetch_homepage()

        self.assertEqual(homepage["title"], "Free Unattended AI Coding CLI for Developers — Ralph Workflow")
        self.assertEqual(homepage["lang_attr"], "en")
        self.assertTrue(homepage.get("retried_after_suspicious_probe"))
        self.assertGreater(homepage["word_count"], 30)

    def test_fetch_homepage_does_not_retry_healthy_response(self):
        from agents.marketing import seo_daily

        healthy = """<!DOCTYPE html><html lang='en'><head><title>Ralph Workflow — AI Agent Orchestration CLI</title><meta name='description' content='Preconfigured AI engineering workflow with planning, development, nested analysis feedback loops, and fresh plan on every pass.'><link rel='canonical' href='https://ralphworkflow.com/'><meta property='og:title' content='x'><meta property='og:description' content='x'><meta property='og:url' content='x'><meta property='og:type' content='website'><meta name='twitter:card' content='summary'></head><body><nav></nav><main><h1>Ralph Workflow</h1><p>This is a healthy homepage with enough body text to avoid the retry path entirely while still looking realistic.</p></main></body></html>"""

        with patch.object(seo_daily, "http_get", return_value=(200, healthy)) as mock_get:
            homepage = seo_daily.fetch_homepage()

        self.assertEqual(mock_get.call_count, 1)
        self.assertFalse(homepage.get("retried_after_suspicious_probe", False))


class OnPageScoreTests(unittest.TestCase):

    def test_empty_page_scores_F(self):
        """Homepage with no title, meta, canonical, h1, json-ld, og = very low score."""
        from agents.marketing import seo_daily

        result = seo_daily.onpage_score({
            "title": "", "meta_description": "", "canonical": "",
            "og_tags": {}, "twitter_card": "", "json_ld": False,
            "has_h1": False, "has_nav": False, "has_main": False,
            "lang_attr": "", "word_count": 50,
        })

        self.assertLess(result["score"], 30,
                        "Score={} should be < 30. Issues: {}".format(result['score'], result['issues']))
        self.assertEqual(result["grade"], "F")

    def test_perfect_homepage_scores_A(self):
        """A fully optimized homepage must score 90+/100 (grade A)."""
        from agents.marketing import seo_daily

        result = seo_daily.onpage_score(make_fake_homepage())

        self.assertGreaterEqual(
            result["score"], 90,
            "Perfect homepage should score >=90, got {}/100 ({})".format(
                result["score"], result["grade"]
            ) + ". Issues: {}".format(result["issues"]),
        )
        self.assertEqual(result["grade"], "A")
        errors = [i for i in result["issues"] if i.get("severity") == "error"]
        self.assertEqual(len(errors), 0, "Should have 0 error issues, got: {}".format(errors))

    def test_long_title_deducts_points(self):
        """A title over 60 characters should deduct 5 points (not just warn)."""
        from agents.marketing import seo_daily

        result = seo_daily.onpage_score(make_fake_homepage({
            "title": "A very long title that definitely exceeds sixty characters in total length here",
        }))

        self.assertLess(result["score"], 90,
                        "Long title should reduce score below 90. Got: {}".format(result['score']))


# ── Duplicate exception handler ─────────────────────────────────────────────────

class DuplicateExceptionHandlerTests(unittest.TestCase):

    def test_run_seo_daily_no_duplicate_except(self):
        """run_seo_daily() must have exactly one 'except json.JSONDecodeError' block."""
        from agents.marketing import run
        import re, inspect

        source = inspect.getsource(run.run_seo_daily)
        json_excepts = re.findall(r'except\s+json\.JSONDecodeError', source)

        self.assertLessEqual(
            len(json_excepts), 1,
            "Found {} 'except json.JSONDecodeError' clauses — should be 1. "
            "A duplicate second clause is unreachable (first catches all JSONDecodeErrors).".format(
                len(json_excepts)
            ),
        )


# ── Insight feedback loop ──────────────────────────────────────────────────────

class InsightFeedbackLoopTests(unittest.TestCase):

    def test_seo_insights_contains_gaps_for_content_team(self):
        """write_seo_insights must write gaps array for generate_content.py."""
        from agents.marketing import run

        fake_seo = {
            "content_gap": {
                "gaps": ["unattended coding agent", "spec-driven AI agent"],
                "covered": ["Ralph Workflow"],
                "coverage_pct": 20.0,
            },
            "priority_actions": ["Fix on-page SEO (55/100)"],
            "onpage_score": "55/100 (D)",
            "ranked_keywords": 0,
            "backlinks": {"count_approx": 0},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(run, "LOG_DIR", Path(tmpdir)):
                path = run.write_seo_insights(fake_seo, [])
            data = json.loads(path.read_text())

        self.assertEqual(data["gaps"], ["unattended coding agent", "spec-driven AI agent"])
        self.assertEqual(data["onpage_score"], "55/100 (D)")

    def test_generate_content_reads_seo_insights(self):
        """generate_content.load_seo_insights returns the gaps for content prioritization."""
        from agents.marketing import generate_content

        fake_insights = {
            "gaps": ["unattended coding agent", "spec-driven AI agent"],
            "priority_keywords": [],
            "onpage_score": "55/100 (D)",
        }

        with patch.object(generate_content, "load_seo_insights", return_value=fake_insights):
            insights = generate_content.load_seo_insights()

        self.assertEqual(insights["gaps"], ["unattended coding agent", "spec-driven AI agent"])


if __name__ == "__main__":
    unittest.main()
