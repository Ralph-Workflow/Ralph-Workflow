import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.marketing import distribution_lane_executor as executor


class DistributionLaneExecutorContactSuggestionTests(unittest.TestCase):
    def test_prefers_website_over_weak_role_email(self):
        suggestion = executor._contact_channel_suggestion([
            {'type': 'website', 'value': 'https://www.nxcode.io/ar/contact', 'label': 'contact page'},
            {'type': 'email', 'value': 'legal@nxcode.io', 'label': 'email'},
        ])

        self.assertEqual(
            suggestion,
            'Use the site contact path first: https://www.nxcode.io/ar/contact',
        )

    def test_uses_real_email_when_available(self):
        suggestion = executor._contact_channel_suggestion([
            {'type': 'email', 'value': 'timewell@timewell.jp', 'label': 'email'},
            {'type': 'website', 'value': 'https://timewell.jp/en/contact', 'label': 'contact page'},
        ])

        self.assertEqual(suggestion, 'Email first: timewell@timewell.jp')

    def test_recent_action_payloads_accepts_timezone_aware_now(self):
        with TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / 'marketing_recent.json').write_text(
                '{\n'
                '  "timestamp": "2026-05-25T00:00:00+02:00",\n'
                '  "type": "primary_repo_flat_contact_manual_delivery",\n'
                '  "status": "executed",\n'
                '  "ok": true\n'
                '}\n',
                encoding='utf-8',
            )

            original = executor.LOG_DIR
            executor.LOG_DIR = log_dir
            try:
                payloads = executor._recent_action_payloads(
                    action_types={'primary_repo_flat_contact_manual_delivery'},
                    now=datetime(2026, 5, 25, 1, 0, tzinfo=UTC),
                    days=7,
                )
            finally:
                executor.LOG_DIR = original

        self.assertEqual(len(payloads), 1)


if __name__ == '__main__':
    unittest.main()
