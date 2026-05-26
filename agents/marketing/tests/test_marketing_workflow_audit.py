import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from agents.marketing import marketing_workflow_audit


class MarketingWorkflowAuditTests(unittest.TestCase):
    def test_action_outcome_ready_rejects_manual_approval_pending(self):
        ready, warning = marketing_workflow_audit.action_outcome_ready({
            'chosen_action': {'type': 'saashub_secondary_surface_comment_execution'},
            'result': {
                'status': 'submitted_and_email_confirmed_pending_manual_approval',
                'ok': True,
                'live_external_action': True,
                'manual_approval_pending': True,
            },
        })

        self.assertFalse(ready)
        self.assertIn('manual approval', warning)

    def test_main_writes_explicit_worked_not_worked_and_low_signal_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            logs = tmp / 'logs'
            logs.mkdir()
            outreach = tmp / 'outreach-log.md'
            outreach.write_text(
                '# Outreach Log\n\nWhat remains open (not a repair failure — a measurement window problem)\n',
                encoding='utf-8',
            )
            adoption = logs / 'adoption_metrics_latest.json'
            adoption.write_text(json.dumps({
                'metrics': [
                    {'platform': 'Codeberg', 'stars': 10, 'watchers': 2, 'forks': 2, 'open_issues': 5, 'html_url': 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'},
                    {'platform': 'GitHub', 'stars': 0, 'watchers': 2, 'forks': 0, 'open_issues': 0, 'html_url': 'https://github.com/Ralph-Workflow/Ralph-Workflow'},
                ],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {
                    'findings': ['Codeberg is flat.'],
                    'failing_signals': ['primary_repo_flat'],
                },
            }), encoding='utf-8')
            retro = logs / 'reddit_post_analysis.json'
            retro.write_text(json.dumps({
                'recent_posts': [{}],
                'repeated_openings': ['Honestly the part I\'d optimize first is the handoff, not the model stack.'],
            }), encoding='utf-8')
            reddit_monitor = tmp / 'reddit_monitor_latest.md'
            reddit_monitor.write_text('partial visibility only. fail closed.', encoding='utf-8')
            apollo = logs / 'apollo_sequence_status_latest.json'
            apollo.write_text(json.dumps({'measurement_pending': False}), encoding='utf-8')
            prior_audit = logs / 'marketing_workflow_audit_latest.json'
            prior_audit.write_text(json.dumps({
                'repair_actions': [
                    {
                        'target_tactic': 'content_distribution',
                        'failure_type': 'primary_repo_flat',
                        'repair_state': 'pending_measurement',
                        'repair_acknowledged_at': '2026-05-25T12:38:31.894824',
                    }
                ]
            }), encoding='utf-8')
            latest_action = logs / 'marketing_2026-05-25_saashub_secondary_surface_comment_execution.json'
            latest_action.write_text(json.dumps({
                'timestamp': '2026-05-25T21:19:37+02:00',
                'chosen_action': {
                    'type': 'saashub_secondary_surface_comment_execution',
                    'title': 'SaaSHub alternatives-page Codeberg routing correction comment',
                },
                'result': {
                    'status': 'submitted_and_email_confirmed_pending_manual_approval',
                    'ok': True,
                    'live_external_action': True,
                    'manual_approval_pending': True,
                    'blocking_factors': ['Manual moderation still required.'],
                },
            }), encoding='utf-8')

            audit_md = logs / 'marketing_workflow_audit_latest.md'
            with ExitStack() as stack:
                stack.enter_context(patch.object(marketing_workflow_audit, 'OUT_DIR', logs))
                stack.enter_context(patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md))
                stack.enter_context(patch.object(marketing_workflow_audit, 'AUDIT_JSON', prior_audit))
                stack.enter_context(patch.object(marketing_workflow_audit, 'OUTREACH', outreach))
                stack.enter_context(patch.object(marketing_workflow_audit, 'ADOPTION', adoption))
                stack.enter_context(patch.object(marketing_workflow_audit, 'RETRO', retro))
                stack.enter_context(patch.object(marketing_workflow_audit, 'APOLLO_SEQUENCE_STATUS', apollo))
                stack.enter_context(patch.object(marketing_workflow_audit, 'REDDIT_MONITOR_LATEST', reddit_monitor))
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(prior_audit.read_text(encoding='utf-8'))
            self.assertIn('worked', payload)
            self.assertIn('not_worked', payload)
            self.assertIn('repetitive', payload)
            self.assertIn('low_signal', payload)
            self.assertIn('should_change_now', payload)
            self.assertFalse(payload['latest_executed_action']['outcome_ready'])
            self.assertIn('manual approval', payload['latest_executed_action']['warning'])
            self.assertFalse(any('live external action artifact' in item for item in payload['worked']))
            self.assertTrue(any('measurement-pending' in item for item in payload['low_signal']))
            primary_repo_flat = next(item for item in payload['repair_actions'] if item['failure_type'] == 'primary_repo_flat')
            self.assertEqual(primary_repo_flat['repair_state'], 'pending_measurement')
            self.assertEqual(primary_repo_flat['repair_acknowledged_at'], '2026-05-25T12:38:31.894824')

            text = audit_md.read_text(encoding='utf-8')
            self.assertIn('## What actually worked', text)
            self.assertIn('## What did not work', text)
            self.assertIn('## What is repetitive', text)
            self.assertIn('## What is low-signal', text)
            self.assertIn('## What should change now', text)

    def test_outcome_capability_runner_satisfies_system_design_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            logs = tmp / 'logs'
            logs.mkdir()
            outreach = tmp / 'outreach-log.md'
            outreach.write_text('# Outreach Log\n', encoding='utf-8')
            adoption = logs / 'adoption_metrics_latest.json'
            adoption.write_text(json.dumps({
                'metrics': [
                    {'platform': 'Codeberg', 'stars': 10, 'watchers': 2, 'forks': 2, 'open_issues': 5, 'html_url': 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'},
                    {'platform': 'GitHub', 'stars': 0, 'watchers': 2, 'forks': 0, 'open_issues': 0, 'html_url': 'https://github.com/Ralph-Workflow/Ralph-Workflow'},
                ],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'findings': ['Codeberg is flat.'], 'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro = logs / 'reddit_post_analysis.json'
            retro.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
            apollo = logs / 'apollo_sequence_status_latest.json'
            apollo.write_text(json.dumps({'measurement_pending': False}), encoding='utf-8')
            apollo_runtime = logs / 'apollo_status_latest.json'
            apollo_runtime.write_text(json.dumps({'status': 'launch_ready', 'cloudflare_blocked': False}), encoding='utf-8')
            outcome_capability = logs / 'outcome_capability_latest.json'
            outcome_capability.write_text(json.dumps({
                'timestamp': '2026-05-25T23:27:19',
                'status': 'executed',
                'selected_lane': 'apollo_outreach',
                'codeberg_primary': 'https://codeberg.org/RalphWorkflow/Ralph-Workflow',
            }), encoding='utf-8')
            reddit_monitor = tmp / 'reddit_monitor_latest.md'
            reddit_monitor.write_text('reddit blocked', encoding='utf-8')
            prior_audit = logs / 'marketing_workflow_audit_latest.json'
            prior_audit.write_text(json.dumps({'repair_actions': []}), encoding='utf-8')
            latest_action = logs / 'marketing_2026-05-25_apollo_outreach_execution.json'
            latest_action.write_text(json.dumps({
                'timestamp': '2026-05-25T23:27:19+02:00',
                'chosen_action': {'type': 'apollo_outreach_execution', 'title': 'Apollo packet'},
                'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
            }), encoding='utf-8')

            with ExitStack() as stack:
                stack.enter_context(patch.object(marketing_workflow_audit, 'OUT_DIR', logs))
                stack.enter_context(patch.object(marketing_workflow_audit, 'AUDIT_MD', logs / 'marketing_workflow_audit_latest.md'))
                stack.enter_context(patch.object(marketing_workflow_audit, 'AUDIT_JSON', prior_audit))
                stack.enter_context(patch.object(marketing_workflow_audit, 'OUTREACH', outreach))
                stack.enter_context(patch.object(marketing_workflow_audit, 'ADOPTION', adoption))
                stack.enter_context(patch.object(marketing_workflow_audit, 'RETRO', retro))
                stack.enter_context(patch.object(marketing_workflow_audit, 'APOLLO_SEQUENCE_STATUS', apollo))
                stack.enter_context(patch.object(marketing_workflow_audit, 'APOLLO_STATUS', apollo_runtime))
                stack.enter_context(patch.object(marketing_workflow_audit, 'OUTCOME_CAPABILITY_STATUS', outcome_capability))
                stack.enter_context(patch.object(marketing_workflow_audit, 'REDDIT_MONITOR_LATEST', reddit_monitor))
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(prior_audit.read_text(encoding='utf-8'))
            self.assertNotIn('outcome_system_underpowered', [item['failure_type'] for item in payload['repair_actions']])


if __name__ == '__main__':
    unittest.main()
