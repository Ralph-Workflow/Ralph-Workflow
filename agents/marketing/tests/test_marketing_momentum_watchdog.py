import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agents.marketing import marketing_momentum_watchdog as watchdog


class MarketingMomentumWatchdogTests(unittest.TestCase):
    def test_active_measurement_hold_suppresses_stale_and_pending_repair_actions(self):
        now = datetime.now().astimezone()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_dir = root / 'logs'
            status_dir.mkdir(parents=True, exist_ok=True)
            report = root / 'reddit_monitor_latest.md'
            report.write_text('**Shortlisted:** 0\n', encoding='utf-8')
            old = now.timestamp() - 7 * 3600
            report.chmod(0o644)
            import os
            os.utime(report, (old, old))

            (status_dir / 'adoption_metrics_latest.json').write_text(json.dumps({
                'evaluation': {'failing_signals': ['primary_repo_flat']}
            }), encoding='utf-8')
            (status_dir / 'marketing_workflow_audit_latest.json').write_text(json.dumps({
                'repair_window_status': 'needs_repair',
                'measurement_pending_reasons': ['same_family_distribution_overlap', 'same_family_outreach_overlap'],
                'has_failing_tactics': True,
                'failing_tactics': ['primary_repo_flat_window'],
                'repair_actions': [
                    {'failure_type': 'primary_repo_flat', 'repair_state': 'needs_execution'},
                    {'failure_type': 'same_family_distribution_overlap', 'repair_state': 'pending_measurement'},
                ],
                'latest_executed_action': {},
            }), encoding='utf-8')
            (status_dir / 'apollo_status.json').write_text(json.dumps({
                'status': 'login_succeeded',
                'cloudflare_blocked': False,
            }), encoding='utf-8')

            hold_dir = root / 'marketing-logs'
            hold_dir.mkdir(parents=True, exist_ok=True)
            hold_started_at = (now - timedelta(minutes=30)).replace(microsecond=0)
            (hold_dir / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(json.dumps({
                'timestamp': hold_started_at.isoformat(),
                'chosen_action': {'type': 'measurement_hold_execution'},
                'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                'why_this_action': {'summary': 'measurement hold is active'},
            }), encoding='utf-8')

            with patch.object(watchdog, 'STATUS_DIR', status_dir), \
                 patch.object(watchdog, 'STATUS_PATH', status_dir / 'marketing_momentum_watchdog.json'), \
                 patch.object(watchdog, 'ADOPTION_PATH', status_dir / 'adoption_metrics_latest.json'), \
                 patch.object(watchdog, 'AUDIT_PATH', status_dir / 'marketing_workflow_audit_latest.json'), \
                 patch.object(watchdog, 'APOLLO_STATUS_PATH', status_dir / 'apollo_status.json'), \
                 patch.object(watchdog, 'REDDIT_EXECUTION_STATUS_PATH', status_dir / 'missing_reddit_execution_status.json'), \
                 patch.object(watchdog, 'RUNNER_PATH', status_dir / 'missing_runner.json'), \
                 patch.object(watchdog, 'ROOT', root), \
                 patch.object(watchdog, 'SEO', root), \
                 patch.object(watchdog, 'LOG_JSONL', root / 'missing_posts.jsonl'), \
                 patch.object(watchdog, 'RETRO', root / 'reddit_retrospective.py'), \
                 patch.object(watchdog.marketing_run, 'LOG_DIR', hold_dir), \
                 patch.object(watchdog, 'append_note', lambda text: None), \
                 patch.object(watchdog, 'newest_report', lambda: report), \
                 patch.object(watchdog, 'newest_post_time', lambda: now - timedelta(hours=1)), \
                 patch.object(watchdog, 'newest_healthy_report_time', lambda _now: (None, None)), \
                 patch.object(watchdog, 'latest_reddit_monitor_runtime', lambda _now: {'status': None, 'age_hours': None}), \
                 patch('agents.marketing.marketing_momentum_watchdog.subprocess.run'):
                rc = watchdog.main()

            self.assertEqual(rc, 0)
            summary = json.loads((status_dir / 'marketing_momentum_watchdog.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['actions'], [])
            self.assertIn('measurement_hold_active', summary['watch_actions'])
            self.assertIn('primary_repo_adoption_flat', summary['watch_actions'])
            self.assertTrue(summary['measurement_hold']['active'])

    def test_without_active_measurement_hold_stale_report_still_flags_attention(self):
        now = datetime.now().astimezone()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_dir = root / 'logs'
            status_dir.mkdir(parents=True, exist_ok=True)
            report = root / 'reddit_monitor_latest.md'
            report.write_text('**Shortlisted:** 0\n', encoding='utf-8')
            old = now.timestamp() - 7 * 3600
            import os
            os.utime(report, (old, old))

            (status_dir / 'adoption_metrics_latest.json').write_text(json.dumps({
                'evaluation': {'failing_signals': []}
            }), encoding='utf-8')
            (status_dir / 'marketing_workflow_audit_latest.json').write_text(json.dumps({
                'repair_window_status': 'clear',
                'measurement_pending_reasons': [],
                'has_failing_tactics': False,
                'failing_tactics': [],
                'repair_actions': [],
                'latest_executed_action': {},
            }), encoding='utf-8')
            (status_dir / 'apollo_status.json').write_text(json.dumps({
                'status': 'login_succeeded',
                'cloudflare_blocked': False,
            }), encoding='utf-8')

            hold_dir = root / 'marketing-logs'
            hold_dir.mkdir(parents=True, exist_ok=True)

            with patch.object(watchdog, 'STATUS_DIR', status_dir), \
                 patch.object(watchdog, 'STATUS_PATH', status_dir / 'marketing_momentum_watchdog.json'), \
                 patch.object(watchdog, 'ADOPTION_PATH', status_dir / 'adoption_metrics_latest.json'), \
                 patch.object(watchdog, 'AUDIT_PATH', status_dir / 'marketing_workflow_audit_latest.json'), \
                 patch.object(watchdog, 'APOLLO_STATUS_PATH', status_dir / 'apollo_status.json'), \
                 patch.object(watchdog, 'REDDIT_EXECUTION_STATUS_PATH', status_dir / 'missing_reddit_execution_status.json'), \
                 patch.object(watchdog, 'RUNNER_PATH', status_dir / 'missing_runner.json'), \
                 patch.object(watchdog, 'SEO', root), \
                 patch.object(watchdog, 'LOG_JSONL', root / 'missing_posts.jsonl'), \
                 patch.object(watchdog, 'RETRO', root / 'reddit_retrospective.py'), \
                 patch.object(watchdog.marketing_run, 'LOG_DIR', hold_dir), \
                 patch.object(watchdog, 'append_note', lambda text: None), \
                 patch.object(watchdog, 'newest_report', lambda: report), \
                 patch.object(watchdog, 'newest_post_time', lambda: now - timedelta(hours=1)), \
                 patch.object(watchdog, 'newest_healthy_report_time', lambda _now: (None, None)), \
                 patch.object(watchdog, 'latest_reddit_monitor_runtime', lambda _now: {'status': None, 'age_hours': None}), \
                 patch('agents.marketing.marketing_momentum_watchdog.subprocess.run'):
                rc = watchdog.main()

            self.assertEqual(rc, 1)
            summary = json.loads((status_dir / 'marketing_momentum_watchdog.json').read_text(encoding='utf-8'))
            self.assertIn('reddit_monitor_stale', summary['actions'])
            self.assertFalse(summary['measurement_hold']['active'])

    def test_recent_execution_block_overrides_opportunity_report(self):
        now = datetime.now().astimezone()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_dir = root / 'logs'
            status_dir.mkdir(parents=True, exist_ok=True)
            report = root / 'reddit_monitor_latest.md'
            report.write_text('**Shortlisted:** 2\n', encoding='utf-8')

            (status_dir / 'adoption_metrics_latest.json').write_text(json.dumps({
                'evaluation': {'failing_signals': []}
            }), encoding='utf-8')
            (status_dir / 'marketing_workflow_audit_latest.json').write_text(json.dumps({
                'repair_window_status': 'clear',
                'measurement_pending_reasons': [],
                'has_failing_tactics': False,
                'failing_tactics': [],
                'repair_actions': [],
                'latest_executed_action': {},
            }), encoding='utf-8')
            (status_dir / 'apollo_status.json').write_text(json.dumps({
                'status': 'login_succeeded',
                'cloudflare_blocked': False,
            }), encoding='utf-8')
            (status_dir / 'reddit_execution_status_latest.json').write_text(json.dumps({
                'generated_at': now.isoformat(),
                'status': 'network_security_blocked',
                'blocking_reason': 'browserless_reddit_block_page',
            }), encoding='utf-8')

            hold_dir = root / 'marketing-logs'
            hold_dir.mkdir(parents=True, exist_ok=True)

            with patch.object(watchdog, 'STATUS_DIR', status_dir), \
                 patch.object(watchdog, 'STATUS_PATH', status_dir / 'marketing_momentum_watchdog.json'), \
                 patch.object(watchdog, 'ADOPTION_PATH', status_dir / 'adoption_metrics_latest.json'), \
                 patch.object(watchdog, 'AUDIT_PATH', status_dir / 'marketing_workflow_audit_latest.json'), \
                 patch.object(watchdog, 'APOLLO_STATUS_PATH', status_dir / 'apollo_status.json'), \
                 patch.object(watchdog, 'REDDIT_EXECUTION_STATUS_PATH', status_dir / 'reddit_execution_status_latest.json'), \
                 patch.object(watchdog, 'RUNNER_PATH', status_dir / 'missing_runner.json'), \
                 patch.object(watchdog, 'SEO', root), \
                 patch.object(watchdog, 'LOG_JSONL', root / 'missing_posts.jsonl'), \
                 patch.object(watchdog, 'RETRO', root / 'reddit_retrospective.py'), \
                 patch.object(watchdog.marketing_run, 'LOG_DIR', hold_dir), \
                 patch.object(watchdog, 'append_note', lambda text: None), \
                 patch.object(watchdog, 'newest_report', lambda: report), \
                 patch.object(watchdog, 'newest_post_time', lambda: now - timedelta(hours=1)), \
                 patch.object(watchdog, 'newest_healthy_report_time', lambda _now: (None, None)), \
                 patch.object(watchdog, 'latest_reddit_monitor_runtime', lambda _now: {'status': None, 'age_hours': None}), \
                 patch('agents.marketing.marketing_momentum_watchdog.subprocess.run'):
                rc = watchdog.main()

            self.assertEqual(rc, 1)
            summary = json.loads((status_dir / 'marketing_momentum_watchdog.json').read_text(encoding='utf-8'))
            self.assertIn('reddit_channel_blocked', summary['actions'])
            self.assertEqual(summary['reddit_execution_status']['status'], 'network_security_blocked')


if __name__ == '__main__':
    unittest.main()
