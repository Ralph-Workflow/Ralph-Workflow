"""Every shipped format-doc example must validate through the real markdown gate.

The examples under ``ralph/mcp/artifacts/format_docs/examples/`` are both
reference material for agents and a standing proof that our own validator
accepts what we tell agents to imitate. Each example file is enumerated
dynamically so a newly added example is covered automatically.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from ralph.mcp.artifacts.format_docs import (
    EXAMPLE_ARTIFACT_TYPES,
    load_bundled_example,
)
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec

import_module("ralph.mcp.artifacts.markdown.specs")

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / (
    "ralph/mcp/artifacts/format_docs/examples"
)


def _shipped_example_paths() -> list[Path]:
    paths = sorted(_EXAMPLES_DIR.glob("*.md"))
    assert paths, f"no example artifacts found under {_EXAMPLES_DIR}"
    return paths


def _example_id(example_path: Path) -> str:
    return example_path.stem


@pytest.mark.parametrize("example_path", _shipped_example_paths(), ids=_example_id)
def test_shipped_example_validates_with_zero_errors(example_path: Path) -> None:
    artifact_type = example_path.stem
    content = example_path.read_text(encoding="utf-8")
    spec = get_spec(artifact_type)
    _, diagnostics = parse_and_validate(content, spec)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == [], f"example {example_path.name} must validate cleanly; got: " + "; ".join(
        f"line {d.line} [{d.rule_id}] {d.message}" for d in errors
    )


def test_every_declared_example_type_ships_a_file() -> None:
    shipped = {path.stem for path in _shipped_example_paths()}
    assert shipped == set(EXAMPLE_ARTIFACT_TYPES)


def test_bundled_example_loader_round_trips_shipped_files() -> None:
    for example_path in _shipped_example_paths():
        loaded = load_bundled_example(example_path.stem)
        assert loaded == example_path.read_text(encoding="utf-8")
    assert load_bundled_example("bogus") is None
