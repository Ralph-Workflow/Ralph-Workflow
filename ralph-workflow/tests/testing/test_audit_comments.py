from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

from ralph.testing.audit_comments import FIX_HINT, audit_repository, main


def _write(root: Path, name: str, content: str) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _run(
    root: Path,
    name: str,
    content: str,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str]:
    target = _write(root, name, content)
    code = main(["--paths", str(target)])
    return code, capsys.readouterr().out


def test_a_python_comment_token_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, output = _run(tmp_path, "mod.py", "value = 1  # a comment\n", capsys)

    assert code == 1, reason
    assert "# a comment" in output, reason


def test_a_hash_inside_a_python_string_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "mod.py", 'marker = "# not a comment"\n', capsys)

    assert code == 0, reason


def test_a_module_docstring_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, output = _run(tmp_path, "mod.py", '"""Module docstring."""\n', capsys)

    assert code == 1, reason
    assert "Module docstring." in output, reason


def test_a_class_docstring_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = 'class Thing:\n    """Class docstring."""\n'

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "Class docstring." in output, reason


def test_a_function_docstring_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = 'def run() -> int:\n    """Function docstring."""\n    return 0\n'

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "Function docstring." in output, reason


def test_a_bare_string_statement_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = 'value = 1\n"documentation for the next reader"\n'

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "documentation for the next reader" in output, reason


def test_a_line_one_shebang_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "#!/usr/bin/env python\nvalue = 1\n"

    code, _output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 0, reason


def test_a_hash_on_a_later_line_is_not_a_shebang(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "value = 1\n#!not a shebang\n"

    code, _output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason


def test_a_noqa_directive_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "value = 1  # no" + "qa\n"

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "no" + "qa" in output, reason


def test_a_type_ignore_directive_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "value = 1  # type:" + " ignore[arg-type]\n"

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "type:" in output, reason


def test_a_pragma_directive_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "value = 1  # pragma:" + " no cover\n"

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "pragma:" in output, reason


def test_a_comment_ok_bypass_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "# comment-ok: tracked in issue 42\nvalue = 1\n"

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "comment-ok" in output, reason


def test_the_finding_shape_is_path_line_text_and_hint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    target = _write(tmp_path, "mod.py", "value = 1  # a comment\n")

    code = main(["--paths", str(target)])
    output = capsys.readouterr().out

    assert code == 1, reason
    assert f"{target}:1: # a comment — {FIX_HINT}" in output, reason


def test_a_shell_comment_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "run.sh", "echo ready # a comment\n", capsys)

    assert code == 1, reason


def test_a_shell_hash_inside_quotes_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "run.sh", 'echo "a # b"\n', capsys)

    assert code == 0, reason


def test_a_toml_comment_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "config.toml", 'key = "value" # a comment\n', capsys)

    assert code == 1, reason


def test_a_toml_hash_inside_quotes_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "config.toml", 'key = "a # b"\n', capsys)

    assert code == 0, reason


def test_a_yaml_comment_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "config.yaml", "key: value # a comment\n", capsys)

    assert code == 1, reason


def test_a_yaml_hash_inside_quotes_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "config.yaml", 'key: "a # b"\n', capsys)

    assert code == 0, reason


def test_a_makefile_comment_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "all:\n\t@echo ready # a comment\n"

    code, _output = _run(tmp_path, "Makefile", source, capsys)

    assert code == 1, reason


def test_a_makefile_hash_inside_quotes_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = 'all:\n\t@echo "a # b"\n'

    code, _output = _run(tmp_path, "Makefile", source, capsys)

    assert code == 0, reason


def test_a_jinja_block_comment_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "{# a jinja comment #}\n<prompt></prompt>\n"

    code, output = _run(tmp_path, "prompt.jinja", source, capsys)

    assert code == 1, reason
    assert "a jinja comment" in output, reason


def test_a_jinja_text_hash_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "<prompt># a markdown heading</prompt>\n"

    code, _output = _run(tmp_path, "prompt.jinja", source, capsys)

    assert code == 0, reason


@pytest.mark.parametrize("suffix", [".j2", ".txt"])
def test_a_jinja_block_comment_fails_in_a_template_suffix(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    suffix: str,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate scans every rendered template suffix"
    source = "{# a jinja comment #}\n<prompt></prompt>\n"

    code, output = _run(tmp_path, f"prompt{suffix}", source, capsys)

    assert code == 1, reason
    assert "a jinja comment" in output, reason


@pytest.mark.parametrize(
    "marker",
    ["decision", "decided", "do not reopen", "rationale", "non-adoption", "we chose"],
)
def test_a_docs_decision_marker_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    marker: str,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = f"This text carries the word {marker} in prose.\n"

    code, output = _run(tmp_path, "notes.md", source, capsys)

    assert code == 1, reason
    assert marker in output, reason


def test_a_docs_marker_inside_a_fenced_block_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "```\nthe decision is quoted code\n```\n"

    code, _output = _run(tmp_path, "notes.md", source, capsys)

    assert code == 0, reason


def test_a_docs_marker_inside_an_inline_code_span_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "Use the `decision` marker in the template.\n"

    code, _output = _run(tmp_path, "notes.md", source, capsys)

    assert code == 0, reason


@pytest.mark.parametrize(
    "marker",
    ["decision", "decided", "do not reopen", "rationale", "non-adoption", "we chose"],
)
def test_an_rst_decision_marker_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    marker: str,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate scans sphinx rst prose"
    source = f"This text carries the word {marker} in prose.\n"

    code, output = _run(tmp_path, "notes.rst", source, capsys)

    assert code == 1, reason
    assert marker in output, reason


def test_an_rst_marker_inside_a_literal_block_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate ignores rst literal blocks"
    source = "The output reads::\n\n    the decision is quoted code\n\nNext paragraph.\n"

    code, _output = _run(tmp_path, "notes.rst", source, capsys)

    assert code == 0, reason


def test_an_rst_marker_inside_a_code_directive_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate ignores rst code directives"
    source = ".. code-block:: python\n\n    decision = 1\n\nNext paragraph.\n"

    code, _output = _run(tmp_path, "notes.rst", source, capsys)

    assert code == 0, reason


def test_an_rst_marker_inside_an_inline_literal_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate ignores rst inline literals"
    source = "Use the ``decision`` marker in the template.\n"

    code, _output = _run(tmp_path, "notes.rst", source, capsys)

    assert code == 0, reason


def test_the_cli_returns_zero_on_a_clean_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    _write(tmp_path, "mod.py", "value = 1\n")
    _write(tmp_path, "notes.md", "Current facts only.\n")

    code = main(["--paths", str(tmp_path)])
    output = capsys.readouterr().out

    assert code == 0, reason
    assert "No comment, docstring or documentation-decision findings." in output, reason


def test_the_cli_returns_one_when_a_finding_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    _write(tmp_path, "mod.py", "value = 1  # a comment\n")

    code = main(["--paths", str(tmp_path)])
    output = capsys.readouterr().out

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason


def test_the_cli_exclude_flag_drops_a_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    bad = _write(tmp_path, "bad.py", "value = 1  # a comment\n")
    _write(tmp_path, "good.py", "value = 2\n")

    code = main(["--paths", str(tmp_path), "--exclude", str(bad)])
    output = capsys.readouterr().out

    assert code == 0, reason
    assert "COMMENT-POLICY FINDINGS" not in output, reason


def test_the_cli_paths_flag_accepts_a_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "mod.py").write_text("value = 1  # a comment\n", encoding="utf-8")

    code = main(["--paths", str(nested)])
    output = capsys.readouterr().out

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason


def test_the_module_runs_as_a_script(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    target = _write(tmp_path, "mod.py", "value = 1  # a comment\n")
    module_path = Path(__file__).resolve().parents[2] / "ralph" / "testing" / "audit_comments.py"
    monkeypatch.setattr("sys.argv", ["audit_comments", "--paths", str(target)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(module_path), run_name="__main__")
    output = capsys.readouterr().out

    assert excinfo.value.code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason


def test_a_toml_hash_after_a_value_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "config.toml", "key = 1# a comment\n", capsys)

    assert code == 1, reason


def test_a_makefile_hash_after_a_value_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    code, _output = _run(tmp_path, "Makefile", "target = 1# a comment\n", capsys)

    assert code == 1, reason


def test_a_makefile_recipe_hash_without_spacing_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "all:\n\t@echo value#literal\n"

    code, _output = _run(tmp_path, "Makefile", source, capsys)

    assert code == 0, reason


def test_a_docstring_in_an_except_handler_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = (
        "def run() -> int:\n"
        "    try:\n"
        "        return 1\n"
        "    except ValueError:\n"
        '        "Handler documentation."\n'
    )

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "Handler documentation." in output, reason


def test_a_docstring_in_a_nested_block_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = (
        "def run(flag: bool) -> int:\n"
        "    if flag:\n"
        '        "Block documentation."\n'
        "        return 1\n"
        "    return 0\n"
    )

    code, output = _run(tmp_path, "mod.py", source, capsys)

    assert code == 1, reason
    assert "Block documentation." in output, reason


def test_a_yaml_block_scalar_literal_text_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "script: |\n  #!/bin/sh\n  echo ready\n"

    code, _output = _run(tmp_path, "config.yaml", source, capsys)

    assert code == 0, reason


def test_a_yaml_comment_after_a_block_indicator_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate replaces the permissive fixtures"
    source = "script: | # a comment\n  echo ready\n"

    code, _output = _run(tmp_path, "config.yaml", source, capsys)

    assert code == 1, reason


@pytest.mark.parametrize(
    "relative_path",
    [
        "ralph/policy/defaults/ralph-workflow-agents.toml",
        "ralph/policy/defaults/ralph-workflow.toml",
        "ralph/policy/defaults/ralph-workflow-local.toml",
        "ralph/policy/defaults/mcp.toml",
        "ralph/testing/test_whitebox_baseline.py",
    ],
)
def test_formerly_exempt_paths_fail_on_comment(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative_path: str,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate rejects formerly exempt paths"
    code, output = _run(tmp_path, relative_path, "value = 1  # formerly exempt comment\n", capsys)

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason
    assert "formerly exempt comment" in output, reason


def test_formerly_exempt_whitebox_baseline_fails_on_docstring(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate rejects docstrings in baseline file"
    source = '"""Docstring in baseline."""\nINIT_COMPLETE = True\n'

    code, output = _run(
        tmp_path,
        "ralph/testing/test_whitebox_baseline.py",
        source,
        capsys,
    )

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason
    assert "Docstring in baseline." in output, reason


def test_toml_agent_block_marker_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate rejects structural agent block markers"
    source = "# @AGENT-BLOCK-START\n# key = 1\n# @AGENT-BLOCK-END\n"

    code, output = _run(tmp_path, "config.toml", source, capsys)

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason
    assert "@AGENT-BLOCK-START" in output, reason


def test_toml_example_marker_fences_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate rejects structural example markers"
    source = "# @EXAMPLE-START\n# key = 1\n# @EXAMPLE-END\n"

    code, output = _run(tmp_path, "config.toml", source, capsys)

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason
    assert "@EXAMPLE-START" in output, reason


def test_toml_bare_example_marker_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate rejects bare example marker"
    source = "# @EXAMPLE\nkey = 1\n"

    code, output = _run(tmp_path, "config.toml", source, capsys)

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason
    assert "@EXAMPLE" in output, reason


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("config.ini", "[section]\n# a comment\nkey = value\n"),
        ("config.conf", "[theme]\n# a comment\ninherit = basic\n"),
        ("script.js", "var value = 1; // a comment\n"),
        ("script.js", "/* a comment */\nvar value = 1;\n"),
        ("styles.css", "/* a comment */\nbody { color: red; }\n"),
        ("Dockerfile", "# a comment\nFROM debian:bookworm-slim\n"),
        (".dockerignore", "# a comment\n*.log\n"),
        ("py.typed", "# a comment\n"),
        ("formula.rb", "# a comment\nclass RalphWorkflow < Formula\nend\n"),
    ],
)
def test_a_comment_in_a_newly_mapped_file_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    source: str,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate covers every mapped suffix"
    code, output = _run(tmp_path, name, source, capsys)

    assert code == 1, reason
    assert "COMMENT-POLICY FINDINGS" in output, reason
    assert "a comment" in output, reason


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("config.ini", "[section]\nkey = value\n"),
        ("config.conf", "[theme]\ninherit = basic\n"),
        ("script.js", "var value = 1;\n"),
        ("styles.css", "body {\n    color: red;\n}\n"),
        ("Dockerfile", "FROM debian:bookworm-slim\n"),
        (".dockerignore", "*.log\n"),
        ("py.typed", ""),
        ("formula.rb", "class RalphWorkflow < Formula\nend\n"),
    ],
)
def test_a_comment_free_file_of_a_newly_mapped_suffix_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    source: str,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate scans every mapped suffix"
    code, _output = _run(tmp_path, name, source, capsys)

    assert code == 0, reason


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("config.ini", '[section]\nkey = "a # b"\n'),
        ("script.js", 'var url = "https://example.com";\n'),
        ("styles.css", "@import url('https://fonts.example.com/css2?family=Inter');\n"),
    ],
)
def test_a_comment_marker_inside_a_quoted_value_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    source: str,
) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate ignores markers inside strings"
    code, _output = _run(tmp_path, name, source, capsys)

    assert code == 0, reason


def test_a_githook_pre_commit_shell_comment_fails(tmp_path: Path) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate scans the git hook directory"
    _write(tmp_path / ".githooks", "pre-commit", "# a comment\nset -euo pipefail\n")

    findings = audit_repository(tmp_path)

    assert findings, reason
    assert any(finding.path.endswith(".githooks/pre-commit") for finding in findings), reason


def test_a_comment_free_githook_pre_commit_passes(tmp_path: Path) -> None:
    reason = "# RALPH-TEST-REASON: absolute no-comment gate scans the git hook directory"
    _write(tmp_path / ".githooks", "pre-commit", "#!/usr/bin/env bash\nset -euo pipefail\n")

    findings = audit_repository(tmp_path)

    assert findings == [], reason


def test_a_missing_explicit_path_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: fail-closed comment audit rejects a missing --paths input"
    missing = tmp_path / "missing-input.py"

    code = main(["--paths", str(missing)])
    output = capsys.readouterr().out

    assert code == 2, reason
    assert "COMMENT-AUDIT INPUT ERROR" in output, reason
    assert str(missing) in output, reason


def test_a_missing_explicit_path_fails_closed_without_path_env(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = (
        "# RALPH-TEST-REASON: fail-closed comment audit rejects a missing input even without PATH"
    )
    monkeypatch.setenv("PATH", "/nonexistent")
    missing = "/definitely-missing-ralph-review-input"

    code = main(["--paths", missing])
    output = capsys.readouterr().out

    assert code == 2, reason
    assert "COMMENT-AUDIT INPUT ERROR" in output, reason
    assert missing in output, reason


def test_a_mixed_valid_and_missing_explicit_path_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: fail-closed comment audit rejects any missing --paths input"
    good = _write(tmp_path, "mod.py", "value = 1\n")
    missing = tmp_path / "missing-input.py"

    code = main(["--paths", str(good), str(missing)])
    output = capsys.readouterr().out

    assert code == 2, reason
    assert "COMMENT-AUDIT INPUT ERROR" in output, reason
    assert str(missing) in output, reason


def test_repository_mode_fails_closed_when_git_is_unavailable(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "# RALPH-TEST-REASON: fail-closed comment audit reports a git enumeration failure"
    monkeypatch.setenv("PATH", "/nonexistent")

    code = main([])
    output = capsys.readouterr().out

    assert code == 2, reason
    assert "COMMENT-AUDIT INPUT ERROR" in output, reason
    assert "git" in output, reason


def test_an_unreadable_explicit_file_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = "# RALPH-TEST-REASON: fail-closed comment audit reports an unreadable required target"
    if os.geteuid() == 0:
        pytest.skip("root reads chmod 000 files")
    target = _write(tmp_path, "mod.py", "value = 1\n")
    target.chmod(0)
    try:
        code = main(["--paths", str(target)])
    finally:
        target.chmod(0o644)
    output = capsys.readouterr().out

    assert code == 2, reason
    assert "COMMENT-AUDIT INPUT ERROR" in output, reason
    assert "cannot read required target" in output, reason
    assert str(target) in output, reason
