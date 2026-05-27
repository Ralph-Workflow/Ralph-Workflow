import unittest
from unittest.mock import patch

from agents.marketing import distribution_lane_selector


class DirectoryReviewWindowDueTests(unittest.TestCase):
    def test_submission_note_anchor_dt_parses_submitted_date(self):
        dt = distribution_lane_selector._submission_note_anchor_dt(
            'Submitted 2026-05-23 via Bubble form; success screen rendered; pending review.'
        )
        self.assertIsNotNone(dt)
        self.assertEqual(dt.isoformat(timespec='seconds'), '2026-05-23T00:00:00')

    def test_pending_directory_review_rows_due_uses_review_window_thresholds(self):
        now = distribution_lane_selector._parse_dt('2026-05-27T04:34:00')
        payload = {
            'directories': {
                'VBWebTools': {
                    'listing_live': False,
                    'listing_url': 'https://www.vbwebtools.com/tools/ralph-workflow',
                    'status_note': 'Submitted 2026-05-23 via WordPress AJAX endpoint; HTTP 200; pending 2-3 day review.',
                },
                'Claudetory': {
                    'listing_live': False,
                    'listing_url': 'https://claudetory.com/tools/ralph-workflow',
                    'status_note': 'Submitted 2026-05-24 via the public /api/submit-resource endpoint; treat as pending review until the public tool page resolves.',
                },
            }
        }

        due_rows = distribution_lane_selector._pending_directory_review_rows_due(now, payload)
        self.assertEqual([row['name'] for row in due_rows], ['VBWebTools'])

    def test_directory_confirmation_due_without_recent_burst_when_review_windows_mature(self):
        now = distribution_lane_selector._parse_dt('2026-05-27T04:34:00')
        payload = {
            'directories': {
                'VBWebTools': {
                    'listing_live': False,
                    'listing_url': 'https://www.vbwebtools.com/tools/ralph-workflow',
                    'status_note': 'Submitted 2026-05-23 via WordPress AJAX endpoint; HTTP 200; pending 2-3 day review.',
                }
            }
        }
        with patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False), patch.object(
            distribution_lane_selector,
            '_backlink_status_snapshot',
            return_value={'payload': payload, 'age_hours': 12.0, 'live_listings': 3},
        ):
            self.assertTrue(distribution_lane_selector._directory_confirmation_due(now, recent_directory_submissions=0))


if __name__ == '__main__':
    unittest.main()
