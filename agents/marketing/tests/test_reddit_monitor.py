import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.marketing import marketing_momentum_watchdog, reddit_monitor
from agents.marketing.reddit_monitor import Candidate, SearchAttempt


class RedditMonitorTests(unittest.TestCase):
    def test_parse_duckduckgo_lite_results_extracts_reddit_comment(self):
        html = '''
        <table>
          <tr>
            <td>1.&nbsp;</td>
            <td>
              <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.reddit.com%2Fr%2FClaudeCode%2Fcomments%2Fabc123%2Fexample%2F" class='result-link'>Claude Code workflow thread</a>
            </td>
          </tr>
          <tr>
            <td>&nbsp;&nbsp;&nbsp;</td>
            <td class='result-snippet'>Readable diff, review bundle, and overnight run safety.</td>
          </tr>
        </table>
        '''
        results = reddit_monitor.parse_duckduckgo_lite_results(html, 'unattended', 'overnight Claude Code reddit')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].community, 'r/ClaudeCode')
        self.assertIn('Readable diff', results[0].snippet)

    def test_parse_brave_results_extracts_unique_reddit_comment(self):
        html = '''
        <a href="https://www.reddit.com/r/ClaudeAI/comments/1loxsxz/claude_code_approval_mode_no_more_chaosengineering/" target="_self">Reddit reddit.com › r/claudeai › claude code approval mode - no more chaos-engineering on Reddit: Claude Code Approval Mode - No more Chaos-Engineering</a>
        <a href="https://www.reddit.com/r/ClaudeAI/comments/1loxsxz/claude_code_approval_mode_no_more_chaosengineering/" target="_self">Top answer 1 of 5 something</a>
        '''
        results = reddit_monitor.parse_brave_results(html, 'approval_drag', 'Claude Code approval reddit')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].community, 'r/ClaudeAI')
        self.assertIn('Claude Code Approval Mode', results[0].snippet)

    def test_fresh_report_reuse_payload_accepts_recent_healthy_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            search_dir = Path(tmpdir)
            latest = search_dir / 'reddit_monitor_latest.md'
            latest.write_text(
                '# Reddit monitor\n\n'
                '- **Threads/posts scanned:** 12\n'
                '- **Shortlisted:** 2\n'
                '- **Query attempts:** 24\n'
                '- **Search diagnostics:** ok=3, fetch_error=21\n',
                encoding='utf-8',
            )
            with patch.object(reddit_monitor, 'SEARCH_DIR', search_dir):
                payload = reddit_monitor._fresh_report_reuse_payload()
            self.assertIsNotNone(payload)
            self.assertEqual(payload['status'], 'fresh_report_reused')
            self.assertEqual(payload['scanned'], 12)
            self.assertEqual(payload['shortlisted'], 2)

    def test_fresh_report_reuse_payload_rejects_partial_visibility_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            search_dir = Path(tmpdir)
            latest = search_dir / 'reddit_monitor_latest.md'
            latest.write_text(
                '# Reddit monitor\n\n'
                '- **Threads/posts scanned:** 12\n'
                '- **Shortlisted:** 4\n'
                '- **Query attempts:** 24\n'
                '- **Important telemetry note**: some Reddit queries were blocked (**reddit_ip_blocked=3**), but other queries still returned usable results (**ok=4**). Treat this as partial coverage, not a total Reddit outage.\n',
                encoding='utf-8',
            )
            with patch.object(reddit_monitor, 'SEARCH_DIR', search_dir):
                payload = reddit_monitor._fresh_report_reuse_payload()
            self.assertIsNone(payload)

    def test_force_refresh_requested_accepts_cli_flag_and_env(self):
        self.assertTrue(reddit_monitor._force_refresh_requested(['--force-refresh']))
        with patch.dict('os.environ', {'RALPH_MARKETING_FORCE_REFRESH': '1'}, clear=False):
            self.assertTrue(reddit_monitor._force_refresh_requested([]))

    def test_main_force_refresh_bypasses_cooldown_and_cache_reuse(self):
        with patch.object(reddit_monitor, 'load_market_intelligence', return_value={}), \
             patch.object(reddit_monitor, '_is_globally_cooled_down', return_value=True), \
             patch.object(reddit_monitor, '_fresh_report_reuse_payload', return_value={'status': 'fresh_report_reused'}), \
             patch.object(reddit_monitor, 'collect_candidates', return_value=([], [])), \
             patch.object(reddit_monitor, 'shortlist', return_value=([], [])), \
             patch.object(reddit_monitor, 'render_report', return_value='# report\n'), \
             patch('builtins.print') as mock_print:
            with tempfile.TemporaryDirectory() as tmpdir:
                search_dir = Path(tmpdir)
                with patch.object(reddit_monitor, 'SEARCH_DIR', search_dir):
                    rc = reddit_monitor.main(['--force-refresh'])
        self.assertEqual(rc, 1)
        printed = '\n'.join(call.args[0] for call in mock_print.call_args_list if call.args)
        self.assertIn('search_provider_degraded', printed)
        self.assertNotIn('cooldown_skip', printed)
        self.assertNotIn('fresh_report_reused', printed)

    def test_score_candidate_rejects_non_software_tax_threads(self):
        score, _reason, direct_reply_fit, mention_fit = reddit_monitor.score_candidate(
            'AI to review tax returns?',
            'Would this help accountants review tax returns faster for the IRS?',
            'r/Accounting',
            'review_tax',
        )
        self.assertLess(score, 0)
        self.assertEqual(direct_reply_fit, 'low')
        self.assertEqual(mention_fit, 'low')

    def test_render_report_marks_mixed_reddit_blocking_as_partial_coverage(self):
        shortlisted = [
            Candidate(
                title='Example thread',
                url='https://www.reddit.com/r/ClaudeCode/comments/abc123/example/',
                community='r/ClaudeCode',
                snippet='reviewable result',
                query_family='review_tax',
                query='ready to review coding agent reddit',
                score=7,
                freshness='during this pass',
                mention_fit='low',
                reason='content-family match: review_tax',
                direct_reply_fit='medium',
            )
        ]
        report = reddit_monitor.render_report(
            shortlisted,
            [],
            [
                SearchAttempt('review_tax', 'query one', 'ok', 4),
                SearchAttempt('review_tax', 'query two', 'reddit_ip_blocked', 0),
            ],
        )
        self.assertIn('credible discussion opportunities', report)
        self.assertIn('partial coverage, not a total Reddit outage', report)
        self.assertNotIn('all Reddit API calls returned HTTP 403 on this pass', report)

    def test_collect_candidates_stops_when_time_budget_is_exceeded(self):
        time_points = iter([0.0, 0.0, 46.0])
        with patch.object(reddit_monitor, 'CONTENT_QUERY_FAMILIES', [('review_tax', ['query one', 'query two'])]), \
             patch.object(reddit_monitor, 'load_recent_post_urls', return_value=set()), \
             patch.object(reddit_monitor, 'search_query', return_value=([], 'ok')):
            candidates, attempts = reddit_monitor.collect_candidates(
                time_budget_seconds=45.0,
                time_source=lambda: next(time_points),
            )
        self.assertEqual(candidates, [])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0].status, 'ok')
        self.assertEqual(attempts[1].status, 'time_budget_exceeded')

    def test_shortlist_diversifies_query_families_before_filling_duplicates(self):
        candidates = [
            Candidate(
                title=f'Production thread {i}',
                url=f'https://www.reddit.com/r/AI_Agents/comments/prod{i}/example/',
                community='r/AI_Agents',
                snippet='production continuity and ready to review',
                query_family='production_failure',
                query='workflow continuity ai agents reddit',
                score=20 - i,
                freshness='during this pass',
                mention_fit='medium',
                reason='content-family match: production_failure',
                direct_reply_fit='high',
            )
            for i in range(4)
        ] + [
            Candidate(
                title='Approval thread',
                url='https://www.reddit.com/r/ClaudeCode/comments/approval/example/',
                community='r/ClaudeCode',
                snippet='approval loop and babysitting',
                query_family='approval_drag',
                query='approval loop coding agent reddit',
                score=17,
                freshness='during this pass',
                mention_fit='medium',
                reason='content-family match: approval_drag',
                direct_reply_fit='medium-high',
            ),
            Candidate(
                title='Unattended thread',
                url='https://www.reddit.com/r/programming/comments/unattended/example/',
                community='r/programming',
                snippet='run overnight and close the laptop',
                query_family='unattended',
                query='run overnight Claude Code reddit',
                score=16,
                freshness='during this pass',
                mention_fit='medium',
                reason='content-family match: unattended',
                direct_reply_fit='medium-high',
            ),
        ]

        shortlisted, _rejected = reddit_monitor.shortlist(candidates)

        families = [candidate.query_family for candidate in shortlisted]
        self.assertIn('approval_drag', families)
        self.assertIn('unattended', families)
        self.assertLessEqual(families.count('production_failure'), 2)

    def test_shortlist_collapses_mirrored_pr_evidence_threads(self):
        candidates = [
            Candidate(
                title='What do you actually look for in the first 60 seconds of a PR review? (Specifically for AI-generated PRs)',
                url='https://www.reddit.com/r/AI_Agents/comments/a1/example/',
                community='r/AI_Agents',
                snippet='finished code, tested code, touched surfaces, unresolved decisions',
                query_family='review_tax',
                query='ready to review coding agent merge PR reddit',
                score=18,
                freshness='during this pass',
                mention_fit='medium',
                reason='review-tax evidence surface',
                direct_reply_fit='high',
            ),
            Candidate(
                title='If an AI agent opened a PR for you, what would you want to see first?',
                url='https://www.reddit.com/r/cursor/comments/b2/example/',
                community='r/cursor',
                snippet='AI-generated PR evidence and reviewer mental load',
                query_family='review_tax',
                query='AI written code review delay PR agent reddit',
                score=17,
                freshness='during this pass',
                mention_fit='medium-low',
                reason='review-tax mirrored prompt',
                direct_reply_fit='medium-high',
            ),
        ]

        shortlisted, rejected = reddit_monitor.shortlist(candidates)

        self.assertEqual(len(shortlisted), 1)
        self.assertEqual(len(rejected), 1)



    def test_watchdog_skips_partial_visibility_report_when_looking_for_healthy_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seo_dir = Path(tmpdir)
            partial = seo_dir / 'reddit_monitor_latest_healthy.md'
            partial.write_text(
                '# Reddit monitor\n\n'
                '- **Important telemetry note**: some Reddit queries were blocked (**reddit_ip_blocked=3**), but other queries still returned usable results (**ok=4**). Treat this as partial coverage, not a total Reddit outage.\n',
                encoding='utf-8',
            )
            real = seo_dir / 'reddit_monitor_2026-05-23_1200.md'
            real.write_text('# Reddit monitor\n\n- **Shortlisted:** 2\n', encoding='utf-8')
            with patch.object(marketing_momentum_watchdog, 'SEO', seo_dir):
                report, _age = marketing_momentum_watchdog.newest_healthy_report_time(marketing_momentum_watchdog.datetime.now().astimezone())
            self.assertEqual(report, real)


if __name__ == '__main__':
    unittest.main()
