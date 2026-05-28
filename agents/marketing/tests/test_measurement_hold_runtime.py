import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agents.marketing import measurement_hold_runtime


class MeasurementHoldRuntimeTests(unittest.TestCase):
    def test_resolve_hold_until_uses_next_truthful_checkpoint_from_execution_board_status(self):
        hold_started_at = datetime(2026, 5, 28, 5, 19, 16)
        hold_payload = {
            'timestamp': '2026-05-28T05:19:16',
            'chosen_action': {'type': 'measurement_hold_execution'},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / 'distribution_lane_latest.json').write_text(json.dumps({
                'short_review_window_release_at': '2026-05-28T09:12:15',
            }), encoding='utf-8')
            (log_dir / 'outcome_execution_board_latest.json').write_text(json.dumps({
                'next_truthful_checkpoint': {
                    'at': '2026-06-01T23:11:13',
                    'source': 'apollo_review_window',
                    'reason': 'Apollo launch/reply measurement window reaches its next review checkpoint.',
                }
            }), encoding='utf-8')

            hold_until = measurement_hold_runtime._resolve_hold_until(
                hold_payload=hold_payload,
                hold_started_at=hold_started_at,
                log_dir=log_dir,
                payloads=[],
            )

        self.assertEqual(hold_until, datetime(2026, 6, 1, 23, 11, 13))


if __name__ == '__main__':
    unittest.main()
