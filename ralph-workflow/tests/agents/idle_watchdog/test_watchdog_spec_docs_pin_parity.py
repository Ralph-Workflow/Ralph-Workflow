"""Docs<->pin-list parity guard for the Trustworthy Idle Watchdog spec.

The companion consolidated spec acceptance test
(``tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py``)
enforces a one-directional R8 invariant via ``test_r8``: every entry in
``RALPH_PIN_TEST_PATHS`` must exist on disk. This module closes the
OTHER direction of the contract: every entry in ``RALPH_PIN_TEST_PATHS``
must be cited verbatim in the traceability documentation at
``docs/agents/watchdog-spec.md``.

The closed direction is the canonical contract committed by the spec
test's own docstring: the ``RALPH_PIN_TEST_PATHS`` list "mirrors the
dedicated pin tests enumerated in ``ralph-workflow/docs/agents/watchdog-spec.md``".
If a pin test is added to the spec (and the docs) without also being
appended to ``RALPH_PIN_TEST_PATHS``, ``test_r8`` fails on the on-disk
check. Conversely, if a pin test is appended to ``RALPH_PIN_TEST_PATHS``
without being cited in the docs, this guard catches the drift BEFORE
the change can land.

Test isolation guarantees:

  * No real subprocess (no ``subprocess.run``, no ``os.system``,
    no real ``Popen`` -- the audit ``audit_test_policy`` enforces
    no real I/O; the one bounded docs read below is sanctioned via
    the file stem in ``_IO_ALLOWLIST``).
  * No ``time.sleep`` (no wall-clock waits).
  * No module-level mutable accumulators (all locals).
  * No ``type: ignore`` directives (tests must be fully typed per AGENTS.md).
  * The ``RALPH_PIN_TEST_PATHS`` tuple is loaded via ``importlib`` with
    ``sys.modules`` registration BEFORE ``exec_module`` -- the source
    module defines a module-level ``@dataclass`` (``_HelpersOnlyMonitor``)
    that triggers ``AttributeError`` under importlib without the
    ``sys.modules`` registration first. Verified.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


def test_every_pin_test_path_is_cited_in_watchdog_spec_docs() -> None:
    """Every ``RALPH_PIN_TEST_PATHS`` entry is cited in watchdog-spec.md.

    Resolves the ralph-workflow package root via
    ``Path(__file__).resolve().parent.parent.parent.parent`` (4x chained
    ``.parent`` == ``parents[3]``). The test file lives at
    ``ralph-workflow/tests/agents/idle_watchdog/`` so:

      parents[0] = ralph-workflow/tests/agents/idle_watchdog
      parents[1] = ralph-workflow/tests/agents
      parents[2] = ralph-workflow/tests
      parents[3] = ralph-workflow

    The ralph-workflow package root contains both
    ``docs/agents/watchdog-spec.md`` (the subject under test) and
    ``tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py``
    (the source of ``RALPH_PIN_TEST_PATHS``). Do NOT use ``parents[4]``
    -- the repo-root ``docs/agents/`` holds a DIFFERENT file set and
    has no ``watchdog-spec.md``.
    """
    test_root: Path = Path(__file__).resolve().parent.parent.parent.parent

    spec_path: Path = (
        test_root / "tests" / "agents" / "idle_watchdog" / "test_trustworthy_idle_watchdog_spec.py"
    )
    spec = importlib.util.spec_from_file_location("_spec_pin_paths", spec_path)
    assert spec is not None, (
        f"Could not build importlib spec for {spec_path}; the source "
        f"file must exist and be a valid Python module."
    )
    module = importlib.util.module_from_spec(spec)
    # MUST register in sys.modules BEFORE exec_module: the source module
    # defines a module-level ``@dataclass`` (``_HelpersOnlyMonitor``)
    # whose decorator raises AttributeError under importlib without the
    # registration first.
    sys.modules["_spec_pin_paths"] = module
    assert spec.loader is not None, (
        f"importlib spec for {spec_path} has no loader; the file "
        f"must be a regular Python module (not a namespace package)."
    )
    spec.loader.exec_module(module)

    pin_paths: tuple[str, ...] = module.RALPH_PIN_TEST_PATHS
    assert isinstance(pin_paths, tuple), (
        f"RALPH_PIN_TEST_PATHS must be a tuple (immutable); got {type(pin_paths).__name__}."
    )

    docs_path: Path = test_root / "docs" / "agents" / "watchdog-spec.md"
    docs_content: str = docs_path.read_text(encoding="utf-8")

    missing: list[str] = [p for p in pin_paths if p not in docs_content]
    assert not missing, (
        f"Docs<->pin-list drift detected: {len(missing)} RALPH_PIN_TEST_PATHS "
        f"entries are NOT cited in {docs_path}. Every entry MUST appear "
        f"verbatim in the traceability doc. Missing paths: {missing}"
    )
