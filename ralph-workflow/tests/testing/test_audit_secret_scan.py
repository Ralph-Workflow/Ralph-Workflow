"""Tests for :mod:`ralph.testing.audit_secret_scan`.

The secret-scan / SAST gate reports credential shapes on NEW or CHANGED
lines only, honours an inline bypass marker, and covers a small SAST slice
(``eval``/``exec`` on non-literal input, ``subprocess`` with
``shell=True``).

The planted credentials below are assembled from split literals on
purpose: the gate scans this repository too, and a fixture that carried a
contiguous credential literal would flag its own test file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_secret_scan import (
    AWS_ACCESS_KEY_ID_PATTERN,
    GITHUB_FINE_GRAINED_TOKEN_PATTERN,
    GITHUB_TOKEN_PATTERN,
    PRIVATE_KEY_PEM_PATTERN,
    SECRET_SCAN_BYPASS_MARKER,
    SLACK_TOKEN_PATTERN,
    SecretFinding,
    audit_secret_scan,
    main,
    scan_python_sast,
    scan_secret_lines,
)

if TYPE_CHECKING:
    from pathlib import Path

#: Split literals: no contiguous credential shape appears in this file.
_AWS_ACCESS_KEY_ID = "AKIA" + "Z7XY3QWERTY12345"
_GITHUB_CLASSIC_TOKEN = "ghp_" + ("a1b2c3d4e5" * 4)[:36]
_GITHUB_FINE_GRAINED_TOKEN = "github_pat_" + ("z9y8x7w6v5" * 3)[:22]
_SLACK_TOKEN = "xoxb-" + "123456789012-abcdefghijkl"
_PEM_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"
_HIGH_ENTROPY_VALUE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
_ASSIGNMENT_KEY = "api" + "_key"
_SECOND_ASSIGNMENT_KEY = "to" + "ken"


def _categories(findings: list[SecretFinding]) -> set[str]:
    return {finding.category for finding in findings}


def _planted_secret_file() -> str:
    """Return the text of a fixture that carries one real-shaped credential."""
    return (
        f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'
        f'aws_key = "{_AWS_ACCESS_KEY_ID}"\n'
        f'github_token = "{_GITHUB_CLASSIC_TOKEN}"\n'
        f'fine_grained = "{_GITHUB_FINE_GRAINED_TOKEN}"\n'
        f'slack_token = "{_SLACK_TOKEN}"\n'
        f"{_PEM_HEADER}\n"
    )


def _clean_file() -> str:
    """Return the text of a fixture that carries no credential."""
    return 'import os\n\n\ndef read_token() -> str:\n    return os.environ["TOKEN"]\n'


# --- the planted secret fails, the clean tree passes ------------------------


def test_planted_secret_in_a_fixture_fails(tmp_path: Path) -> None:
    """A credential planted in a temporary fixture is reported."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "config.py").write_text(_planted_secret_file(), encoding="utf-8")

    findings = audit_secret_scan({}, {"config.py": _planted_secret_file()})

    assert "generic-secret" in _categories(findings)
    assert "aws-access-key-id" in _categories(findings)
    assert "github-token" in _categories(findings)
    assert "github-fine-grained-token" in _categories(findings)
    assert "slack-token" in _categories(findings)
    assert "private-key" in _categories(findings)


def test_clean_tree_passes(tmp_path: Path) -> None:
    """A tree without credentials produces no findings."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "config.py").write_text(_clean_file(), encoding="utf-8")

    assert audit_secret_scan({}, {"config.py": _clean_file()}) == []


def test_the_reported_secret_value_is_never_printed() -> None:
    """The gate prints a fingerprint, never the credential itself."""
    findings = scan_secret_lines("config.py", _planted_secret_file(), {1})

    assert findings
    for finding in findings:
        assert _HIGH_ENTROPY_VALUE not in finding.detail
        assert _AWS_ACCESS_KEY_ID not in finding.detail
        assert "fingerprint" in finding.detail


# --- baseline-diff mode -----------------------------------------------------


def test_a_baseline_secret_is_not_reported() -> None:
    """A credential that the baseline already had is pre-existing debt."""
    findings = audit_secret_scan(
        {"config.py": _planted_secret_file()},
        {"config.py": _planted_secret_file()},
    )

    assert findings == []


def test_a_changed_line_with_a_secret_is_reported() -> None:
    """A credential on a CHANGED line is a regression and is reported."""
    baseline = {"config.py": f'{_ASSIGNMENT_KEY} = "old-value"\n'}
    current = {"config.py": f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'}

    findings = audit_secret_scan(baseline, current)

    assert "generic-secret" in _categories(findings)


def test_an_unchanged_line_around_a_changed_line_is_not_reported() -> None:
    """Only the changed line is reported; its neighbours stay quiet."""
    baseline = {"config.py": f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\nother = 1\n'}
    current = {"config.py": f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\nother = 2\n'}

    findings = audit_secret_scan(baseline, current)

    assert findings == []


def test_a_new_file_is_fully_scanned() -> None:
    """A file that the baseline does not have is entirely new."""
    findings = audit_secret_scan({}, {"config.py": _planted_secret_file()})

    assert findings


# --- the bypass marker ------------------------------------------------------


def test_the_bypass_marker_suppresses_a_finding() -> None:
    """An inline ``# secret-scan-ok: <reason>`` marker waives the line."""
    line = f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"  # {SECRET_SCAN_BYPASS_MARKER}: fixture\n'

    assert scan_secret_lines("config.py", line, {1}) == []


def test_the_bypass_marker_is_scoped_to_its_line() -> None:
    """The marker waives its own line only."""
    text = (
        f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"  # {SECRET_SCAN_BYPASS_MARKER}: fixture\n'
        f'{_SECOND_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'
    )

    findings = scan_secret_lines("config.py", text, {1, 2})

    assert [finding.line for finding in findings] == [2]


# --- the individual detectors -----------------------------------------------


def test_each_named_detector_fires_on_its_own_shape() -> None:
    """Every named detector recognises its own credential shape."""
    aws = scan_secret_lines("a.py", f'x = "{_AWS_ACCESS_KEY_ID}"\n', {1})
    github = scan_secret_lines("a.py", f'x = "{_GITHUB_CLASSIC_TOKEN}"\n', {1})
    fine_grained = scan_secret_lines("a.py", f'x = "{_GITHUB_FINE_GRAINED_TOKEN}"\n', {1})
    slack = scan_secret_lines("a.py", f'x = "{_SLACK_TOKEN}"\n', {1})
    pem = scan_secret_lines("a.py", f"{_PEM_HEADER}\n", {1})

    assert "aws-access-key-id" in _categories(aws)
    assert "github-token" in _categories(github)
    assert "github-fine-grained-token" in _categories(fine_grained)
    assert "slack-token" in _categories(slack)
    assert "private-key" in _categories(pem)


def test_the_detector_constants_are_named_and_usable() -> None:
    """The detector constants are module-level and directly usable."""
    assert AWS_ACCESS_KEY_ID_PATTERN.search(_AWS_ACCESS_KEY_ID) is not None
    assert GITHUB_TOKEN_PATTERN.search(_GITHUB_CLASSIC_TOKEN) is not None
    assert GITHUB_FINE_GRAINED_TOKEN_PATTERN.search(_GITHUB_FINE_GRAINED_TOKEN) is not None
    assert SLACK_TOKEN_PATTERN.search(_SLACK_TOKEN) is not None
    assert PRIVATE_KEY_PEM_PATTERN.search(_PEM_HEADER) is not None


def test_a_pem_header_inside_a_sentence_is_not_a_finding() -> None:
    """Prose that mentions the header is not a leaked private key."""
    line = f'text = "key fragment {_PEM_HEADER} data"\n'

    assert scan_secret_lines("a.py", line, {1}) == []


def test_placeholders_are_never_findings() -> None:
    """Obvious placeholders must not trip the gate."""
    placeholders = (
        'api_key = "REPLACE-ME"',
        'api_key = "xxxxxxxxxxxxxxxxxxxx"',
        'api_key = "example-api-key-value"',
        'api_key = "dummy-api-key-value"',
        'api_key = "your-api-key-value"',
        'api_key = "${API_KEY}"',
        'api_key = "{{ api_key }}"',
        "api_key = '<your-api-key>'",
    )
    for index, line in enumerate(placeholders, start=1):
        assert scan_secret_lines("a.py", f"{line}\n", {index}) == [], line


def test_a_low_entropy_credential_literal_is_not_a_finding() -> None:
    """A long but low-entropy fixture string is test material, not a secret."""
    line = 'api_key = "live-broker-secret-12345"\n'

    assert scan_secret_lines("a.py", line, {1}) == []


# --- the SAST slice ---------------------------------------------------------


def test_eval_on_non_literal_input_is_flagged() -> None:
    """``eval`` of a runtime value executes attacker-controlled code."""
    findings = scan_python_sast("ralph/mod.py", "def f(value):\n    return eval(value)\n")

    assert "sast-eval" in _categories(findings)


def test_exec_on_non_literal_input_is_flagged() -> None:
    """``exec`` of a runtime value executes attacker-controlled code."""
    findings = scan_python_sast("ralph/mod.py", "def f(value):\n    exec(value)\n")

    assert "sast-eval" in _categories(findings)


def test_eval_on_a_literal_is_allowed() -> None:
    """A literal argument carries no dynamic input."""
    assert scan_python_sast("ralph/mod.py", "def f():\n    return eval('1 + 1')\n") == []


def test_subprocess_with_shell_true_is_flagged() -> None:
    """``shell=True`` hands the command string to a shell."""
    source = "import subprocess\nsubprocess.run('ls', shell=True)\n"

    assert "sast-shell-true" in _categories(scan_python_sast("ralph/mod.py", source))


def test_subprocess_with_an_aliased_import_is_flagged() -> None:
    """An aliased import does not evade the slice."""
    source = "import subprocess as sp\nsp.check_output('ls', shell=True)\n"

    assert "sast-shell-true" in _categories(scan_python_sast("ralph/mod.py", source))


def test_subprocess_without_shell_is_allowed() -> None:
    """An argument vector without a shell is the safe form."""
    source = "import subprocess\nsubprocess.run(['ls'], shell=False)\n"

    assert scan_python_sast("ralph/mod.py", source) == []


def test_the_sast_slice_ignores_test_modules() -> None:
    """Test fixtures that execute generated snippets are out of the slice."""
    source = "def f(value):\n    return eval(value)\n"

    assert scan_python_sast("tests/test_recipe.py", source) == []


# --- CLI contract -----------------------------------------------------------


def test_main_returns_one_when_a_secret_is_planted(tmp_path: Path) -> None:
    """The CLI fails on a planted credential."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "config.py").write_text(_planted_secret_file(), encoding="utf-8")

    assert main([str(root)]) == 1


def test_main_returns_zero_when_clean(tmp_path: Path) -> None:
    """The CLI passes on a clean tree."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "config.py").write_text(_clean_file(), encoding="utf-8")

    assert main([str(root)]) == 0


def test_main_returns_zero_when_only_the_baseline_carries_the_secret(tmp_path: Path) -> None:
    """Baseline-diff mode keeps a pre-existing credential out of the report."""
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    baseline.mkdir()
    current.mkdir()
    (baseline / "config.py").write_text(_planted_secret_file(), encoding="utf-8")
    (current / "config.py").write_text(_planted_secret_file(), encoding="utf-8")

    assert main([str(baseline), str(current)]) == 0


def test_main_returns_two_when_a_root_is_missing(tmp_path: Path) -> None:
    """A missing root is an error, not a silent pass."""
    root = tmp_path / "tree"
    root.mkdir()

    assert main([str(root), str(tmp_path / "absent")]) == 2
