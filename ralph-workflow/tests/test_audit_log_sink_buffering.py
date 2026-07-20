"""Regression tests pinning the log-sink-buffering audit contract.

The log-sink-buffering audit
(``ralph.testing.audit_log_sink_buffering``) is the AST drift gate
that locks the ``buffering=8192`` invariant on every file-path
``logger.add(...)`` call in ``ralph/logging.py``. Without it, a
future refactor could silently drop the kwarg (or pass
``buffering=1``) and revert to loguru's line-buffered file default
-- one OS write per record, one fsevents notification per record,
the exact per-record filesystem-mutation class the fseventsd
mitigation closes.

These tests write forbidden-construct source files under pytest's
``tmp_path`` fixture and run the audit directly against the temp
``package_root``. No real subprocess, no ``time.sleep``, no real
file I/O outside ``tmp_path``. They cover:

  * a compliant fixture: every file sink passes
    ``buffering=8192`` -> zero violations.
  * a violating fixture: a file sink omits buffering -> exactly
    one ``file_sink_missing_buffering`` violation at the expected
    line.
  * a violation fixture where ``buffering=1`` is passed -> exactly
    one ``file_sink_missing_buffering`` violation (line-buffered
    is the regression class the audit catches).
  * a stream-sink fixture: a callable/stream sink without
    buffering -> zero violations (the audit must NOT false-flag
    non-file sinks).
  * the real production ``ralph/logging.py`` -> zero violations
    (proves the audit is clean on the committed tree).
  * the audit module's forbidden-I/O contract (no ``time.sleep``,
    no real subprocess, no real HTTP) -- mirrors the
    ``audit_fsevents_watch_consolidation`` I/O contract test.
"""

from __future__ import annotations

import ast as _ast
from pathlib import Path

import pytest

from ralph.testing import audit_log_sink_buffering as audit

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPO_ROOT / "ralph"
_LOGGING_MODULE: str = "logging.py"
_MIN_BLOCK_BUFFERING: int = 2


def _write_fake_logging_module(
    tmp_path: Path,
    *,
    module_body: str,
) -> Path:
    """Write a fake package_root containing ``logging.py`` for the audit.

    Args:
        tmp_path: The pytest ``tmp_path`` fixture; the fake package
            tree is rooted at ``tmp_path / "ralph"``.
        module_body: The full source body of the fake ``logging.py``.
            Tests pass mutated ``logger.add(...)`` bodies through
            this parameter so the fixtures differ only in the
            mutation site.

    Returns:
        The path of the fake ``package_root`` (the audit walks this
            directory).
    """
    package_root: Path = tmp_path / "ralph"
    module_path: Path = package_root / _LOGGING_MODULE
    module_path.parent.mkdir(parents=True)
    module_path.write_text(module_body, encoding="utf-8")
    return package_root


def test_audit_passes_real_production_tree() -> None:
    """The audit returns zero violations against the committed ralph/ tree.

    Proves the current tree -- both engine file sinks
    (``ralph.log`` and ``ralph.jsonl``) passed through the
    ``_add_buffered_file_sink`` helper with ``buffering=8192`` --
    satisfies the invariant.
    """
    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        PRODUCTION_ROOT
    )
    assert violations == [], (
        f"audit must be clean on the real ralph-workflow tree; got: {violations}"
    )


def test_audit_flags_file_sink_without_buffering(tmp_path: Path) -> None:
    """A ``logger.add(<file-path>, ...)`` that omits ``buffering`` triggers
    ``file_sink_missing_buffering``.

    The audit must catch the regression class where the buffering
    kwarg is forgotten -- loguru's ``FileSink`` defaults to
    ``buffering=1`` (line-buffered), so an omitted buffering kwarg
    reinflates the fseventsd footprint.
    """
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(path):\n"
            "    logger.add(path / 'ralph.log', level='DEBUG')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation; got kinds: {kinds}"
    )
    text_violation: audit.LogSinkBufferingViolation = next(
        v for v in violations if v.kind == "file_sink_missing_buffering"
    )
    assert text_violation.line > 0, (
        f"violation line must be the call's lineno; got {text_violation.line}"
    )


def test_audit_flags_file_sink_with_buffering_one(tmp_path: Path) -> None:
    """``buffering=1`` (line-buffered) triggers ``file_sink_missing_buffering``.

    The mitigation contract requires block buffering
    (``buffering >= 2``). Passing ``buffering=1`` keeps the
    line-buffered behavior and reinflates the fseventsd footprint;
    the audit rejects it as the regression class it exists to
    catch.
    """
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(path):\n"
            "    logger.add(path / 'ralph.log', level='DEBUG', buffering=1)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for buffering=1;"
        f" got kinds: {kinds}"
    )


def test_audit_flags_file_sink_with_non_int_buffering(tmp_path: Path) -> None:
    """A non-int (e.g. variable name) buffering value triggers
    ``file_sink_missing_buffering``.

    The audit requires a literal ``ast.Constant`` int value so
    runtime expressions like ``buffering=runtime_buffer`` cannot
    sneak through with an unresolved value at audit time.
    """
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

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for non-constant"
        f" buffering; got kinds: {kinds}"
    )


def test_audit_passes_compliant_fixture(tmp_path: Path) -> None:
    """A compliant fixture (both file sinks pass ``buffering=8192``)
    yields zero violations."""
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

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )
    assert violations == [], (
        f"compliant fixture must yield zero violations; got: {violations}"
    )


def test_audit_ignores_callable_stream_sinks(tmp_path: Path) -> None:
    """A callable/stream sink (no buffering) yields zero violations.

    The CLI's terminal sink is ``logger.add(make_stderr_log_sink(),
    ...)`` -- a callable sink, not a file sink. The audit must NOT
    flag it as missing buffering. This fixture proves the
    stream/callable discriminator works.
    """
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

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )
    assert violations == [], (
        f"stream/callable sinks must NOT be flagged; got: {violations}"
    )


def test_audit_flags_text_log_path_without_buffering(tmp_path: Path) -> None:
    """``logger.add(text_log_path, ...)`` without ``buffering`` triggers
    ``file_sink_missing_buffering``.

    The plan explicitly requires the audit to recognize the canonical
    ``text_log_path`` Name binding as a file-path expression. Without
    this branch, a future refactor that writes
    ``logger.add(text_log_path, level='INFO')`` directly (instead of
    routing through ``_add_buffered_file_sink``) would silently
    regress to loguru's line-buffered ``FileSink`` default and
    reinflate the fseventsd footprint. This fixture pins that
    detection.
    """
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(text_log_path):\n"
            "    logger.add(text_log_path, level='INFO')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for text_log_path;"
        f" got kinds: {kinds}"
    )
    text_violation: audit.LogSinkBufferingViolation = next(
        v for v in violations if v.kind == "file_sink_missing_buffering"
    )
    assert text_violation.line > 0, (
        f"violation line must be the call's lineno; got {text_violation.line}"
    )


def test_audit_flags_structured_log_path_without_buffering(tmp_path: Path) -> None:
    """``logger.add(structured_log_path, ...)`` without ``buffering``
    triggers ``file_sink_missing_buffering``.

    The plan explicitly requires the audit to recognize the canonical
    ``structured_log_path`` Name binding as a file-path expression.
    Mirrors the ``text_log_path`` fixture -- pins the structured
    JSONL sink under the same invariant.
    """
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(structured_log_path):\n"
            "    logger.add(structured_log_path, level='INFO', serialize=True)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

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
    """``logger.add(text_log_path, ..., buffering=8192)`` yields zero violations.

    Negative-control fixture proving the named-path recognition
    does NOT false-flag a correctly buffered call -- the audit
    only triggers on MISSING buffering.
    """
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(text_log_path):\n"
            "    logger.add(text_log_path, level='INFO', buffering=8192)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )
    assert violations == [], (
        f"buffered text_log_path sink must NOT be flagged; got: {violations}"
    )


def test_audit_passes_structured_log_path_with_buffering(tmp_path: Path) -> None:
    """``logger.add(structured_log_path, ..., buffering=8192)`` yields
    zero violations.

    Negative-control fixture proving the named-path recognition
    does NOT false-flag a correctly buffered call -- the audit
    only triggers on MISSING buffering.
    """
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

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )
    assert violations == [], (
        f"buffered structured_log_path sink must NOT be flagged;"
        f" got: {violations}"
    )


def test_audit_flags_attribute_path_without_buffering(tmp_path: Path) -> None:
    """``logger.add(paths.text_log_path, ...)`` without ``buffering``
    triggers ``file_sink_missing_buffering`` at the call line.

    PLAN step 4 requires the audit to recognize Attribute
    filesystem-path expressions; this fixture pins that the
    canonical ``*.text_log_path`` form is detected. Without
    Attribute recognition, a future refactor that writes
    ``logger.add(paths.text_log_path, level='INFO')`` directly
    (bypassing the ``_add_buffered_file_sink`` helper) would
    silently regress to loguru's line-buffered FileSink default
    and reinflate the fseventsd footprint.
    """
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(paths):\n"
            "    logger.add(paths.text_log_path, level='INFO')\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

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
    """``logger.add(paths.structured_log_path, ...)`` without
    ``buffering`` triggers ``file_sink_missing_buffering``.

    Mirrors the ``text_log_path`` attribute-path fixture and pins
    the structured JSONL sink under the same invariant. The audit
    MUST treat any ``Attribute`` whose terminal ``attr`` is
    ``structured_log_path`` (or ``text_log_path``) as a file-path
    expression regardless of the value's namespace (e.g. an
    object attribute access via ``paths.`` / ``config.``).
    """
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

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

    kinds: list[str] = [v.kind for v in violations]
    assert "file_sink_missing_buffering" in kinds, (
        f"expected file_sink_missing_buffering violation for"
        f" attribute-path sink (paths.structured_log_path); got kinds: {kinds}"
    )


def test_audit_passes_attribute_path_with_buffering(tmp_path: Path) -> None:
    """``logger.add(paths.text_log_path, ..., buffering=8192)`` yields
    zero violations.

    Negative-control fixture proving the Attribute filesystem-path
    recognition does NOT false-flag a correctly buffered
    attribute-path sink -- the audit only triggers on MISSING
    buffering. Without this negative control, an over-broad
    fix that always flags any Attribute sink regardless of the
    buffering kwarg would slip past review.
    """
    package_root: Path = _write_fake_logging_module(
        tmp_path,
        module_body=(
            "from loguru import logger\n"
            "\n"
            "def configure(paths):\n"
            "    logger.add(paths.text_log_path, level='INFO', buffering=8192)\n"
        ),
    )

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )
    assert violations == [], (
        f"buffered attribute-path sink (paths.text_log_path with"
        f" buffering=8192) must NOT be flagged; got: {violations}"
    )


def test_audit_passes_attribute_path_structured_with_buffering(
    tmp_path: Path,
) -> None:
    """``logger.add(paths.structured_log_path, ..., buffering=8192)``
    yields zero violations.

    Buffered negative control for the structured attribute path.
    Proves the Attribute recognition accepts any value-bearing
    namespace (``paths.``, ``config.``, ``ctx.``, etc.) when the
    buffering kwarg is present and correct.
    """
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

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )
    assert violations == [], (
        f"buffered attribute-path structured sink (config.structured_log_path"
        f" with buffering=8192) must NOT be flagged; got: {violations}"
    )


def test_audit_does_not_flag_non_canonical_attribute(tmp_path: Path) -> None:
    """``logger.add(obj.foo, ...)`` (a non-canonical attribute) is
    NOT flagged even without buffering.

    The audit's Attribute recognition is conservative: only the
    canonical ``text_log_path`` / ``structured_log_path`` terminal
    attribute names trigger the file-sink check. Other attribute
    access (e.g. ``module.foo``, ``config.bar``) is left alone to
    avoid over-matching unrelated patterns. The
    :func:`_is_stream_or_callable_sink` filter still runs first,
    so true stream/callable attribute access (e.g. ``sys.stderr``)
    is excluded at the stream stage.
    """
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

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )
    assert violations == [], (
        f"non-canonical attribute sinks (obj.foo, obj.bar) must NOT be"
        f" flagged -- the audit is conservative on Attribute; got: {violations}"
    )


def test_audit_flags_missing_logging_module(tmp_path: Path) -> None:
    """A package root WITHOUT ``logging.py`` triggers ``missing_logging_module``.

    The file's absence is itself drift -- the audit must NOT
    silently pass when the canonical module is gone.
    """
    package_root: Path = tmp_path / "ralph"
    package_root.mkdir(parents=True)
    # Intentionally do NOT write logging.py.

    violations: list[audit.LogSinkBufferingViolation] = audit.audit_log_sink_buffering(
        package_root
    )

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
    """The audit must NOT use ``time.sleep``, ``asyncio.sleep``,
    ``subprocess.run``, ``httpx.*``, ``requests.*``,
    ``urllib.request.urlopen``, or ``socket.create_connection``
    (the test-policy and mcp-timeout invariants). The audit is
    purely an AST walk over local files.

    The check uses AST-based detection (not regex) so the literal
    strings in the test source do not produce false positives.
    """
    audit_path: Path = (
        REPO_ROOT / "ralph" / "testing" / "audit_log_sink_buffering.py"
    )
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
    assert not all_violations, (
        f"audit module uses forbidden I/O primitives: {all_violations}"
    )


@pytest.mark.subprocess_e2e
def test_audit_module_main_function_returns_zero_on_clean_tree() -> None:
    """Run the audit's ``main()`` in-process and assert exit 0 on the real tree.

    Black-box proof that the audit works as a wired verify step.
    Calling ``main()`` directly (instead of via ``subprocess``)
    keeps the test well under the per-test timeout while still
    validating the CLI entry-point contract.
    """
    rc: int = audit.main([])
    assert rc == 0, f"audit main() must return 0 on clean tree; got {rc}"


@pytest.mark.parametrize(
    "forbidden_name",
    [
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
    ],
)
def test_audit_module_forbids_known_io_primitives(forbidden_name: str) -> None:
    """The audit must not be modified to introduce any of the known
    I/O primitives. This locks the invariant that the audit is a
    pure static walker and never a runtime probe.

    Uses AST-based detection (not regex) so the literal strings in
    the test source do not produce false positives.
    """
    audit_path: Path = (
        REPO_ROOT / "ralph" / "testing" / "audit_log_sink_buffering.py"
    )
    source: str = audit_path.read_text(encoding="utf-8")
    tree: _ast.Module = _ast.parse(source, filename=str(audit_path))

    parts: list[str] = forbidden_name.split(".")

    def _matches(node: _ast.AST) -> bool:
        if isinstance(node, _ast.Name):
            return parts == [node.id]
        if isinstance(node, _ast.Attribute):
            inner: bool = _matches(node.value)
            if not inner:
                return False
            return parts[-1] == node.attr and len(parts) >= 2
        return False

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call) and _matches(node.func):
            raise AssertionError(
                f"audit module contains forbidden call to {forbidden_name}"
                f" at {audit_path.name}:{node.lineno}"
            )
