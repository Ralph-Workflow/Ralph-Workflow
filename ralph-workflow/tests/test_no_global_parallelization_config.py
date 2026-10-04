"""Regression tests: no global parallelization config must exist outside transition policy.

Parallelization in Ralph v1 is transition-scoped: only [phases.<phase>.parallelization]
blocks in pipeline.toml control fan-out. Top-level or cross-pipeline parallel switches
in UnifiedConfig or ralph-workflow.toml are forbidden.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

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


def test_ralph_workflow_toml_has_no_global_parallel_keys() -> None:
    """ralph-workflow.toml and ralph-workflow-local.toml must not have global parallel keys.

    Both templates are tracked in the clean clone at
    ``ralph/policy/defaults/ralph-workflow.toml`` and
    ``ralph/policy/defaults/ralph-workflow-local.toml``. The contract is that
    neither may expose a top-level ``parallel_execution``,
    ``max_parallel_workers``, or ``max_work_units`` key. Parallelization is
    transition-scoped and belongs in ``pipeline.toml`` only.
    """
    expected = {
        _DEFAULTS_DIR / "ralph-workflow.toml",
        _DEFAULTS_DIR / "ralph-workflow-local.toml",
    }
    missing = sorted(str(p) for p in expected if not p.exists())
    assert not missing, f"tracked template(s) missing from clean clone: {missing}"
    forbidden = ("parallel_execution", "max_parallel_workers", "max_work_units")
    for toml_path in sorted(expected):
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        offenders = [name for name in forbidden if name in data]
        assert not offenders, (
            f"{toml_path.name} must not have top-level parallel key(s) "
            f"{offenders!r}; parallelization is transition-scoped and lives in "
            "pipeline.toml under [phases.<phase>.parallelization]."
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
