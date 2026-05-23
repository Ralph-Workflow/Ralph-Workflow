import unittest
from datetime import datetime
from types import SimpleNamespace

from agents.marketing import run
from agents.marketing.distribution_lane_selector import LaneDecision


class RunRepairModeTests(unittest.TestCase):
    def test_primary_repo_flat_does_not_advance_on_handoff_only_execution(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': ['primary_repo_flat'],
            'repair_actions': [
                {
                    'failure_type': 'primary_repo_flat',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                }
            ],
        }
        execution = SimpleNamespace(action_type='curator_handoff_packet_execution', live_external_action=False)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertFalse(changed)
        self.assertEqual(audit['repair_actions'][0]['repair_state'], 'needs_execution')
        self.assertEqual(audit['repair_window_status'], 'needs_repair')

    def test_primary_repo_flat_advances_on_live_external_execution(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': ['primary_repo_flat'],
            'repair_actions': [
                {
                    'failure_type': 'primary_repo_flat',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                }
            ],
        }
        execution = SimpleNamespace(action_type='directory_submission_execution', live_external_action=True)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertTrue(changed)
        self.assertEqual(audit['repair_actions'][0]['repair_state'], 'pending_measurement')
        self.assertEqual(audit['repair_window_status'], 'measurement_pending')

    def test_same_family_pauses_advance_when_other_lane_runs(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': [],
            'repair_actions': [
                {
                    'failure_type': 'same_family_distribution_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                },
                {
                    'failure_type': 'same_family_outreach_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                },
            ],
        }
        execution = SimpleNamespace(action_type='stackoverflow_answer_execution', live_external_action=False)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertTrue(changed)
        self.assertEqual(
            [repair['repair_state'] for repair in audit['repair_actions']],
            ['pending_measurement', 'pending_measurement'],
        )
        self.assertEqual(audit['repair_window_status'], 'clear')

    def test_same_family_outreach_advances_on_manual_contact_packet_execution(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': [],
            'repair_actions': [
                {
                    'failure_type': 'same_family_outreach_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                },
            ],
        }
        execution = SimpleNamespace(action_type='curator_contact_handoff_packet_execution', live_external_action=False)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertTrue(changed)
        self.assertEqual(audit['repair_actions'][0]['repair_state'], 'pending_measurement')
        self.assertEqual(audit['repair_window_status'], 'clear')

    def test_load_active_pending_repairs_keeps_measurement_window_repairs_live(self):
        audit = {
            'repair_window_status': 'measurement_pending',
            'measurement_pending_reasons': ['primary_repo_flat'],
            'repair_actions': [
                {
                    'failure_type': 'primary_repo_flat',
                    'repair_kind': 'tactic',
                    'repair_state': 'pending_measurement',
                },
                {
                    'failure_type': 'same_family_distribution_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'pending_measurement',
                },
            ],
        }

        repairs = run._load_active_pending_repairs(audit)

        self.assertEqual(len(repairs), 2)
        self.assertEqual({repair['failure_type'] for repair in repairs}, {'primary_repo_flat', 'same_family_distribution_overlap'})

    def test_apply_repair_mode_overrides_keeps_distribution_reset_choice_during_measurement_window(self):
        decision = LaneDecision(
            lane='distribution_reset',
            reason='base reason',
            reasons=['base reason'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/distribution_action_brief.md',
        )
        pending_repairs = [
            {
                'failure_type': 'primary_repo_flat',
                'repair_kind': 'tactic',
                'repair_state': 'pending_measurement',
                'action': 'keep pushing conversion evidence while measurement is pending',
            },
            {
                'failure_type': 'same_family_outreach_overlap',
                'repair_kind': 'tactic',
                'repair_state': 'pending_measurement',
            },
        ]

        updated = run._apply_repair_mode_overrides(decision, pending_repairs)

        self.assertEqual(updated.lane, 'distribution_reset')
        self.assertTrue(updated.skip_curator_outreach)
        self.assertEqual(updated.reason, 'base reason')

    def test_apply_repair_mode_overrides_keeps_distribution_reset_choice_during_needs_execution_window(self):
        decision = LaneDecision(
            lane='distribution_reset',
            reason='base reason',
            reasons=['base reason'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/distribution_action_brief.md',
        )
        pending_repairs = [
            {
                'failure_type': 'primary_repo_flat',
                'repair_kind': 'tactic',
                'repair_state': 'needs_execution',
                'action': 'replace stale distribution with conversion-oriented work',
            },
        ]

        updated = run._apply_repair_mode_overrides(decision, pending_repairs)

        self.assertEqual(updated.lane, 'distribution_reset')
        self.assertEqual(updated.reason, 'base reason')


if __name__ == '__main__':
    unittest.main()
