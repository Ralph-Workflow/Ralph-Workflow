from unittest import TestCase

from agents.marketing.distribution_lane_executor import OWNED_CONTENT_SOURCE_CANDIDATES


class OwnedContentPriorityTests(TestCase):
    def test_good_unattended_task_is_first_owned_content_candidate(self):
        self.assertTrue(OWNED_CONTENT_SOURCE_CANDIDATES)
        self.assertEqual(
            OWNED_CONTENT_SOURCE_CANDIDATES[0].name,
            'good_unattended_task.md',
        )
