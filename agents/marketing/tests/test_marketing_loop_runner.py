import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.marketing import marketing_loop_runner


class MarketingLoopRunnerTests(unittest.TestCase):
    def test_audit_requires_post_audit_runtime_only_for_needs_execution_system_design_repairs(self):
        self.assertTrue(marketing_loop_runner._audit_requires_post_audit_runtime({
            'repair_window_status': 'needs_repair',
            'repair_actions': [
                {
                    'repair_kind': 'system_design',
                    'repair_state': 'needs_execution',
                }
            ],
        }))
        self.assertFalse(marketing_loop_runner._audit_requires_post_audit_runtime({
            'repair_window_status': 'measurement_pending',
            'repair_actions': [
                {
                    'repair_kind': 'system_design',
                    'repair_state': 'pending_measurement',
                }
            ],
        }))
        self.assertFalse(marketing_loop_runner._audit_requires_post_audit_runtime({
            'repair_window_status': 'needs_repair',
            'repair_actions': [
                {
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                }
            ],
        }))

    def test_load_audit_payload_falls_back_to_latest_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / 'marketing_workflow_audit_latest.json'
            audit_path.write_text(json.dumps({'repair_window_status': 'needs_repair'}), encoding='utf-8')
            with patch.object(marketing_loop_runner, 'AUDIT_LATEST', audit_path):
                payload = marketing_loop_runner._load_audit_payload('not-json')
        self.assertEqual(payload['repair_window_status'], 'needs_repair')

    def test_main_triggers_post_audit_runtimes_when_audit_opens_system_design_repair(self):
        audit_stdout = json.dumps({
            'repair_window_status': 'needs_repair',
            'repair_actions': [
                {
                    'repair_kind': 'system_design',
                    'repair_state': 'needs_execution',
                }
            ],
        })

        def fake_run(cmd, capture_output=True, text=True):
            script_name = Path(cmd[-1]).name
            stdout = '{}'
            if script_name == 'marketing_workflow_audit.py':
                stdout = audit_stdout
            return type('Proc', (), {
                'returncode': 0,
                'stdout': stdout,
                'stderr': '',
            })()

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / 'marketing_loop_runner_latest.json'
            with patch.object(marketing_loop_runner, 'OUT', out_path), \
                 patch.object(marketing_loop_runner.subprocess, 'run', side_effect=fake_run):
                rc = marketing_loop_runner.main()

            self.assertEqual(rc, 0)
            payload = json.loads(out_path.read_text(encoding='utf-8'))
            scripts = [Path(entry['script']).name for entry in payload['results']]
            self.assertIn('outcome_capability_runner.py', scripts)
            self.assertIn('outcome_execution_board_runner.py', scripts)
            post_entries = [entry for entry in payload['results'] if entry.get('triggered_by') == 'post_audit_system_design_repair']
            self.assertEqual(len(post_entries), 2)

    def test_main_skips_post_audit_runtimes_when_audit_is_measurement_pending(self):
        audit_stdout = json.dumps({
            'repair_window_status': 'measurement_pending',
            'repair_actions': [
                {
                    'repair_kind': 'system_design',
                    'repair_state': 'pending_measurement',
                }
            ],
        })

        def fake_run(cmd, capture_output=True, text=True):
            script_name = Path(cmd[-1]).name
            stdout = audit_stdout if script_name == 'marketing_workflow_audit.py' else '{}'
            return type('Proc', (), {
                'returncode': 0,
                'stdout': stdout,
                'stderr': '',
            })()

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / 'marketing_loop_runner_latest.json'
            with patch.object(marketing_loop_runner, 'OUT', out_path), \
                 patch.object(marketing_loop_runner.subprocess, 'run', side_effect=fake_run):
                rc = marketing_loop_runner.main()

            self.assertEqual(rc, 0)
            payload = json.loads(out_path.read_text(encoding='utf-8'))
            scripts = [Path(entry['script']).name for entry in payload['results']]
            self.assertNotIn('outcome_capability_runner.py', scripts)
            self.assertNotIn('outcome_execution_board_runner.py', scripts)


if __name__ == '__main__':
    unittest.main()
