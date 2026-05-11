"""Regression tests: no global parallelization config must exist outside transition policy.

Parallelization in Ralph v1 is transition-scoped: only [phases.<phase>.parallelization]
blocks in pipeline.toml control fan-out. Top-level or cross-pipeline parallel switches
in UnifiedConfig or ralph-workflow.toml are forbidden.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from pydantic import BaseModel

from ralph.config.models import UnifiedConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULTS_DIR = _REPO_ROOT / "ralph-workflow" / "ralph" / "policy" / "defaults"

_FORBIDDEN_TOP_LEVEL_KEYS = re.compile(
    r"^(parallel_execution|max_parallel_workers|max_work_units|"
    r"require_allowed_directories|post_fanout_verification)$"
)


def test_pipeline_toml_has_no_top_level_parallel_execution() -> None:
    """pipeline.toml must not have a top-level [parallel_execution] key."""
    toml_path = _DEFAULTS_DIR / "pipeline.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert "parallel_execution" not in data, (
        "pipeline.toml must not have a top-level [parallel_execution] block. "
        "Parallelization is transition-scoped: configure it under "
        "[phases.<phase>.parallelization] only."
    )


@pytest.mark.parametrize(
    "toml_filename",
    ["ralph-workflow.toml", "ralph-workflow-local.toml"],
)
def test_ralph_workflow_toml_has_no_global_parallel_keys(toml_filename: str) -> None:
    """ralph-workflow.toml and ralph-workflow-local.toml must not have global parallel keys."""
    toml_path = _DEFAULTS_DIR / toml_filename
    if not toml_path.exists():
        pytest.skip(f"{toml_filename} not found in defaults directory")

    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    for forbidden in ("parallel_execution", "max_parallel_workers", "max_work_units"):
        assert forbidden not in data, (
            f"{toml_filename} must not have a top-level '{forbidden}' key. "
            f"Parallelization belongs only in pipeline.toml under [phases.<phase>.parallelization]."
        )


def _walk_model_fields(
    model_cls: type,
    visited: set[type] | None = None,
) -> list[str]:
    """Recursively collect field names from a Pydantic model."""
    if visited is None:
        visited = set()
    if model_cls in visited:
        return []
    visited.add(model_cls)

    found: list[str] = []
    for name, field in model_cls.model_fields.items():
        if name.startswith("_"):
            continue
        found.append(name)
        annotation = field.annotation
        # Unwrap Optional / union types
        origin = getattr(annotation, "__origin__", None)
        args = getattr(annotation, "__args__", ()) if origin is not None else (annotation,)
        for arg in args:
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                found.extend(_walk_model_fields(arg, visited))
    return found


def test_unified_config_has_no_forbidden_parallel_fields() -> None:
    """UnifiedConfig and nested models must not expose forbidden global parallel fields."""
    all_fields = _walk_model_fields(UnifiedConfig)
    violations = [name for name in all_fields if _FORBIDDEN_TOP_LEVEL_KEYS.match(name)]
    assert violations == [], (
        f"UnifiedConfig (or a nested model) contains forbidden global parallel field(s): "
        f"{violations!r}. "
        "Parallelization config must only exist as [phases.<phase>.parallelization] "
        "in PipelinePolicy, not in UnifiedConfig."
    )
