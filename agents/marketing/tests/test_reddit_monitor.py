import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.marketing import reddit_monitor


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


if __name__ == '__main__':
    unittest.main()
