"""Tests pinning the first-import cycle audit.

The audit (``ralph.testing.audit_import_order``) is the gate that keeps the
``make install`` breakage from recurring: ``ralph.install`` imported
``ralph.display`` first, and a cycle through ``ralph.recovery.controller`` and
the conflict-resolution package re-entered ``ralph.display.parallel_display``
while it was still executing, so ``from ralph.display.parallel_display import
...`` raised ``ImportError: cannot import name ... from partially initialized
module``. The test suite never saw it because ``conftest.py`` fixes the import
order, which is why the audit replays the import machinery once per module as
the FIRST import of a fresh interpreter.

Every "flags" case below is a fake package that a fresh ``python -I`` really
fails to import with that ``ImportError``, and every "allows" case is one it
really imports cleanly. The gate is only worth having if it agrees with the
interpreter, so the cases are written from the interpreter's behaviour rather
than from the rule's wording. Everything goes through the in-memory
``audit_sources`` seam: nothing is written, imported, or executed.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.executor.process import ProcessResult
from ralph.testing import audit_import_order
from ralph.testing.audit_import_order import AuditParseError, Violation
from ralph.verify import run_verify

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_IMPORT_ORDER_STEP_LABEL = "import order audit (audit_import_order)"
_IMPORT_ORDER_STEP_ARGS = ("run", "python", "-m", "ralph.testing.audit_import_order")

_GETATTR_HOOK = """
def __getattr__(name: str) -> int:
    if name == "y":
        return 2
    raise AttributeError(name)
"""


def _tree(files: Mapping[str, str]) -> dict[str, str]:
    """Return a fake ``ralph.pkg`` source tree keyed by repo-relative path.

    ``ralph/__init__.py`` and ``ralph/pkg/__init__.py`` default to empty, so a
    case only spells out the modules it is about. Bodies are dedented and
    their leading newline stripped, so line 1 is the first line written.
    """
    sources = {"ralph/__init__.py": "", "ralph/pkg/__init__.py": ""}
    for path, body in files.items():
        sources[f"ralph/{path}"] = textwrap.dedent(body).lstrip("\n")
    return sources


def _audit(files: Mapping[str, str]) -> list[Violation]:
    return audit_import_order.audit_sources(_tree(files))


def _sites(violations: Sequence[Violation]) -> list[tuple[str, int, str, str]]:
    """Return each violation's failing statement: importer, line, target, name."""
    return [(item.importer, item.lineno, item.target, item.name) for item in violations]


# --- the basic two-module cycle ------------------------------------------------


@pytest.mark.parametrize(
    ("a_imports_b", "b_imports_a"),
    [
        ("from ralph.pkg.b import x", "from ralph.pkg.a import y"),
        ("from .b import x", "from .a import y"),
    ],
    ids=["absolute", "relative"],
)
def test_flags_a_cycle_entered_from_the_side_whose_name_is_not_yet_bound(
    a_imports_b: str, b_imports_a: str
) -> None:
    """``import ralph.pkg.a`` fails; ``import ralph.pkg.b`` alone succeeds."""
    violations = _audit(
        {
            "pkg/a.py": f"{a_imports_b}\ny = 2\n",
            "pkg/b.py": f"x = 1\n{b_imports_a}\n",
        }
    )

    assert violations == [
        Violation(
            entry="ralph.pkg.a",
            importer="ralph.pkg.b",
            lineno=2,
            target="ralph.pkg.a",
            name="y",
            chain=("ralph.pkg.a", "ralph.pkg.b"),
        )
    ]


def test_violation_report_names_the_entry_statement_and_chain() -> None:
    violations = _audit(
        {
            "pkg/a.py": "from ralph.pkg.b import x\ny = 2\n",
            "pkg/b.py": "x = 1\nfrom ralph.pkg.a import y\n",
        }
    )

    report = str(violations[0])

    assert "importing ralph.pkg.a first fails: ralph.pkg.b:2" in report
    assert "`from ralph.pkg.a import y` runs while ralph.pkg.a is partially initialised" in report
    assert "chain: ralph.pkg.a -> ralph.pkg.b" in report


def test_flags_each_side_of_a_cycle_that_binds_both_names_late() -> None:
    """Both entry points fail, at two different statements."""
    violations = _audit(
        {
            "pkg/a.py": "from ralph.pkg.b import x\ny = 2\n",
            "pkg/b.py": "from ralph.pkg.a import y\nx = 1\n",
        }
    )

    assert _sites(violations) == [
        ("ralph.pkg.b", 1, "ralph.pkg.a", "y"),
        ("ralph.pkg.a", 1, "ralph.pkg.b", "x"),
    ]
    assert [item.entry for item in violations] == ["ralph.pkg.a", "ralph.pkg.b"]


def test_allows_the_cycle_when_the_needed_name_is_bound_before_the_cycling_import() -> None:
    violations = _audit(
        {
            "pkg/a.py": "y = 2\nfrom ralph.pkg.b import x\n",
            "pkg/b.py": "x = 1\nfrom ralph.pkg.a import y\n",
        }
    )

    assert violations == []


@pytest.mark.parametrize(
    ("statement", "reference"),
    [
        ("from ralph.pkg import {mod}", "{mod}"),
        ("from . import {mod}", "{mod}"),
        ("import ralph.pkg.{mod}", "ralph.pkg.{mod}"),
        ("import ralph.pkg.{mod} as {mod}", "{mod}"),
    ],
    ids=["from-package", "relative-from-package", "dotted-import", "dotted-import-as"],
)
def test_allows_a_cycle_that_binds_the_module_and_reads_names_at_call_time(
    statement: str, reference: str
) -> None:
    """A still-executing submodule is found through ``sys.modules``."""
    a_source = f"""
        {statement.format(mod="b")}


        def y() -> int:
            return {reference.format(mod="b")}.x() + 1
        """
    b_source = f"""
        {statement.format(mod="a")}


        def x() -> int:
            return 1


        def z() -> int:
            return {reference.format(mod="a")}.y()
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": b_source})

    assert violations == []


# --- the package-__init__ shape behind the make-install breakage --------------


_DISPLAY_LEAF = """
class Panel:
    pass
"""

_DISPLAY_STATUS = """
from ralph.pkg.controller import run


def show() -> str:
    return run()
"""

_CONTROLLER = """
from ralph.pkg.display.panel import Panel


def run() -> str:
    return Panel.__name__
"""


def test_flags_a_leaf_import_whose_package_init_re_enters_the_importer() -> None:
    """Importing the leaf runs ``display/__init__.py`` first, which loops back.

    ``panel`` imports nothing, yet ``import ralph.pkg.controller`` fails:
    the package ``__init__`` re-exports ``status``, and ``status`` imports
    ``run`` from the controller that is still on its first line.
    """
    violations = _audit(
        {
            "pkg/display/__init__.py": "from ralph.pkg.display.status import show\n",
            "pkg/display/panel.py": _DISPLAY_LEAF,
            "pkg/display/status.py": _DISPLAY_STATUS,
            "pkg/controller.py": _CONTROLLER,
        }
    )

    assert violations == [
        Violation(
            entry="ralph.pkg.controller",
            importer="ralph.pkg.display.status",
            lineno=1,
            target="ralph.pkg.controller",
            name="run",
            chain=("ralph.pkg.controller", "ralph.pkg.display", "ralph.pkg.display.status"),
        )
    ]


def test_allows_the_same_leaf_import_when_the_package_init_is_empty() -> None:
    violations = _audit(
        {
            "pkg/display/__init__.py": "",
            "pkg/display/panel.py": _DISPLAY_LEAF,
            "pkg/display/status.py": _DISPLAY_STATUS,
            "pkg/controller.py": _CONTROLLER,
        }
    )

    assert violations == []


# --- statements that never run at import time ---------------------------------


# ``b`` reads ``y`` from ``a`` before binding ``x``. Entered via ``b``, any
# import of ``x`` that ``a`` RUNS at import time fails, so pairing it with an
# ``a`` that imports ``x`` only where it never runs proves the audit skipped
# that statement.
_B_NEEDS_A_FIRST = "from ralph.pkg.a import y\nx = 1\n"

_TYPE_CHECKING_GUARDS = pytest.mark.parametrize(
    ("header", "guard"),
    [
        ("from typing import TYPE_CHECKING", "TYPE_CHECKING"),
        ("import typing", "typing.TYPE_CHECKING"),
    ],
    ids=["name", "attribute"],
)


@_TYPE_CHECKING_GUARDS
def test_ignores_imports_under_a_type_checking_guard(header: str, guard: str) -> None:
    a_source = f"""
        {header}


        def y() -> int:
            return 1


        if {guard}:
            from ralph.pkg.b import x
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert violations == []


@_TYPE_CHECKING_GUARDS
def test_flags_imports_in_the_else_of_a_type_checking_guard(header: str, guard: str) -> None:
    """The ``else:`` arm is exactly the code that runs at import time."""
    a_source = f"""
        {header}


        def y() -> int:
            return 1


        if {guard}:
            pass
        else:
            from ralph.pkg.b import x
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert _sites(violations) == [("ralph.pkg.a", 11, "ralph.pkg.b", "x")]
    assert violations[0].chain == ("ralph.pkg.b", "ralph.pkg.a")


@pytest.mark.parametrize(
    "a_source",
    [
        """
        def y() -> int:
            from ralph.pkg.b import x

            return x
        """,
        """
        async def y() -> int:
            from ralph.pkg.b import x

            return x
        """,
    ],
    ids=["function", "async-function"],
)
def test_ignores_imports_inside_functions(a_source: str) -> None:
    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert violations == []


def test_ignores_imports_under_a_main_guard() -> None:
    a_source = """
        def y() -> int:
            return 1


        if __name__ == "__main__":
            from ralph.pkg.b import x
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert violations == []


@pytest.mark.parametrize(
    "a_source",
    [
        """
        def y() -> int:
            return 1


        try:
            from ralph.pkg.b import x
        finally:
            pass
        """,
        """
        def y() -> int:
            return 1


        for _ in range(1):
            from ralph.pkg.b import x
        """,
    ],
    ids=["try-body", "loop-body"],
)
def test_flags_imports_nested_in_blocks_that_run_at_import_time(a_source: str) -> None:
    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert [(item.importer, item.target, item.name) for item in violations] == [
        ("ralph.pkg.a", "ralph.pkg.b", "x")
    ]


# --- PEP 562 module __getattr__ -----------------------------------------------


_B_BINDS_X_THEN_READS_Y = "x = 1\nfrom ralph.pkg.a import y\n"


def test_allows_a_partially_initialised_module_that_already_defines_getattr() -> None:
    """``from a import y`` falls back to ``a.__getattr__("y")``."""
    a_source = f"{_GETATTR_HOOK.lstrip()}\n\nfrom ralph.pkg.b import x\n"

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_BINDS_X_THEN_READS_Y})

    assert violations == []


def test_flags_a_module_whose_getattr_is_defined_after_the_cycling_import() -> None:
    """A hook that is not defined yet cannot answer the lookup."""
    a_source = f"from ralph.pkg.b import x\n\n{_GETATTR_HOOK}"

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_BINDS_X_THEN_READS_Y})

    assert _sites(violations) == [("ralph.pkg.b", 2, "ralph.pkg.a", "y")]


# --- relative imports ---------------------------------------------------------


def test_resolves_relative_imports_from_a_package_init_and_a_plain_module() -> None:
    """``from .a`` in ``ralph/pkg/__init__.py`` is ``ralph.pkg.a``, not ``ralph.a``.

    ``from . import helper`` in ``ralph/pkg/a.py`` reads ``helper`` off
    ``ralph.pkg``, which is still executing its first line.
    """
    violations = _audit(
        {
            "pkg/__init__.py": "from .a import y\nhelper = 1\n",
            "pkg/a.py": "from . import helper\ny = 2\n",
        }
    )

    assert violations == [
        Violation(
            entry="ralph.pkg",
            importer="ralph.pkg.a",
            lineno=1,
            target="ralph.pkg",
            name="helper",
            chain=("ralph.pkg", "ralph.pkg.a"),
        )
    ]


def test_allows_relative_imports_once_the_package_binds_the_name_first() -> None:
    violations = _audit(
        {
            "pkg/__init__.py": "helper = 1\nfrom .a import y\n",
            "pkg/a.py": "from . import helper\ny = 2\n",
        }
    )

    assert violations == []


def test_allows_a_package_init_that_imports_its_submodules_relatively() -> None:
    """``from . import a`` names a submodule, which the import system loads."""
    violations = _audit(
        {
            "pkg/__init__.py": "from . import a, b\n",
            "pkg/a.py": "from . import b\n\n\ndef y() -> int:\n    return b.x()\n",
            "pkg/b.py": "from . import a\n\n\ndef x() -> int:\n    return 1\n",
        }
    )

    assert violations == []


def test_resolves_parent_relative_imports_across_subpackages() -> None:
    violations = _audit(
        {
            "pkg/a.py": "from .sub.c import z\ny = 2\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/c.py": "from ..a import y\nz = 3\n",
        }
    )

    assert _sites(violations) == [
        ("ralph.pkg.sub.c", 1, "ralph.pkg.a", "y"),
        ("ralph.pkg.a", 1, "ralph.pkg.sub.c", "z"),
    ]


# --- scope and reporting ------------------------------------------------------


def test_ignores_imports_of_modules_outside_the_audited_tree() -> None:
    """Non-``ralph`` modules never import ``ralph`` back, so they are opaque."""
    violations = _audit(
        {
            "pkg/a.py": """
                import json
                from collections import OrderedDict
                from rich.console import Console

                from ralph.pkg.b import x

                y = 2
                """,
            "pkg/b.py": """
                import os.path
                from email.mime import text
                from rich.text import Text

                x = 1
                """,
        }
    )

    assert violations == []


def test_reports_each_failing_statement_once_for_the_first_entry_point() -> None:
    """``a``, ``c`` and ``d`` all fail at the same line of ``b``."""
    violations = _audit(
        {
            "pkg/a.py": "from ralph.pkg.b import x\ny = 2\n",
            "pkg/b.py": "x = 1\nfrom ralph.pkg.a import y\n",
            "pkg/c.py": "from ralph.pkg.a import y\n",
            "pkg/d.py": "import ralph.pkg.a\n",
        }
    )

    assert _sites(violations) == [("ralph.pkg.b", 2, "ralph.pkg.a", "y")]
    assert [item.entry for item in violations] == ["ralph.pkg.a"]


def test_unparseable_source_raises_rather_than_reporting_clean() -> None:
    """A module the audit cannot read must never be counted as cleared."""
    sources = _tree({"pkg/a.py": "y = 2\n", "pkg/broken.py": "def broken(:\n"})

    with pytest.raises(AuditParseError) as raised:
        audit_import_order.audit_sources(sources)

    assert isinstance(raised.value.__cause__, SyntaxError)
    assert "ralph/pkg/broken.py" in str(raised.value)


# --- entry-point wiring -------------------------------------------------------


_REPORTED = Violation(
    entry="ralph.pkg.a",
    importer="ralph.pkg.b",
    lineno=2,
    target="ralph.pkg.a",
    name="y",
    chain=("ralph.pkg.a", "ralph.pkg.b"),
)


def _clean_tree(_root: Path) -> tuple[list[Violation], int]:
    return [], 5


def _violating_tree(_root: Path) -> tuple[list[Violation], int]:
    return [_REPORTED], 3


def _missing_tree(root: Path) -> tuple[list[Violation], int]:
    raise FileNotFoundError(f"audit root is not a directory: {root / 'ralph'}")


def _unparseable_tree(_root: Path) -> tuple[list[Violation], int]:
    return audit_import_order.audit_sources({"ralph/broken.py": "def broken(:\n"}), 1


@pytest.mark.parametrize(
    ("audit_tree", "exit_code", "expected_output"),
    [
        (_clean_tree, 0, "All 5 module(s) under ralph/ import cleanly when imported first."),
        (_violating_tree, 1, "IMPORT-ORDER VIOLATIONS: 1 in 3 module(s)"),
        (_violating_tree, 1, str(_REPORTED)),
        (_missing_tree, 1, "audit root is not a directory"),
        (_unparseable_tree, 1, "Could not parse a file under audit"),
    ],
    ids=["clean", "violations-header", "violations-detail", "missing-root", "unparseable"],
)
def test_main_audits_the_working_directory_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    audit_tree: Callable[[Path], tuple[list[Violation], int]],
    exit_code: int,
    expected_output: str,
) -> None:
    roots: list[Path] = []

    def recording_audit_tree(root: Path) -> tuple[list[Violation], int]:
        roots.append(root)
        return audit_tree(root)

    monkeypatch.setattr(audit_import_order, "audit_tree", recording_audit_tree)

    assert audit_import_order.main([]) == exit_code
    assert expected_output in capsys.readouterr().out
    assert roots == [Path.cwd()]


class _FailingStepRunner:
    """Verify runner that succeeds every step except the one it is told to fail."""

    def __init__(self, failing_args: tuple[str, ...]) -> None:
        self.failing_args = failing_args
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture_output: bool = True,
    ) -> ProcessResult:
        del cwd, env, timeout, capture_output
        self.calls.append((command, tuple(args)))
        returncode = 1 if tuple(args) == self.failing_args else 0
        return ProcessResult(command=(command, *args), returncode=returncode, stdout="", stderr="")


def test_make_verify_runs_the_import_order_audit_under_its_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A red audit must stop ``make verify`` and name the import-order step."""
    runner = _FailingStepRunner(_IMPORT_ORDER_STEP_ARGS)

    exit_code = run_verify(cwd=Path("ralph-workflow"), runner=runner)

    assert exit_code == 1
    assert runner.calls[-1] == ("uv", _IMPORT_ORDER_STEP_ARGS)
    assert f"`make verify` failed while running: {_IMPORT_ORDER_STEP_LABEL}" in (
        capsys.readouterr().err
    )


# --- shapes the first version of the audit got wrong --------------------------


def test_flags_imports_in_the_else_of_a_main_guard() -> None:
    """Only the ``__main__`` BODY is skipped on import; its ``else:`` runs.

    A fresh ``import ralph.pkg.b`` raises ``ImportError: cannot import name
    'x' from partially initialized module 'ralph.pkg.b'``, but the audit
    drops the whole ``if`` statement, ``orelse`` included.
    """
    a_source = """
        def y() -> int:
            return 1


        if __name__ == "__main__":
            pass
        else:
            from ralph.pkg.b import x
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert _sites(violations) == [("ralph.pkg.a", 8, "ralph.pkg.b", "x")]


def test_flags_imports_in_a_class_body() -> None:
    """A class body executes at import time, so its imports take part.

    A fresh ``import ralph.pkg.b`` raises ``ImportError: cannot import name
    'x' from partially initialized module 'ralph.pkg.b'``, but the audit
    never descends into class bodies.
    """
    a_source = """
        def y() -> int:
            return 1


        class Holder:
            from ralph.pkg.b import x
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert _sites(violations) == [("ralph.pkg.a", 6, "ralph.pkg.b", "x")]


def test_ignores_imports_in_the_else_of_a_negated_type_checking_guard() -> None:
    """``if not TYPE_CHECKING: ... else:`` puts the type-only arm in ``else``.

    A fresh ``import ralph.pkg.b`` succeeds, but the audit treats the
    negated guard as an ordinary ``if`` and replays both arms.
    """
    a_source = """
        from typing import TYPE_CHECKING


        def y() -> int:
            return 1


        if not TYPE_CHECKING:
            pass
        else:
            from ralph.pkg.b import x
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert violations == []


# --- module-level reads through a bound module ---------------------------------

_B_BINDS_A_THEN_X = "from ralph.pkg import a\nx = 1\n\n\nclass Base:\n    pass\n"


@pytest.mark.parametrize(
    ("a_source", "site"),
    [
        ("from ralph.pkg import b\nX = b.x\n", (2, "ralph.pkg.b", "x")),
        (
            "from ralph.pkg import b\n\n\nclass C(b.Base):\n    pass\n",
            (4, "ralph.pkg.b", "Base"),
        ),
        (
            "from ralph.pkg import b\n\n\ndef f(value: int = b.x) -> int:\n    return value\n",
            (4, "ralph.pkg.b", "x"),
        ),
        # ``ralph.pkg.b`` is not yet an attribute of ``ralph.pkg`` while it runs.
        ("import ralph.pkg.b\nX = ralph.pkg.b.x\n", (2, "ralph.pkg", "b")),
    ],
    ids=["assignment", "class-base", "default-argument", "dotted-import"],
)
def test_flags_module_level_reads_of_a_partially_initialised_module(
    a_source: str, site: tuple[int, str, str]
) -> None:
    """Binding the module only defers the lookup if the read is deferred too.

    A fresh ``import ralph.pkg.b`` raises ``AttributeError`` for each shape.
    """
    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_BINDS_A_THEN_X})

    assert _sites(violations) == [("ralph.pkg.a", *site)]
    assert [item.access for item in violations] == ["attribute"]
    assert "read at module level" in str(violations[0])


def test_allows_reads_of_a_bound_module_inside_functions() -> None:
    """The fix pattern itself: bind the module, read the name at call time."""
    a_source = "from ralph.pkg import b\n\n\ndef get() -> int:\n    return b.x\n"

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_BINDS_A_THEN_X})

    assert violations == []


def test_star_import_binds_the_names_it_copies() -> None:
    """``from m import *`` binds m's public names, so a later lookup finds them."""
    violations = _audit(
        {
            "pkg/helpers.py": "foo = 1\n",
            "pkg/a.py": "from ralph.pkg.helpers import *\nfrom ralph.pkg import b\n",
            "pkg/b.py": "from ralph.pkg.a import foo\nx = 1\n",
        }
    )

    assert violations == []


def test_allows_a_failing_import_caught_by_an_import_error_handler() -> None:
    """The handler runs instead, so the module still imports."""
    a_source = """
        def y() -> int:
            return 1


        try:
            from ralph.pkg.b import x
        except ImportError:
            x = None
        """

    violations = _audit({"pkg/a.py": a_source, "pkg/b.py": _B_NEEDS_A_FIRST})

    assert violations == []
