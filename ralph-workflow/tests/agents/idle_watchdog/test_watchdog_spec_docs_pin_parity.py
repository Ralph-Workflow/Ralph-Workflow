from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


def test_every_pin_test_path_is_cited_in_watchdog_spec_docs() -> None:
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
