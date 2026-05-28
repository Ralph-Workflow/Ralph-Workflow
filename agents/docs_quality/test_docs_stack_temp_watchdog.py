from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path('/home/mistlight/.openclaw/workspace/agents/docs_quality/docs_stack_temp_watchdog.py')


spec = importlib.util.spec_from_file_location('docs_stack_temp_watchdog', MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

EDITORIAL_MODULE_PATH = Path('/home/mistlight/.openclaw/workspace/agents/docs_quality/ralph_docs_editorial_audit.py')
editorial_spec = importlib.util.spec_from_file_location('ralph_docs_editorial_audit', EDITORIAL_MODULE_PATH)
editorial_mod = importlib.util.module_from_spec(editorial_spec)
assert editorial_spec.loader is not None
sys.modules[editorial_spec.name] = editorial_mod
editorial_spec.loader.exec_module(editorial_mod)


class DocsStackTempWatchdogTests(unittest.TestCase):
    def test_aggressive_repair_passes_stops_after_runner_and_verify_without_redundant_direct_checks(self) -> None:
        calls: list[tuple[str, int]] = []

        def fake_run(path: Path, *, timeout: int = mod.TIMEOUT, watchdog_mode: bool = False, skip_agentic: bool = False) -> dict:
            calls.append((path.name, timeout))
            if path == mod.RUNNER:
                return {'path': str(path), 'exit': 1, 'output': 'runner failed'}
            if path == mod.VERIFY:
                return {'path': str(path), 'exit': 1, 'output': 'verifier failed'}
            raise AssertionError(f'unexpected path {path}')

        with patch.object(mod, 'run_py', side_effect=fake_run), \
             patch.object(mod, 'load_agentic', return_value={'status': 'fail', 'loopHealthy': False, 'shouldUserNeedToRepeatThis': True}), \
             patch.object(mod, 'docs_state_fingerprint', return_value='fp'), \
             patch.object(mod, 'evaluate_health', return_value=(False, ['broken'])):
            runs = mod.aggressive_repair_passes()

        self.assertEqual([name for name, _ in calls], ['ralph_docs_runner.py', 'ralph_docs_verify.py'])
        self.assertEqual(len(runs), 2)

    def test_recursion_guard_returns_broken_status(self) -> None:
        with patch.dict(mod.os.environ, {mod.WATCHDOG_SELF_ENV: '1'}, clear=False), \
             patch.object(mod, 'write_status') as write_status:
            rc = mod.main([])

        self.assertEqual(rc, 1)
        write_status.assert_called_once()

    def test_verifier_lock_exit_75_does_not_fail_when_green_artifact_and_valid_signoff_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            verifier = Path(tmpdir) / 'ralph_verifier_latest.md'
            signoff = Path(tmpdir) / 'docs_stack_parallel_signoff.json'
            verifier.write_text('Status: independently verified pass\n', encoding='utf-8')
            signoff.write_text(json.dumps({
                'approvedToDeactivate': True,
                'docsStateFingerprint': 'abc123',
                'verifierPassed': True,
                'agenticPassed': True,
                'repeatFailureCleared': True,
            }), encoding='utf-8')

            runs = [
                {'path': str(mod.CHECKER), 'exit': 0, 'output': 'DOCS_QUALITY_OK'},
                {'path': str(mod.EDITORIAL), 'exit': 0, 'output': 'DOCS_EDITORIAL_OK'},
                {'path': str(mod.AGENTIC), 'exit': 0, 'output': '{}'},
                {'path': str(mod.VERIFY), 'exit': 75, 'output': 'SKIP: another Ralph docs loop process already holds the global lock'},
            ]
            agentic = {
                'status': 'pass',
                'loopHealthy': True,
                'shouldUserNeedToRepeatThis': False,
            }

            with patch.object(mod, 'VERIFIER_MD', verifier), patch.object(mod, 'PARALLEL_SIGNOFF_JSON', signoff):
                healthy, reasons = mod.evaluate_health(agentic, runs, 'abc123')

            self.assertTrue(healthy)
            self.assertEqual(reasons, [])

    def test_missing_parallel_signoff_still_fails_even_when_artifacts_are_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            verifier = Path(tmpdir) / 'ralph_verifier_latest.md'
            signoff = Path(tmpdir) / 'docs_stack_parallel_signoff.json'
            verifier.write_text('Status: independently verified pass\n', encoding='utf-8')
            signoff.write_text(json.dumps({
                'approvedToDeactivate': False,
                'docsStateFingerprint': None,
                'verifierPassed': False,
                'agenticPassed': False,
                'repeatFailureCleared': False,
            }), encoding='utf-8')

            runs = [
                {'path': str(mod.CHECKER), 'exit': 0, 'output': 'DOCS_QUALITY_OK'},
                {'path': str(mod.EDITORIAL), 'exit': 0, 'output': 'DOCS_EDITORIAL_OK'},
                {'path': str(mod.AGENTIC), 'exit': 0, 'output': '{}'},
                {'path': str(mod.VERIFY), 'exit': 75, 'output': 'SKIP: another Ralph docs loop process already holds the global lock'},
            ]
            agentic = {
                'status': 'pass',
                'loopHealthy': True,
                'shouldUserNeedToRepeatThis': False,
            }

            with patch.object(mod, 'VERIFIER_MD', verifier), patch.object(mod, 'PARALLEL_SIGNOFF_JSON', signoff):
                healthy, reasons = mod.evaluate_health(agentic, runs, 'abc123')

            self.assertFalse(healthy)
            self.assertIn('parallel signoff did not approve deactivation', reasons)


class RalphDocsAgenticReviewWatchdogTests(unittest.TestCase):
    def test_bounded_watchdog_result_reuses_cached_full_verdict(self) -> None:
        agentic_spec = importlib.util.spec_from_file_location(
            'ralph_docs_agentic_review',
            Path('/home/mistlight/.openclaw/workspace/agents/docs_quality/ralph_docs_agentic_review.py'),
        )
        agentic_mod = importlib.util.module_from_spec(agentic_spec)
        assert agentic_spec.loader is not None
        agentic_spec.loader.exec_module(agentic_mod)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_report = Path(tmpdir) / 'ralph_agentic_latest.json'
            cached = {
                'status': 'pass',
                'summary': 'cached pass',
                'loopHealthy': True,
                'criteria': {'positioning': 'pass'},
                'mustFix': [],
                'strongestEvidence': [],
                'shouldUserNeedToRepeatThis': False,
            }
            json_report.write_text(json.dumps(cached), encoding='utf-8')

            with patch.object(agentic_mod, 'JSON_REPORT', json_report):
                result = agentic_mod._bounded_watchdog_result('bounded skip')

            self.assertEqual(result['status'], 'pass')
            self.assertTrue(result['loopHealthy'])
            self.assertFalse(result['shouldUserNeedToRepeatThis'])
            self.assertEqual(result['watchdogMode'], 'cached-full-agentic-verdict')

    def test_bounded_watchdog_result_fails_only_without_prior_full_verdict(self) -> None:
        agentic_spec = importlib.util.spec_from_file_location(
            'ralph_docs_agentic_review',
            Path('/home/mistlight/.openclaw/workspace/agents/docs_quality/ralph_docs_agentic_review.py'),
        )
        agentic_mod = importlib.util.module_from_spec(agentic_spec)
        assert agentic_spec.loader is not None
        agentic_spec.loader.exec_module(agentic_mod)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_report = Path(tmpdir) / 'ralph_agentic_latest.json'
            with patch.object(agentic_mod, 'JSON_REPORT', json_report):
                result = agentic_mod._bounded_watchdog_result('bounded skip')

            self.assertEqual(result['status'], 'fail')
            self.assertFalse(result['loopHealthy'])
            self.assertTrue(result['shouldUserNeedToRepeatThis'])

    def test_runner_lock_exit_75_does_not_invalidate_broken_but_reportable_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            verifier = Path(tmpdir) / 'ralph_verifier_latest.md'
            signoff = Path(tmpdir) / 'docs_stack_parallel_signoff.json'
            verifier.write_text('Status: independent verifier failed signoff\n', encoding='utf-8')
            signoff.write_text(json.dumps({
                'approvedToDeactivate': True,
                'docsStateFingerprint': 'olderfp',
                'verifierPassed': True,
                'agenticPassed': True,
                'repeatFailureCleared': True,
            }), encoding='utf-8')

            runs = [
                {'path': str(mod.CHECKER), 'exit': 0, 'output': 'DOCS_QUALITY_OK'},
                {'path': str(mod.EDITORIAL), 'exit': 0, 'output': 'DOCS_EDITORIAL_OK'},
                {'path': str(mod.AGENTIC), 'exit': 1, 'output': '{"status": "fail"}'},
                {'path': str(mod.RUNNER), 'exit': 75, 'output': 'SKIP: another Ralph docs loop process already holds the global lock'},
                {'path': str(mod.VERIFY), 'exit': 1, 'output': 'independent verifier failed signoff'},
            ]
            agentic = {
                'status': 'fail',
                'loopHealthy': False,
                'shouldUserNeedToRepeatThis': True,
            }

            with patch.object(mod, 'VERIFIER_MD', verifier), patch.object(mod, 'PARALLEL_SIGNOFF_JSON', signoff):
                healthy, reasons = mod.evaluate_health(agentic, runs, 'newfp')

            self.assertFalse(healthy)
            self.assertNotIn('parallel signoff fingerprint does not match current docs state', reasons)
            self.assertIn('agentic status is not pass', reasons)
        with tempfile.TemporaryDirectory() as tmpdir:
            verifier = Path(tmpdir) / 'ralph_verifier_latest.md'
            signoff = Path(tmpdir) / 'docs_stack_parallel_signoff.json'
            verifier.write_text('Status: independently verified pass\n', encoding='utf-8')
            signoff.write_text(json.dumps({
                'approvedToDeactivate': False,
                'docsStateFingerprint': None,
                'verifierPassed': False,
                'agenticPassed': False,
                'repeatFailureCleared': False,
            }), encoding='utf-8')

            runs = [
                {'path': str(mod.CHECKER), 'exit': 0, 'output': 'DOCS_QUALITY_OK'},
                {'path': str(mod.EDITORIAL), 'exit': 0, 'output': 'DOCS_EDITORIAL_OK'},
                {'path': str(mod.AGENTIC), 'exit': 0, 'output': '{}'},
                {'path': str(mod.VERIFY), 'exit': 75, 'output': 'SKIP: another Ralph docs loop process already holds the global lock'},
            ]
            agentic = {
                'status': 'pass',
                'loopHealthy': True,
                'shouldUserNeedToRepeatThis': False,
            }

            with patch.object(mod, 'VERIFIER_MD', verifier), patch.object(mod, 'PARALLEL_SIGNOFF_JSON', signoff):
                healthy, reasons = mod.evaluate_health(agentic, runs, 'abc123')

            self.assertFalse(healthy)
            self.assertIn('parallel signoff did not approve deactivation', reasons)


class RalphDocsEditorialGuardrailTests(unittest.TestCase):
    def test_start_here_requires_explicit_success_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            page = Path(tmpdir) / 'START_HERE.md'
            page.write_text(
                '# Start Here\n\n## Before you start\n\n- repo\n\n## Install and run\n\n```bash\nralph\n```\n\n## Next pages only if you need them\n',
                encoding='utf-8',
            )

            issues = editorial_mod.require_start_here_structure(page)

        self.assertTrue(any(issue.kind == 'start-here-structure-gap' for issue in issues))

    def test_mirror_readme_rejects_non_canonical_subtagline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            page = Path(tmpdir) / 'README.md'
            page.write_text('# Ralph Workflow\n\nWrite the spec. Wake up to working software.\n', encoding='utf-8')

            issues = editorial_mod.require_mirror_readme_positioning_discipline(page)

        self.assertEqual([issue.kind for issue in issues], ['mirror-positioning-drift'])

    def test_reviewable_output_rejects_duplicate_role_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            page = Path(tmpdir) / 'reviewable-output.md'
            page.write_text(
                'This page is supporting proof and not the main product pitch.\n\n'
                'Use this page after the product story.\n'
                'This page is supporting proof and not the main product pitch.\n',
                encoding='utf-8',
            )

            issues = editorial_mod.require_proof_page_no_role_duplication(page)

        self.assertEqual([issue.kind for issue in issues], ['proof-role-duplication'])

    def test_task_framing_rejects_small_task_starter_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            page = Path(tmpdir) / 'START_HERE.md'
            page.write_text(
                '# Start Here\n\n'
                'If you want the lowest-friction first run, pick a validation rule or a focused feature slice.\n'
                'A bounded refactor with tests should be easy to judge tomorrow morning.\n',
                encoding='utf-8',
            )

            issues = editorial_mod.require_task_framing(page)

        self.assertTrue(any(issue.kind == 'fit-drift' for issue in issues))

    def test_positioning_alignment_rejects_proof_first_workspace_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            page = Path(tmpdir) / 'README.md'
            page.write_text(
                '# Ralph Workflow\n\n'
                'Run tonight. Review in the morning. Merge if earned.\n\n'
                'A reviewable result matters more than anything else.\n',
                encoding='utf-8',
            )

            issues = editorial_mod.require_positioning_alignment(page)

        kinds = {issue.kind for issue in issues}
        self.assertIn('positioning-gap', kinds)
        self.assertIn('positioning-drift', kinds)

    def test_workspace_root_docs_are_in_audit_scope(self) -> None:
        self.assertIn(editorial_mod.WORKSPACE_ROOT_README, editorial_mod.TOP_LEVEL_SURFACES)
        self.assertIn(editorial_mod.WORKSPACE_ROOT_START_HERE, editorial_mod.TOP_LEVEL_SURFACES)
        self.assertIn(editorial_mod.WORKSPACE_ROOT_README, editorial_mod.PRODUCT_SURFACES)
        self.assertIn(editorial_mod.WORKSPACE_ROOT_START_HERE, editorial_mod.PRODUCT_SURFACES)
        self.assertFalse(hasattr(editorial_mod, 'WORKSPACE_ROOT_FIRST_TASK'))


if __name__ == '__main__':
    unittest.main()
