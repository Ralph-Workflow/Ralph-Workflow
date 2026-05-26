import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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

    def test_prefers_feedback_form_over_generic_homepage(self):
        suggestion = executor._contact_channel_suggestion([
            {'type': 'website', 'value': 'https://aisaying.net', 'label': 'website'},
            {'type': 'website', 'value': 'https://aisaying.net/knowledge/article/ai-coding-tools-comparison-matrix', 'label': 'feedback form'},
        ])

        self.assertEqual(
            suggestion,
            'Use the site contact path first: https://aisaying.net/knowledge/article/ai-coding-tools-comparison-matrix',
        )

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

    def test_current_primary_repo_flat_actionable_findings_skips_targets_already_in_active_manual_review(self):
        now = datetime(2026, 5, 26, 3, 51)
        with TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            payload = {
                'timestamp': '2026-05-26T03:40:00+02:00',
                'action_type': 'manual_outreach_asset_follow_through',
                'status': 'delivered_to_current_chat',
                'why_this_action': {
                    'targets_prepared': ['TIMEWELL'],
                },
                'result': {
                    'status': 'delivered_to_current_chat',
                },
            }
            (log_dir / 'marketing_active_manual_delivery.json').write_text(
                json.dumps(payload),
                encoding='utf-8',
            )

            original = executor.LOG_DIR
            executor.LOG_DIR = log_dir
            try:
                with mock.patch.object(executor, '_recent_contact_targets', return_value=set()):
                    with mock.patch.object(executor, '_publisher_target_has_manual_executable_channel', return_value=True):
                        with mock.patch.object(
                            executor,
                            '_load_primary_repo_flat_contact_discovery',
                            return_value=[
                                {'target': 'TIMEWELL', 'channels': [{'type': 'website', 'value': 'https://timewell.jp/en/contact'}]},
                                {'target': 'AI Saying', 'channels': [{'type': 'website', 'value': 'https://aisaying.net'}]},
                            ],
                        ):
                            findings = executor._current_primary_repo_flat_actionable_findings(now)
            finally:
                executor.LOG_DIR = original

        self.assertEqual([row['target'] for row in findings], ['AI Saying'])


if __name__ == '__main__':
    unittest.main()
