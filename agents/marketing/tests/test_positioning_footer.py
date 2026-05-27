from unittest import TestCase

from agents.marketing.positioning import (
    CODEBERG_PRIMARY,
    FIRST_TASK_GUIDE,
    GITHUB_MIRROR,
    START_HERE_GUIDE,
    repo_cta_footer,
)


class PositioningFooterTests(TestCase):
    def test_repo_cta_footer_points_to_codeberg_first_task_and_start_here(self):
        footer = repo_cta_footer()
        self.assertIn(CODEBERG_PRIMARY, footer)
        self.assertIn(FIRST_TASK_GUIDE, footer)
        self.assertIn(START_HERE_GUIDE, footer)
        self.assertIn(GITHUB_MIRROR, footer)
        self.assertIn('Best evaluator path', footer)
        self.assertIn('would you merge this?', footer)
