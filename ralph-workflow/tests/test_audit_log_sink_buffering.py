from __future__ import annotations

import ast as _ast
from pathlib import Path

from ralph.testing import audit_log_sink_buffering as audit

REPO_ROOT = Path(__file__).resolve().parents[1]
_LOGGING_MODULE: str = "logging.py"
_MIN_BLOCK_BUFFERING: int = 2


def _write_fake_logging_module(
    tmp_path: Path,
    *,
    module_body: str,
) -> Path:
    package_root: Path = tmp_path / "ralph"
    module_path: Path = package_root / _LOGGING_MODULE
    module_path.parent.mkdir(parents=True)
    module_path.write_text(module_body, encoding="utf-8")
    return package_root


def test_audit_flags_file_sink_without_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(path):\n"
            "    logger.add(path / 'ralph.log', level='DEBUG')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    assert len(violations) == 1, f"expected exactly one violation; got {violations}"
    text_violation: audit.LogSinkBufferingViolation = violations[0]
    assert text_violation.kind == "file_sink_missing_buffering"
    assert text_violation.line == 4, (
        f"violation line must be the fixture call line; got {text_violation.line}"
    )


def test_audit_log_sink_buffering_regression_helper_missing_buffering(
    tmp_path: Path,
) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "_FILE_SINK_BUFFER_BYTES = 8192\n"
            "\n"
            "def _add_buffered_file_sink(adder, path):\n"
            "    return adder(str(path), level='INFO')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    assert len(violations) == 1, f"expected exactly one violation; got {violations}"
    helper_violation: audit.LogSinkBufferingViolation = violations[0]
    assert helper_violation.kind == "file_sink_missing_buffering"
    assert helper_violation.line == 5, (
        f"violation line must be the helper adder call; got {helper_violation.line}"
    )


def test_audit_flags_file_sink_with_buffering_one(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(path):\n"
            "    logger.add(path / 'ralph.log', level='DEBUG', buffering=1)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for buffering=1; got kinds: {kinds}"
    )


def test_audit_flags_file_sink_with_non_int_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "RUNTIME_BUFFER = 8192\n"
            "\n"
            "def configure(path):\n"
            "    logger.add(path / 'ralph.log', level='DEBUG', buffering=RUNTIME_BUFFER)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for non-constant"
        f" buffering; got kinds: {kinds}"
    )


def test_audit_passes_compliant_fixture(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(path):\n"
            "    logger.add(path / 'ralph.log', level='DEBUG', buffering=8192)\n"
            "    logger.add(path / 'ralph.jsonl', level='DEBUG', buffering=8192)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)
    assert violations == [], f"compliant fixture must yield zero violations; got: {violations}"


def test_audit_ignores_callable_stream_sinks(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def make_stderr_log_sink():\n"
            "    return lambda msg: None\n"
            "\n"
            "def configure():\n"
            "    sink = make_stderr_log_sink()\n"
            "    logger.add(sink, level='DEBUG')\n"
            "    logger.add(make_stderr_log_sink(), level='INFO')\n"
            "    import sys\n"
            "    logger.add(sys.stderr, level='ERROR')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)
    assert violations == [], f"stream/callable sinks must NOT be flagged; got: {violations}"


def test_audit_flags_text_log_path_without_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(text_log_path):\n"
            "    logger.add(text_log_path, level='INFO')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for text_log_path; got kinds: {kinds}"
    )
    text_violation: audit.LogSinkBufferingViolation = next(
        v for v in violations if v.kind == "file_sink_missing_buffering"
    )
    assert text_violation.line > 0, (
        f"violation line must be the call's lineno; got {text_violation.line}"
    )


def test_audit_flags_structured_log_path_without_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(structured_log_path):\n"
            "    logger.add(structured_log_path, level='INFO', serialize=True)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for"
        f" structured_log_path; got kinds: {kinds}"
    )
    text_violation: audit.LogSinkBufferingViolation = next(
        v for v in violations if v.kind == "file_sink_missing_buffering"
    )
    assert text_violation.line > 0, (
        f"violation line must be the call's lineno; got {text_violation.line}"
    )


def test_audit_passes_text_log_path_with_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(text_log_path):\n"
            "    logger.add(text_log_path, level='INFO', buffering=8192)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)
    assert violations == [], f"buffered text_log_path sink must NOT be flagged; got: {violations}"


def test_audit_passes_structured_log_path_with_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(structured_log_path):\n"
            "    logger.add(structured_log_path, level='INFO', serialize=True,"
            " buffering=8192)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)
    assert violations == [], (
        f"buffered structured_log_path sink must NOT be flagged; got: {violations}"
    )


def test_audit_flags_attribute_path_without_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(paths):\n"
            "    logger.add(paths.text_log_path, level='INFO')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for"
        f" attribute-path sink (paths.text_log_path); got kinds: {kinds}"
    )
    text_violation: audit.LogSinkBufferingViolation = next(
        v for v in violations if v.kind == "file_sink_missing_buffering"
    )
    assert text_violation.line > 0, (
        f"violation line must be the call's lineno; got {text_violation.line}"
    )


def test_audit_flags_attribute_path_structured_log_path_without_buffering(
    tmp_path: Path,
) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(paths):\n"
            "    logger.add(paths.structured_log_path, level='INFO',"
            " serialize=True)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for"
        f" attribute-path sink (paths.structured_log_path); got kinds: {kinds}"
    )


def test_audit_passes_attribute_path_with_buffering(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(paths):\n"
            "    logger.add(paths.text_log_path, level='INFO', buffering=8192)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)
    assert violations == [], (
        f"buffered attribute-path sink (paths.text_log_path with"
        f" buffering=8192) must NOT be flagged; got: {violations}"
    )


def test_audit_passes_attribute_path_structured_with_buffering(
    tmp_path: Path,
) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(config):\n"
            "    logger.add(config.structured_log_path, level='INFO',"
            " serialize=True, buffering=8192)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)
    assert violations == [], (
        f"buffered attribute-path structured sink (config.structured_log_path"
        f" with buffering=8192) must NOT be flagged; got: {violations}"
    )


def test_audit_does_not_flag_non_canonical_attribute(tmp_path: Path) -> None:
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(obj):\n"
            "    logger.add(obj.foo, level='INFO')\n"
            "    logger.add(obj.bar, level='INFO')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)
    assert violations == [], (
        f"non-canonical attribute sinks (obj.foo, obj.bar) must NOT be"
        f" flagged -- the audit is conservative on Attribute; got: {violations}"
    )


def test_audit_flags_missing_logging_module(tmp_path: Path) -> None:
    package_root: Path = tmp_path / "ralph"
    package_root.mkdir(parents=True)

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(package_root)

    kinds: list[str] = [v.kind for v in violations]
    assert "missing_logging_module" in kinds, (
        f"expected missing_logging_module violation; got kinds: {kinds}"
    )


def _dotted(node: _ast.AST) -> str | None:
    if isinstance(node, _ast.Name):
        return node.id
    if isinstance(node, _ast.Attribute):
        base: str | None = _dotted(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def test_audit_module_imports_clean() -> None:
    audit_path: Path = REPO_ROOT / "ralph" / "testing" / "audit_log_sink_buffering.py"
    source: str = audit_path.read_text(encoding="utf-8")
    tree: _ast.Module = _ast.parse(source, filename=str(audit_path))

    forbidden_calls: dict[str, list[int]] = {
        "time.sleep": [],
        "asyncio.sleep": [],
        "subprocess.run": [],
        "subprocess.Popen": [],
        "subprocess.call": [],
        "subprocess.check_output": [],
        "urllib.request.urlopen": [],
        "socket.create_connection": [],
    }
    forbidden_attrs: dict[str, list[int]] = {
        "httpx": [],
        "requests": [],
    }

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            name: str | None = _dotted(node.func)
            if name is None:
                continue
            if name in forbidden_calls:
                forbidden_calls[name].append(node.lineno)
        elif isinstance(node, _ast.Attribute):
            value_name: str | None = _dotted(node.value)
            if value_name in forbidden_attrs:
                forbidden_attrs[value_name].append(node.lineno)

    all_violations: list[str] = []
    for name, lines in forbidden_calls.items():
        all_violations.extend(f"{audit_path.name}:{lineno}: call to {name}" for lineno in lines)
    for name, lines in forbidden_attrs.items():
        all_violations.extend(
            f"{audit_path.name}:{lineno}: attribute access on {name}" for lineno in lines
        )
    assert not all_violations, f"audit module uses forbidden I/O primitives: {all_violations}"


def test_audit_module_forbids_known_io_primitives() -> None:
    audit_path: Path = REPO_ROOT / "ralph" / "testing" / "audit_log_sink_buffering.py"
    source: str = audit_path.read_text(encoding="utf-8")
    tree: _ast.Module = _ast.parse(source, filename=str(audit_path))

    def _dotted_name(node: _ast.AST) -> str | None:
        if isinstance(node, _ast.Name):
            return node.id
        if isinstance(node, _ast.Attribute):
            parent = _dotted_name(node.value)
            return f"{parent}.{node.attr}" if parent is not None else None
        return None

    forbidden_names = {
        "time.sleep",
        "asyncio.sleep",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_output",
        "urllib.request.urlopen",
        "socket.create_connection",
        "httpx.get",
        "requests.get",
    }
    violations = [
        f"{_dotted_name(node.func)} at {audit_path.name}:{node.lineno}"
        for node in _ast.walk(tree)
        if isinstance(node, _ast.Call) and _dotted_name(node.func) in forbidden_names
    ]
    assert not violations, f"audit module contains forbidden calls: {violations}"
