from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.testing.audit_banned_constructs import (
    DEFAULT_GOVERNED_PATHS,
    FIX_HINT,
    main,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_default_governed_paths_scanned_when_no_paths_specified": (
        "assert that pyproject.toml, _driver_rounds.py, pydantic_validation_errors.py, "
        "and banned constructs testing modules are included in DEFAULT_GOVERNED_PATHS"
    ),
}


def _write(root: Path, relative: str, source: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return target


def _run(
    paths: list[Path],
    capsys: pytest.CaptureFixture[str],
    json_output: bool = False,
) -> tuple[int, str, str]:
    arguments: list[str] = []
    for path in paths:
        arguments.extend(("--paths", str(path)))
    if json_output:
        arguments.append("--json")
    code = main(arguments)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


_BANNED_CASES: tuple[tuple[str, str, str], ...] = (
    ("calls", "eval('value')\n", "eval call"),
    ("builtins-calls", "import builtins\nbuiltins.eval('value')\n", "eval call"),
    ("importlib", "import importlib\nimportlib.import_module('pkg')\n", "dynamic importlib"),
    (
        "importlib-from",
        "from importlib import import_module\nimport_module('pkg')\n",
        "dynamic importlib",
    ),
    ("namespaces", "globals()\n", "globals call"),
    ("dynamic-attribute", "name = 'x'\ngetattr(object(), name)\n", "non-literal getattr"),
    ("typing-any", "from typing import Any\nvalue: Any\n", "typing.Any"),
    ("typing-any-alias", "from typing import Any as Value\nvalue: Value\n", "typing.Any"),
    ("typing-cast", "from typing import cast\nvalue = cast(int, 1)\n", "typing.cast"),
    ("type-ignore", "value = 1  # type:" + " ignore[attr-defined]\n", "# type: ignore"),
    ("unsafe-import", "import pickle\n", "pickle import"),
    ("metaclass", "class Item(metaclass=type):\n    pass\n", "metaclass="),
    (
        "dynamic-method",
        "class Item:\n    def __getattr__(self, name: str) -> object:\n        return name\n",
        "__getattr__",
    ),
    (
        "dynamic-getattribute",
        "class Item:\n    def __getattribute__(self, name: str) -> object:\n        return name\n",
        "__getattribute__",
    ),
    (
        "dynamic-setattr",
        "class Item:\n    def __setattr__(self, name: str, value: object) -> None:\n        pass\n",
        "__setattr__",
    ),
    ("type-factory", "Item = type('Item', (), {})\n", "three-argument type"),
    ("inspect", "import inspect\ninspect.signature(object)\n", "inspect"),
    ("monkeypatch", "import pathlib\npathlib.Path.extra = 1\n", "runtime monkeypatch"),
    ("environ", "import os\nvalue = os.environ.get('HOME')\n", "os.environ read"),
    (
        "global",
        "value = 0\ndef update() -> None:\n    global value\n    value = 1\n",
        "global declaration",
    ),
    (
        "nonlocal",
        "def outer() -> None:\n    value = 0\n    def inner() -> None:\n        nonlocal value\n        value = 1\n",
        "nonlocal declaration",
    ),
    (
        "mutable-default",
        "def collect(values: list[int] = []) -> list[int]:\n    return values\n",
        "mutable default",
    ),
    (
        "mutable-default-call",
        "def collect(values: list[int] = list()) -> list[int]:\n    return values\n",
        "mutable default",
    ),
    ("assert", "def validate(value: int) -> None:\n    assert value\n", "runtime assert"),
    ("except", "try:\n    value = 1\nexcept:\n    value = 2\n", "bare except"),
    ("lambda", "identity = lambda value: value\n", "lambda assigned"),
    (
        "annotated-lambda",
        "from collections.abc import Callable\nvalue: Callable[[int], int] = lambda item: item\n",
        "lambda assigned",
    ),
    (
        "passthrough",
        "def wrapper(*args: object) -> object:\n    return target(*args)\n",
        "blind pass-through",
    ),
    ("star-import", "from package import *\n", "star import"),
    ("shell", "import subprocess\nsubprocess.run(['x'], shell=True)\n", "shell=True"),
    ("sys-path", "import sys\nsys.path.append('x')\n", "sys.path edit"),
    ("finalizer", "class Item:\n    def __del__(self) -> None:\n        pass\n", "__del__"),
    ("all-mutation", "__all__ = []\n__all__.append('value')\n", "__all__ mutation"),
    ("all-remove", "__all__ = ['value']\n__all__.remove('value')\n", "__all__ mutation"),
    ("print", "print('value')\n", "print in library"),
    ("sleep", "import time\ntime.sleep(1)\n", "time.sleep"),
    ("thread-state", "import threading\nLOCK = threading.Lock()\n", "module-level shared Lock"),
    (
        "thread-mutable",
        "import threading\nvalues = []\ndef worker() -> None:\n    values.append(1)\nthreading.Thread(target=worker).start()\n",
        "closes over mutable",
    ),
    (
        "inheritance",
        "from ralph.base import Base\nclass Child(Base):\n    pass\n",
        "cross-module inheritance",
    ),
    (
        "type-dispatch",
        "if isinstance(value, (str, int)):\n    result = value\n",
        "tuple-form isinstance",
    ),
    (
        "dynamic-instance",
        "class Item:\n    def set(self, name: str) -> None:\n        setattr(self, name, 1)\n",
        "dynamic instance attribute",
    ),
    (
        "dynamic-instance-assignment",
        "class Item:\n    def set(self) -> None:\n        self.value = 1\n",
        "dynamic instance attribute",
    ),
)


@pytest.mark.parametrize(("family", "source", "expected"), _BANNED_CASES)
def test_each_banned_construct_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    family: str,
    source: str,
    expected: str,
) -> None:
    root = tmp_path / family
    target = _write(root, "ralph/mod.py", source)
    paths = [target]
    if family == "inheritance":
        paths.append(_write(root, "ralph/base.py", "class Base:\n    pass\n"))

    code, output, error = _run(paths, capsys)

    assert code == 1
    assert error == ""
    assert expected in output
    assert FIX_HINT in output


@pytest.mark.parametrize("call", ["exec", "compile", "__import__"])
def test_each_builtin_escape_call_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    call: str,
) -> None:
    arguments = "'value'" if call != "compile" else "'value', 'fixture', 'exec'"
    target = _write(tmp_path, "ralph/mod.py", f"{call}({arguments})\n")

    code, output, _error = _run([target], capsys)

    assert code == 1
    assert f"{call} call" in output


@pytest.mark.parametrize("call", ["locals", "vars"])
def test_each_namespace_escape_call_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    call: str,
) -> None:
    argument = "object()" if call == "vars" else ""
    target = _write(tmp_path, "ralph/mod.py", f"{call}({argument})\n")

    code, output, _error = _run([target], capsys)

    assert code == 1
    assert f"{call} call" in output


@pytest.mark.parametrize("call", ["setattr", "delattr"])
def test_each_non_literal_attribute_call_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    call: str,
) -> None:
    suffix = ", 1" if call == "setattr" else ""
    target = _write(
        tmp_path,
        "ralph/mod.py",
        f"name = 'x'\n{call}(object(), name{suffix})\n",
    )

    code, output, _error = _run([target], capsys)

    assert code == 1
    assert f"non-literal {call}" in output


@pytest.mark.parametrize("module", ["ctypes", "cffi", "marshal"])
def test_each_other_unsafe_import_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    module: str,
) -> None:
    target = _write(tmp_path, "ralph/mod.py", f"import {module}\n")

    code, output, _error = _run([target], capsys)

    assert code == 1
    assert f"{module} import" in output


def test_string_literals_with_construct_names_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = 'TEXT = "eval( cast Any getattr setattr importlib.import_module # type: ignore"\n'
    target = _write(tmp_path, "ralph/mod.py", source)

    code, output, error = _run([target], capsys)

    assert code == 0
    assert output == "No banned constructs found.\n"
    assert error == ""


@pytest.mark.parametrize("relative", ["ralph/adapters/loader.py", "tests/test_loader.py"])
def test_no_path_surface_exempts_banned_calls(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: str,
) -> None:
    target = _write(tmp_path, relative, "eval('value')\n")

    code, output, _error = _run([target], capsys)

    assert code == 1
    assert "eval call" in output


@pytest.mark.parametrize(
    ("directive", "expected"),
    [("# type:" + " ignore", "# type: ignore"), ("# no" + "qa", "# noqa")],
)
def test_inline_suppression_comments_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    directive: str,
    expected: str,
) -> None:
    target = _write(tmp_path, "ralph/mod.py", f"value = 1  {directive}\n")

    code, output, _error = _run([target], capsys)

    assert code == 1
    assert expected in output


def test_monkeypatch_fixture_is_banned_in_test_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "def test_value(monkeypatch: object) -> None:\n    monkeypatch.setattr(object(), 'value', 1)\n"
    target = _write(tmp_path, "tests/test_mod.py", source)

    code, output, _error = _run([target], capsys)

    assert code == 1
    assert "runtime monkeypatch" in output


def test_relative_cross_module_inheritance_is_banned(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = _write(tmp_path, "ralph/base.py", "class Base:\n    pass\n")
    child = _write(
        tmp_path,
        "ralph/child.py",
        "from .base import Base\nclass Child(Base):\n    pass\n",
    )

    code, output, _error = _run([base, child], capsys)

    assert code == 1
    assert "cross-module inheritance" in output


def test_pyproject_extend_ignore_reenabling_banned_rule_is_a_config_bypass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = '[tool.ruff.lint]\nextend-ignore = ["S307"]\n'
    config = _write(tmp_path, "pyproject.toml", source)

    code, output, _error = _run([config], capsys)

    assert code == 1
    assert "CONFIG-BYPASS" in output


def test_mypy_per_module_ignore_errors_is_a_config_bypass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "[mypy-ralph.dynamic]\nignore_errors = True\n"
    config = _write(tmp_path, "mypy.ini", source)

    code, output, _error = _run([config], capsys)

    assert code == 1
    assert "CONFIG-BYPASS" in output


def test_pyproject_per_file_ignore_of_unrelated_rule_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = '[tool.ruff.lint.per-file-ignores]\n"tests/*.py" = ["E501"]\n'
    config = _write(tmp_path, "pyproject.toml", source)

    code, _output, _error = _run([config], capsys)

    assert code == 0


def test_pyproject_extend_ignore_of_unrelated_rule_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = '[tool.ruff.lint]\nextend-ignore = ["E501"]\n'
    config = _write(tmp_path, "pyproject.toml", source)

    code, _output, _error = _run([config], capsys)

    assert code == 0


def test_non_subprocess_shell_keyword_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _write(tmp_path, "ralph/mod.py", "run(command='x', shell=True)\n")

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_inspect_reflection_is_allowed_outside_production(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _write(tmp_path, "tests/test_mod.py", "import inspect\ninspect.signature(object)\n")

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_assertion_is_allowed_in_test_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _write(tmp_path, "tests/test_mod.py", "def test_value() -> None:\n    assert True\n")

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_slotted_class_attribute_assignment_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "class Item:\n    __slots__ = ('value',)\n    def set(self) -> None:\n        self.value = 1\n"
    target = _write(tmp_path, "ralph/mod.py", source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_pyproject_per_file_ignore_is_a_config_bypass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = '[tool.ruff.lint.per-file-ignores]\n"tests/*.py" = ["S101"]\n'
    config = _write(tmp_path, "pyproject.toml", source)

    code, output, _error = _run([config], capsys)

    assert code == 1
    assert "CONFIG-BYPASS" in output


def test_mypy_per_module_relaxation_is_a_config_bypass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "[mypy-ralph.dynamic]\ndisallow_any_expr = False\n"
    config = _write(tmp_path, "mypy.ini", source)

    code, output, _error = _run([config], capsys)

    assert code == 1
    assert "CONFIG-BYPASS" in output


def test_scoped_paths_only_report_selected_findings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = _write(tmp_path, "selected.py", "eval('selected')\n")
    _write(tmp_path, "ignored.py", "exec('ignored')\n")

    code, output, _error = _run([selected], capsys)

    assert code == 1
    assert "selected.py" in output
    assert "ignored.py" not in output


def test_ordinary_identifiers_and_comment_text_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "evaluate = 1\ncasting = 2\nattribute_name = 3\n# eval cast getattr text only\n"
    target = _write(tmp_path, "ralph/mod.py", source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_literal_attribute_lookup_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _write(tmp_path, "ralph/mod.py", "value = getattr(object(), 'real')\n")

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_broad_handler_that_reraises_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "try:\n    value = operation()\nexcept Exception:\n    log_error()\n    raise\n"
    target = _write(tmp_path, "ralph/mod.py", source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_explicit_kwargs_use_without_forwarding_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "def option(**kwargs: str) -> str | None:\n    return kwargs.get('name')\n"
    target = _write(tmp_path, "ralph/mod.py", source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_external_base_class_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "from enum import Enum\nclass Choice(Enum):\n    ONE = 'one'\n"
    target = _write(tmp_path, "ralph/mod.py", source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_plain_all_declaration_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _write(tmp_path, "ralph/mod.py", "__all__ = ['value']\nvalue = 1\n")

    code, _output, _error = _run([target], capsys)

    assert code == 0


@pytest.mark.parametrize(
    "relative",
    [
        "tests/test_wait.py",
        "ralph/cli/output.py",
        "ralph/contrib/output.py",
        "ralph/bench_speed.py",
    ],
)
def test_contextual_production_exemptions_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: str,
) -> None:
    source = "import time\ntime.sleep(0)\n" if relative.startswith("tests/") else "print('ok')\n"
    target = _write(tmp_path, relative, source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_attribute_compile_call_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "import re\nPATTERN = re.compile('value')\n"
    target = _write(tmp_path, "ralph/mod.py", source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_config_env_module_may_read_environ(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "import os\nVALUE = os.environ.get('HOME')\n"
    target = _write(tmp_path, "ralph/config/_env.py", source)

    code, _output, _error = _run([target], capsys)

    assert code == 0


def test_json_output_carries_machine_readable_finding_codes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _write(tmp_path, "ralph/mod.py", "eval('value')\n")

    code, output, _error = _run([target], capsys, json_output=True)
    payload: object = json.loads(output)

    assert code == 1
    assert isinstance(payload, list)
    assert payload
    first: Mapping[str, object] = payload[0]
    assert first["code"] == "BANNED-CALL"
    assert first["fix_hint"] == FIX_HINT


def test_default_governed_paths_scanned_when_no_paths_specified(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert "ralph/checkpoint" in DEFAULT_GOVERNED_PATHS
    assert "ralph/files" in DEFAULT_GOVERNED_PATHS
    expected_governed_paths = (
        "pyproject.toml",
        "ralph/checkpoint",
        "ralph/files",
        "ralph/pipeline/conflict_resolution/_driver_rounds.py",
        "ralph/pydantic_validation_errors.py",
        "ralph/testing/audit_banned_constructs.py",
        "ralph/testing/_banned_constructs_config.py",
        "ralph/testing/_banned_constructs_model.py",
        "ralph/testing/_banned_constructs_scope.py",
        "ralph/testing/_banned_constructs_source.py",
    )
    for path in expected_governed_paths:
        assert path in DEFAULT_GOVERNED_PATHS
    code = main([])
    captured = capsys.readouterr()
    assert code == 0
    assert "Banned constructs audit (advisory)" not in captured.out
    assert "No banned constructs found." in captured.out
