#!/usr/bin/env python3
import sys
import os
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prepare_agent_dispatch import (
    count_errors_in_dylint_file,
    get_actual_dylint_info,
    get_effective_dylint_info,
    AGENTS,
)


class TestCountErrorsInDylintFile(unittest.TestCase):
    def test_returns_count_from_total_line(self):
        content = textwrap.dedent("""\
            # Dylint Errors: json_parser

            Total: 42 errors

            ================================================================================
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            self.assertEqual(count_errors_in_dylint_file(path), 42)
        finally:
            os.unlink(path)

    def test_returns_zero_when_file_has_no_errors(self):
        content = "# Dylint Errors: foo\n\nTotal: 0 errors\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            self.assertEqual(count_errors_in_dylint_file(path), 0)
        finally:
            os.unlink(path)

    def test_returns_negative_one_for_missing_file(self):
        self.assertEqual(count_errors_in_dylint_file("/nonexistent/path/dylint-foo.txt"), -1)

    def test_returns_zero_when_total_line_absent(self):
        content = "# Dylint Errors: foo\n\nNo total line here.\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            self.assertEqual(count_errors_in_dylint_file(path), 0)
        finally:
            os.unlink(path)

    def test_strips_hardcoded_count_suffix_from_path_argument(self):
        content = "# Dylint Errors: json_parser\n\nTotal: 7 errors\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            path_with_suffix = f"{path} (162)"
            self.assertEqual(count_errors_in_dylint_file(path_with_suffix), 7)
        finally:
            os.unlink(path)


class TestGetActualDylintInfo(unittest.TestCase):
    def _make_dylint_file(self, tmpdir, module, count):
        content = f"# Dylint Errors: {module}\n\nTotal: {count} errors\n"
        path = os.path.join(tmpdir, f"dylint-{module}.txt")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_returns_actual_sum_across_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = self._make_dylint_file(tmpdir, "reducer", 10)
            p2 = self._make_dylint_file(tmpdir, "pipeline", 5)
            config = {
                "dylint_files": [p1, p2],
                "total_dylint": 999,
            }
            total, display = get_actual_dylint_info(config)
            self.assertEqual(total, 15)

    def test_display_strings_show_real_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = self._make_dylint_file(tmpdir, "reducer", 10)
            config = {"dylint_files": [p1], "total_dylint": 999}
            _, display = get_actual_dylint_info(config)
            self.assertIn("(10)", display[0])

    def test_missing_file_contributes_plain_path_and_zero_to_total(self):
        config = {
            "dylint_files": ["/nonexistent/dylint-foo.txt"],
            "total_dylint": 999,
        }
        total, display = get_actual_dylint_info(config)
        self.assertEqual(total, 0)
        self.assertEqual(display[0], "/nonexistent/dylint-foo.txt (0)")

    def test_strips_hardcoded_count_suffix_from_config_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_path = self._make_dylint_file(tmpdir, "reducer", 3)
            config = {
                "dylint_files": [f"{real_path} (109)"],
                "total_dylint": 109,
            }
            total, display = get_actual_dylint_info(config)
            self.assertEqual(total, 3)
            self.assertIn("(3)", display[0])
            self.assertNotIn("(109)", display[0])

    def test_all_agents_have_dylint_files_key(self):
        for agent_name, config in AGENTS.items():
            self.assertIn(
                "dylint_files",
                config,
                f"Agent {agent_name} is missing 'dylint_files' key",
            )


class TestGetEffectiveDylintInfo(unittest.TestCase):
    def _make_dylint_file(self, tmpdir, module, count):
        content = f"# Dylint Errors: {module}\n\nTotal: {count} errors\n"
        path = os.path.join(tmpdir, f"dylint-{module}.txt")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_uses_actual_counts_when_dylint_succeeded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_dylint_file(tmpdir, "reducer", 4)
            total, display, stale = get_effective_dylint_info(
                {"dylint_files": [path], "total_dylint": 999},
                dylint_success=True,
            )
            self.assertEqual(total, 4)
            self.assertEqual(stale, False)
            self.assertIn("(4)", display[0])

    def test_skips_dylint_entirely_when_dylint_is_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_dylint_file(tmpdir, "reducer", 4)
            total, display, stale = get_effective_dylint_info(
                {"dylint_files": [path], "total_dylint": 999},
                dylint_success=False,
            )
            self.assertEqual(total, 0)
            self.assertEqual(stale, True)
            self.assertEqual(
                display,
                ["Skipped: dylint data is stale; fix compilation errors first."],
            )


class TestCheckCargoAvailable(unittest.TestCase):
    def test_returns_true_when_cargo_version_succeeds(self):
        import subprocess
        original_run = subprocess.run
        def mock_run(cmd, *args, **kwargs):
            if cmd == ["cargo", "--version"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="cargo 1.70.0", stderr="")
            return original_run(cmd, *args, **kwargs)
        subprocess.run = mock_run
        try:
            from prepare_agent_dispatch import check_cargo_available
            result = check_cargo_available()
            self.assertTrue(result)
        finally:
            subprocess.run = original_run

    def test_returns_false_when_cargo_not_found(self):
        import subprocess
        original_run = subprocess.run
        def mock_run(cmd, *args, **kwargs):
            if cmd == ["cargo", "--version"]:
                raise FileNotFoundError("cargo not found")
            return original_run(cmd, *args, **kwargs)
        subprocess.run = mock_run
        try:
            from prepare_agent_dispatch import check_cargo_available
            result = check_cargo_available()
            self.assertFalse(result)
        finally:
            subprocess.run = original_run

    def test_returns_false_when_command_not_found_in_stderr(self):
        import subprocess
        original_run = subprocess.run
        def mock_run(cmd, *args, **kwargs):
            if cmd == ["cargo", "--version"]:
                return subprocess.CompletedProcess(cmd, returncode=127, stdout="", stderr="cargo: command not found")
            return original_run(cmd, *args, **kwargs)
        subprocess.run = mock_run
        try:
            from prepare_agent_dispatch import check_cargo_available
            result = check_cargo_available()
            self.assertFalse(result)
        finally:
            subprocess.run = original_run

    def test_returns_false_when_returncode_nonzero(self):
        import subprocess
        original_run = subprocess.run
        def mock_run(cmd, *args, **kwargs):
            if cmd == ["cargo", "--version"]:
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")
            return original_run(cmd, *args, **kwargs)
        subprocess.run = mock_run
        try:
            from prepare_agent_dispatch import check_cargo_available
            result = check_cargo_available()
            self.assertFalse(result)
        finally:
            subprocess.run = original_run


class TestCargoWarningInInstructions(unittest.TestCase):
    def setUp(self):
        import os
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.orig_cwd = os.getcwd()
        os.chdir(self.project_root)
        os.makedirs("tmp", exist_ok=True)

    def tearDown(self):
        import os
        os.chdir(self.orig_cwd)
    
    def test_cargo_warning_added_when_cargo_not_available(self):
        from prepare_agent_dispatch import generate_agent_instructions
        
        errors_by_agent = {}
        warnings_by_agent = {}
        has_test_failures = False
        dylint_success = True
        
        generate_agent_instructions(
            errors_by_agent,
            warnings_by_agent,
            has_test_failures,
            dylint_success=dylint_success,
            cargo_available=False
        )
        
        for agent_name in ["workflow-reducer", "workflow-config", "workflow-json"]:
            filename = f"tmp/agent-instructions-{agent_name}.txt"
            if os.path.exists(filename):
                with open(filename) as f:
                    content = f.read()
                self.assertIn("CARGO IS DELIBERATELY TURNED OFF", content)
                self.assertIn("DO NOT run `.opencode/verify_agent_work.sh`", content)
                self.assertIn("Focus on **IMPLEMENTING**", content)
                os.unlink(filename)

    def test_no_cargo_warning_when_cargo_available(self):
        from prepare_agent_dispatch import generate_agent_instructions
        
        errors_by_agent = {}
        warnings_by_agent = {}
        has_test_failures = False
        dylint_success = True
        
        generate_agent_instructions(
            errors_by_agent,
            warnings_by_agent,
            has_test_failures,
            dylint_success=dylint_success,
            cargo_available=True
        )
        
        for agent_name in ["workflow-reducer", "workflow-config", "workflow-json"]:
            filename = f"tmp/agent-instructions-{agent_name}.txt"
            if os.path.exists(filename):
                with open(filename) as f:
                    content = f.read()
                self.assertNotIn("CARGO IS DELIBERATELY TURNED OFF", content)
                os.unlink(filename)


if __name__ == "__main__":
    unittest.main()
