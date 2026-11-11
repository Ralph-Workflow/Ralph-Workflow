from __future__ import annotations

import configparser
from pathlib import Path

_PYTEST_INI = Path(__file__).resolve().parents[1] / "pytest.ini"


def _ini_section() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(_PYTEST_INI, encoding="utf-8")
    return parser["pytest"]


def test_passing_tests_do_not_retain_their_temp_trees() -> None:
    assert _ini_section()["tmp_path_retention_policy"] == "failed"


def test_only_the_current_session_directory_is_retained() -> None:
    assert _ini_section()["tmp_path_retention_count"] == "1"


def test_failing_tests_still_keep_their_temp_tree_for_debugging() -> None:
    assert _ini_section()["tmp_path_retention_policy"] != "none"
