from __future__ import annotations

import configparser
import tomllib
from functools import cache
from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "inline suppression policy": "the policy now forbids inline suppressions outright instead of requiring a reason marker",
}
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
MYPY_INI_PATH = REPO_ROOT / "mypy.ini"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
TYPE_IGNORE_POLICY_DOC = (
    WORKSPACE_ROOT / "ralph-workflow" / "docs" / "agents" / "type-ignore-policy.md"
)


def _load_mypy_config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(MYPY_INI_PATH, encoding="utf-8")
    return parser


def _load_pyproject() -> dict[str, object]:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


@cache
def _repo_python_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in REPO_ROOT.rglob("*.py")
        if not any(part.startswith(".venv") for part in path.parts) and "tmp" not in path.parts
    )


def _comment_token(line: str, marker: str) -> bool:
    comment_idx = line.find(marker)
    if comment_idx < 0:
        return False
    before_comment = line[:comment_idx]
    in_single = False
    in_double = False
    index = 0
    while index < len(before_comment):
        if before_comment[index] == "\\":
            index += 2
            continue
        if before_comment[index] == "'" and not in_double:
            in_single = not in_single
        elif before_comment[index] == '"' and not in_single:
            in_double = not in_double
        index += 1
    return not (in_single or in_double)


def test_mypy_requires_error_codes_on_type_ignores() -> None:
    parser = _load_mypy_config()
    enabled_codes = {
        code.strip()
        for code in parser["mypy"].get("enable_error_code", "").split(",")
        if code.strip()
    }

    assert "ignore-without-code" in enabled_codes


def test_ruff_enforces_blanket_type_ignore_rule() -> None:
    pyproject = _load_pyproject()
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    ruff = tool["ruff"]
    assert isinstance(ruff, dict)
    lint = ruff["lint"]
    assert isinstance(lint, dict)
    select = lint["select"]
    assert isinstance(select, list)

    assert "PGH" in select


def test_repo_carries_no_inline_type_ignore_suppressions() -> None:
    offenders: list[str] = []
    for path in _repo_python_files():
        text = path.read_text(encoding="utf-8")
        if "# type:" not in text:
            continue
        rel_path = str(path.relative_to(REPO_ROOT))
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _comment_token(line, "# type:"):
                offenders.append(f"{rel_path}:{line_number}")

    assert offenders == [], (
        "inline type suppressions are banned; suppress in tool configuration "
        f"instead. Found: {offenders}"
    )


def test_repo_carries_no_inline_pyright_directives() -> None:
    offenders: list[str] = []
    for path in _repo_python_files():
        text = path.read_text(encoding="utf-8")
        if "# py" not in text:
            continue
        rel_path = str(path.relative_to(REPO_ROOT))
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _comment_token(line, "# py" + "right:"):
                offenders.append(f"{rel_path}:{line_number}")

    assert offenders == [], (
        "runtime code must not carry directives from py" + f"right. Found: {offenders}"
    )


def test_type_ignore_policy_document_exists() -> None:
    assert TYPE_IGNORE_POLICY_DOC.is_file(), f"Missing policy document: {TYPE_IGNORE_POLICY_DOC}"


def test_type_ignore_policy_document_describes_configuration_governance() -> None:
    text = TYPE_IGNORE_POLICY_DOC.read_text(encoding="utf-8")

    assert "audit_typecheck_bypass" in text
    assert "disable_error_code" in text
    assert "warn_unused_ignores" in text
    assert "disallow_untyped_defs" in text
    assert "ignore_missing_imports" in text


def test_config_authority_mypy_ini_only() -> None:
    pyproject = _load_pyproject()
    tool = pyproject.get("tool")
    assert isinstance(tool, dict), "Expected [tool] section in pyproject.toml"
    mypy_config = tool.get("mypy")
    assert mypy_config is None, (
        "Found [tool.mypy] in pyproject.toml; mypy.ini is the sole authority. "
        f"Config: {mypy_config}"
    )
