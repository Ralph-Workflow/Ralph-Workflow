import tempfile
import unittest
from pathlib import Path

from agents.marketing.sync_research import SyncPlan, build_sync_plan, resolve_sync_paths


class FakeRunner:
    def __init__(self, diff_cached_returncode=0):
        self.calls = []
        self.diff_cached_returncode = diff_cached_returncode

    def run(self, args, check=True):
        self.calls.append(list(args))

        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode
                self.stdout = ""
                self.stderr = ""

        if args[:3] == ["diff", "--cached", "--quiet"]:
            return Result(returncode=self.diff_cached_returncode)
        return Result(returncode=0)


class SyncResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

        (self.workspace / "AGENTS.md").write_text("agents\n")
        (self.workspace / "TOOLS.md").write_text("tools\n")
        (self.workspace / "marketing").mkdir()
        (self.workspace / "marketing" / "note.md").write_text("note\n")
        (self.workspace / "seo-reports").mkdir()
        (self.workspace / "agents").mkdir()
        (self.workspace / "agents" / "marketing").mkdir(parents=True, exist_ok=True)
        (self.workspace / "agents" / "marketing" / "STRATEGY.md").write_text("strategy\n")
        (self.workspace / "agents" / "marketing" / "SKILLS_RESEARCH.md").write_text("skills\n")
        (self.workspace / "agents" / "seo").mkdir(parents=True, exist_ok=True)
        (self.workspace / "agents" / "seo" / "logs").mkdir()
        (self.workspace / "agents" / "seo" / "logs" / "cron.log").write_text("log\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_sync_paths_includes_existing_specs_and_globs(self):
        specs = [
            "AGENTS.md",
            "TOOLS.md",
            "marketing",
            "agents/marketing/STRATEGY.md",
            "agents/*/logs",
            "missing-dir",
        ]
        paths = resolve_sync_paths(self.workspace, specs)
        self.assertEqual(
            paths,
            [
                "AGENTS.md",
                "TOOLS.md",
                "marketing",
                "agents/marketing/STRATEGY.md",
                "agents/seo/logs",
            ],
        )

    def test_build_sync_plan_stages_paths_and_detects_changes(self):
        runner = FakeRunner(diff_cached_returncode=1)
        specs = ["AGENTS.md", "agents/*/logs"]
        plan = build_sync_plan(self.workspace, specs, "test commit", runner=runner)

        self.assertIsInstance(plan, SyncPlan)
        self.assertTrue(plan.has_changes)
        self.assertEqual(plan.commit_message, "test commit")
        self.assertIn(["add", "AGENTS.md", "agents/seo/logs"], runner.calls)
        self.assertIn(["diff", "--cached", "--quiet"], runner.calls)

    def test_build_sync_plan_reports_no_changes_when_diff_is_clean(self):
        runner = FakeRunner(diff_cached_returncode=0)
        plan = build_sync_plan(self.workspace, ["AGENTS.md"], "test commit", runner=runner)
        self.assertFalse(plan.has_changes)


if __name__ == "__main__":
    unittest.main()
