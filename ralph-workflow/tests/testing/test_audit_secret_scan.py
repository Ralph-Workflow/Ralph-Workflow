from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing import audit_secret_scan as secret_scan_gate
from ralph.testing.audit_secret_scan import (
    AWS_ACCESS_KEY_ID_PATTERN,
    GITHUB_FINE_GRAINED_TOKEN_PATTERN,
    GITHUB_TOKEN_PATTERN,
    PRIVATE_KEY_PEM_PATTERN,
    SECRET_SCAN_EXEMPTIONS,
    SLACK_TOKEN_PATTERN,
    SecretFinding,
    audit_secret_scan,
    main,
    scan_secret_lines,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "marker exemption": "the inline marker comment no longer waives a secret-scan finding",
    "structural exemption": "the structural exemption registry now carries the waived value fingerprint",
}
if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_AWS_ACCESS_KEY_ID = "AKIA" + "Z7XY3QWERTY12345"
_GITHUB_CLASSIC_TOKEN = "ghp_" + ("a1b2c3d4e5" * 4)[:36]
_GITHUB_FINE_GRAINED_TOKEN = "github_pat_" + ("z9y8x7w6v5" * 3)[:22]
_SLACK_TOKEN = "xoxb-" + "123456789012-abcdefghijkl"
_PEM_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"
_HIGH_ENTROPY_VALUE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
_ASSIGNMENT_KEY = "api" + "_key"
_SECOND_ASSIGNMENT_KEY = "to" + "ken"

_BANDIT_OWNED_SOURCE = (
    "import subprocess\nsubprocess.run('ls', shell=True)\ndef run(value):\n    return eval(value)\n"
)


def _categories(findings: list[SecretFinding]) -> set[str]:
    return {finding.category for finding in findings}


def _fingerprint_of(value: str) -> str:
    return secret_scan_gate.fingerprint(value)


def _planted_secret_file() -> str:
    return (
        f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'
        f'aws_key = "{_AWS_ACCESS_KEY_ID}"\n'
        f'github_token = "{_GITHUB_CLASSIC_TOKEN}"\n'
        f'fine_grained = "{_GITHUB_FINE_GRAINED_TOKEN}"\n'
        f'slack_token = "{_SLACK_TOKEN}"\n'
        f"{_PEM_HEADER}\n"
    )


def _clean_file() -> str:
    return 'import os\n\n\ndef read_token() -> str:\n    return os.environ["TOKEN"]\n'


def test_planted_secret_in_a_fixture_fails(tmp_path: Path) -> None:
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
    root = tmp_path / "tree"
    root.mkdir()
    (root / "config.py").write_text(_clean_file(), encoding="utf-8")

    assert audit_secret_scan({}, {"config.py": _clean_file()}) == []


def test_the_reported_secret_value_is_never_printed() -> None:
    findings = scan_secret_lines("config.py", _planted_secret_file(), {1})

    assert findings
    for finding in findings:
        assert _HIGH_ENTROPY_VALUE not in finding.detail
        assert _AWS_ACCESS_KEY_ID not in finding.detail
        assert "fingerprint" in finding.detail


def test_a_baseline_secret_is_not_reported() -> None:
    findings = audit_secret_scan(
        {"config.py": _planted_secret_file()},
        {"config.py": _planted_secret_file()},
    )

    assert findings == []


def test_a_changed_line_with_a_secret_is_reported() -> None:
    baseline = {"config.py": f'{_ASSIGNMENT_KEY} = "old-value"\n'}
    current = {"config.py": f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'}

    findings = audit_secret_scan(baseline, current)

    assert "generic-secret" in _categories(findings)


def test_an_unchanged_line_around_a_changed_line_is_not_reported() -> None:
    baseline = {"config.py": f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\nother = 1\n'}
    current = {"config.py": f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\nother = 2\n'}

    findings = audit_secret_scan(baseline, current)

    assert findings == []


def test_a_new_file_is_fully_scanned() -> None:
    findings = audit_secret_scan({}, {"config.py": _planted_secret_file()})

    assert findings


def test_the_bypass_marker_no_longer_suppresses_a_finding() -> None:
    line = f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"  # secret-scan-ok: fixture\n'

    findings = scan_secret_lines("config.py", line, {1})

    assert findings, "an inline marker comment MUST NOT silence the secret scan"


def test_the_bypass_marker_no_longer_silences_any_line() -> None:
    text = (
        f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"  # secret-scan-ok: fixture\n'
        f'{_SECOND_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'
    )

    findings = scan_secret_lines("config.py", text, {1, 2})

    assert [finding.line for finding in findings] == [1, 2], (
        "an inline marker comment MUST NOT silence the secret scan"
    )


def test_a_registered_exemption_suppresses_the_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'
    monkeypatch.setattr(
        secret_scan_gate,
        "SECRET_SCAN_EXEMPTIONS",
        frozenset({("config.py", "generic-secret", _fingerprint_of(_HIGH_ENTROPY_VALUE))}),
    )

    assert scan_secret_lines("config.py", line, {1}) == []


def test_a_registered_exemption_does_not_suppress_another_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'
    monkeypatch.setattr(
        secret_scan_gate,
        "SECRET_SCAN_EXEMPTIONS",
        frozenset({("other.py", "generic-secret", _fingerprint_of(_HIGH_ENTROPY_VALUE))}),
    )

    assert scan_secret_lines("config.py", line, {1}) != []


def test_a_registered_exemption_does_not_suppress_another_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = f'{_ASSIGNMENT_KEY} = "{_HIGH_ENTROPY_VALUE}"\n'
    monkeypatch.setattr(
        secret_scan_gate,
        "SECRET_SCAN_EXEMPTIONS",
        frozenset({("config.py", "aws-access-key-id", _fingerprint_of(_HIGH_ENTROPY_VALUE))}),
    )

    assert scan_secret_lines("config.py", line, {1}) != []


def test_the_exemption_registry_is_empty_by_default() -> None:
    assert frozenset() == SECRET_SCAN_EXEMPTIONS


def test_each_named_detector_fires_on_its_own_shape() -> None:
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
    assert AWS_ACCESS_KEY_ID_PATTERN.search(_AWS_ACCESS_KEY_ID) is not None
    assert GITHUB_TOKEN_PATTERN.search(_GITHUB_CLASSIC_TOKEN) is not None
    assert GITHUB_FINE_GRAINED_TOKEN_PATTERN.search(_GITHUB_FINE_GRAINED_TOKEN) is not None
    assert SLACK_TOKEN_PATTERN.search(_SLACK_TOKEN) is not None
    assert PRIVATE_KEY_PEM_PATTERN.search(_PEM_HEADER) is not None


def test_a_pem_header_inside_a_sentence_is_not_a_finding() -> None:
    line = f'text = "key fragment {_PEM_HEADER} data"\n'

    assert scan_secret_lines("a.py", line, {1}) == []


def test_placeholders_are_never_findings() -> None:
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
    line = 'api_key = "live-broker-secret-12345"\n'

    assert scan_secret_lines("a.py", line, {1}) == []


def test_the_module_carries_no_sast_slice() -> None:
    assert not hasattr(secret_scan_gate, "scan_python_sast")
    assert "scan_python_sast" not in secret_scan_gate.__all__


def test_a_bandit_finding_does_not_fire_from_the_secret_gate() -> None:
    findings = audit_secret_scan({}, {"ralph/mod.py": _BANDIT_OWNED_SOURCE})

    assert findings == []


def test_the_cli_ignores_a_tree_that_only_carries_bandit_findings(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    baseline.mkdir()
    current.mkdir()
    (current / "mod.py").write_text(_BANDIT_OWNED_SOURCE, encoding="utf-8")

    assert main([str(baseline), str(current)]) == 0


def test_main_returns_two_when_no_committed_baseline_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "config.py").write_text(_planted_secret_file(), encoding="utf-8")

    def _absent_head_tree(_root: Path) -> Path | None:
        return None

    monkeypatch.setattr("ralph.testing.audit_secret_scan.head_tree", _absent_head_tree)

    assert main([str(root)]) == 2


def test_main_returns_two_when_the_default_tree_has_no_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def _absent_head_tree(_root: Path) -> Path | None:
        return None

    monkeypatch.setattr("ralph.testing.audit_secret_scan.head_tree", _absent_head_tree)

    assert main([]) == 2


def test_main_returns_one_when_a_secret_is_planted(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    baseline.mkdir()
    current.mkdir()
    (current / "config.py").write_text(_planted_secret_file(), encoding="utf-8")

    assert main([str(baseline), str(current)]) == 1


def test_main_returns_zero_when_clean(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    baseline.mkdir()
    current.mkdir()
    (current / "config.py").write_text(_clean_file(), encoding="utf-8")

    assert main([str(baseline), str(current)]) == 0


def test_main_returns_zero_when_only_the_baseline_carries_the_secret(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    baseline.mkdir()
    current.mkdir()
    (baseline / "config.py").write_text(_planted_secret_file(), encoding="utf-8")
    (current / "config.py").write_text(_planted_secret_file(), encoding="utf-8")

    assert main([str(baseline), str(current)]) == 0


def test_main_returns_two_when_a_root_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()

    assert main([str(root), str(tmp_path / "absent")]) == 2
