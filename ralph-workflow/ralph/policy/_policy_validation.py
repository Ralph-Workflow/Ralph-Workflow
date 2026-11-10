"""Policy-file loading, block compilation, and schema validation.

The policy loader (:mod:`ralph.policy.loader`) owns the layering rules --
which file wins, how a workspace inherits, and which bundle Ralph gets back.
This module owns the part that decides whether ONE file is acceptable: it
reads a policy TOML, compiles the block-authored pipeline graph, and
validates the agents/pipeline/artifacts schemas against the policy models.

Both modules are one contract: the loader answers "which file?", this
module answers "is this file valid?".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from loguru import logger
from pydantic import TypeAdapter, ValidationError

from ralph.config._toml_fallback import load_optional_toml
from ralph.config.config_error_messages import warn_unknown_top_level_fields
from ralph.policy.models import (
    AgentsPolicy,
    ArtifactsPolicy,
    IndividualPolicyBlock,
    PhaseDefinition,
    PipelinePolicy,
    PolicyBlock,
)
from ralph.policy.validation import PolicyValidationError

if TYPE_CHECKING:
    from pathlib import Path

ValidationErrorDetail = Mapping[str, object]
ValidationErrorDetails = Sequence[ValidationErrorDetail]

PIPELINE_POLICY_FIELDS = frozenset(
    {
        "blocks",
        "entry_block",
        "phases",
        "entry_phase",
        "terminal_phase",
        "loop_counters",
        "budget_counters",
        "post_commit_routes",
        "lifecycle_phases",
        "default_phase_retry_policy",
        "recovery",
        "cycle_timebox",
        "development_timebox",
        "commit_review",
    }
)
_BLOCKS_ADAPTER = TypeAdapter(dict[str, PolicyBlock])
_ARTIFACTS_TOP_LEVEL_FIELDS = frozenset({"artifacts"})


def _kind_for_policy_path(path: Path) -> str:
    name = path.name
    if "pipeline" in name:
        return "pipeline"
    if "agents" in name:
        return "agents"
    if "artifacts" in name:
        return "artifacts"
    if "portfolio" in name:
        return "portfolio"
    return "policy"


def load_toml(
    path: Path,
    *,
    kind: str | None = None,
    strict: bool = False,
) -> dict[str, object]:
    """Load a TOML file, returning empty dict if absent or on fallback.

    Args:
        path: Path to the TOML file.
        kind: Optional policy kind for warning messages. If None, derived from path.
        strict: If True, raise PolicyValidationError on parse or read errors.

    Returns:
        Parsed TOML content or empty dict if file doesn't exist or on fallback.

    Raises:
        PolicyValidationError: If TOML parsing fails OR the file cannot be read
            and strict=True.
    """
    resolved_kind = kind if kind is not None else _kind_for_policy_path(path)
    try:
        data = load_optional_toml(path, kind=resolved_kind, strict=strict)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PolicyValidationError(
            (
                f"Could not parse TOML at {path}: {exc}.\n"
                "WHY: settings in a malformed TOML file are not safe to use, "
                "so Ralph refuses to guess what you meant.\n"
                f"FIX: correct the TOML syntax in {path}, then re-run "
                "`ralph --check-config`."
            ),
            source=str(path.name),
        ) from exc
    except OSError as exc:
        raise PolicyValidationError(
            (
                f"Could not read TOML at {path}: {exc}.\n"
                "WHY: a file that Ralph cannot open is the same as a file "
                "Ralph cannot trust, so the loader refuses to silently fall back.\n"
                f"FIX: check that {path} exists, that the file is readable "
                "(file mode and ownership), and that the directory is accessible; "
                "then re-run `ralph --check-config`."
            ),
            source=str(path.name),
        ) from exc

    if data:
        _warn_unknown_policy_top_level_fields(data, path)
    return data


def _warn_unknown_policy_top_level_fields(data: dict[str, object], path: Path) -> None:
    if path.name == "pipeline.toml":
        warn_unknown_top_level_fields(data, path, PIPELINE_POLICY_FIELDS)
    elif path.name == "artifacts.toml":
        warn_unknown_top_level_fields(data, path, _ARTIFACTS_TOP_LEVEL_FIELDS)
    elif path.name == "agents.toml":
        warn_unknown_top_level_fields(data, path, AgentsPolicy.model_fields)


def _phase_name_for_block(
    block_name: str,
    blocks: Mapping[str, PolicyBlock],
    *,
    path: tuple[str, ...] = (),
) -> str:
    block = blocks.get(block_name)
    if block is None:
        raise PolicyValidationError(
            f"pipeline.toml block '{block_name}' is referenced but not defined",
            source="pipeline",
        )
    if isinstance(block, IndividualPolicyBlock):
        return block.phase_name
    if block_name in path:
        cycle = " -> ".join((*path, block_name))
        raise PolicyValidationError(
            f"pipeline.toml block graph contains a cycle: {cycle}",
            source="pipeline",
        )
    if not block.child_blocks:
        raise PolicyValidationError(
            "pipeline.toml group block "
            f"'{block_name}' must declare at least one child_blocks entry",
            source="pipeline",
        )
    return _phase_name_for_block(block.child_blocks[0], blocks, path=(*path, block_name))


def _compile_block_pipeline_data(data: dict[str, object]) -> dict[str, object]:
    raw_blocks = data.get("blocks")
    if raw_blocks is None:
        return data
    if not isinstance(raw_blocks, Mapping):
        raise PolicyValidationError(
            "pipeline.toml blocks must be a mapping of block names to block definitions",
            source="pipeline",
        )

    blocks = _BLOCKS_ADAPTER.validate_python(raw_blocks)
    phases: dict[str, object] = {}
    lifecycle_phases: dict[str, object] = {}

    for block_name, block in blocks.items():
        if isinstance(block, IndividualPolicyBlock):
            if block.phase_name in phases:
                raise PolicyValidationError(
                    "pipeline.toml phase_name "
                    f"'{block.phase_name}' is declared by more than one individual block",
                    source="pipeline",
                )
            phases[block.phase_name] = block.phase
            continue

        for child_name in block.child_blocks:
            if child_name not in blocks:
                raise PolicyValidationError(
                    "pipeline.toml group block "
                    f"'{block_name}' references unknown child_blocks entry '{child_name}'",
                    source="pipeline",
                )
        if block.completion_block not in block.child_blocks:
            raise PolicyValidationError(
                "pipeline.toml group block "
                f"'{block_name}' completion_block '{block.completion_block}' "
                "must also appear in child_blocks",
                source="pipeline",
            )
        completion_phase = _phase_name_for_block(block.completion_block, blocks)
        lifecycle_phases[completion_phase] = {
            "lifecycle_name": block_name,
            "completion_block": block.completion_block,
            "increments_counter": block.increments_counter,
            "loop_resets": list(block.loop_resets),
            "before_complete": list(block.before_complete),
            "after_complete": list(block.after_complete),
        }

    entry_block = data.get("entry_block")
    if not isinstance(entry_block, str) or not entry_block:
        raise PolicyValidationError(
            "pipeline.toml block-authored policies must declare entry_block",
            source="pipeline",
        )
    entry_phase = _phase_name_for_block(entry_block, blocks)

    normalized = dict(data)
    normalized["blocks"] = blocks
    normalized["phases"] = phases
    normalized["entry_phase"] = entry_phase
    normalized["lifecycle_phases"] = lifecycle_phases
    return normalized


def _normalize_pipeline_data(data: dict[str, object]) -> dict[str, object]:
    """Normalize pipeline data and reject obsolete authoring formats."""
    nested_pipeline = data.get("pipeline")
    if isinstance(nested_pipeline, Mapping) and not PIPELINE_POLICY_FIELDS.intersection(data):
        raise PolicyValidationError(
            "pipeline.toml uses the obsolete [pipeline] wrapper format. "
            "This redesign is a hard break; regenerate the policy with the "
            "new block-based schema.",
            source="pipeline",
        )
    return _compile_block_pipeline_data(data)


def format_validation_error_messages(exc: ValidationError) -> list[str]:
    """Format all pydantic ValidationError errors into human-readable strings."""
    details = cast(
        "ValidationErrorDetails", exc.errors()
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    return [format_validation_error_detail(detail) for detail in details]


def format_validation_error_detail(detail: ValidationErrorDetail) -> str:
    """Format a single pydantic validation error detail as 'location: message'."""
    loc = detail.get("loc")
    msg = detail.get("msg")
    return f"  {format_validation_location(loc)}: {format_validation_message(msg)}"


def format_validation_location(raw_loc: object | None) -> str:
    """Format a pydantic error location tuple to a dotted path string."""
    if raw_loc is None:
        return "<root>"
    if isinstance(raw_loc, list | tuple):
        if not raw_loc:
            return "<root>"
        return ".".join(str(component) for component in raw_loc)
    return str(raw_loc)


def format_validation_message(raw_msg: object | None) -> str:
    """Return the validation error message string, substituting a placeholder if absent."""
    if isinstance(raw_msg, str):
        return raw_msg
    if raw_msg is None:
        return "<missing message>"
    return str(raw_msg)


def validate_agents(data: dict[str, object]) -> AgentsPolicy:
    """Validate and return AgentsPolicy.

    Args:
        data: Raw TOML dictionary.

    Returns:
        Validated AgentsPolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    misplaced_pipeline_fields = sorted(set(data).intersection(PIPELINE_POLICY_FIELDS))
    if misplaced_pipeline_fields:
        fields = ", ".join(f"`{field}`" for field in misplaced_pipeline_fields)
        raise PolicyValidationError(
            f"agents.toml field(s) {fields} belong in pipeline.toml, not agents.toml.",
            source="agents",
        )
    try:
        return AgentsPolicy.model_validate(data)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise PolicyValidationError(
            "agents.toml validation failed:\n" + "\n".join(msgs),
            source="agents",
        ) from exc


def _phase_def_declares_edge(phase_def: PhaseDefinition, target: str) -> bool:
    """Return whether *phase_def* routes directly to *target*.

    Checks transitions, decisions, bypass routes, result-status post-commit
    routes, and post-commit routes declared on the phase definition.
    """
    t = phase_def.transitions
    for candidate in (t.on_success, t.on_failure, t.on_loopback, t.on_reject):
        if candidate == target:
            return True
    for route in phase_def.decisions.values():
        if route.target == target:
            return True
    for routes_attr in ("bypass_routes", "result_status_post_commit"):
        routes: dict[str, str] = getattr(phase_def, routes_attr)
        for route_target in routes.values():
            if route_target == target:
                return True
    return False


def _edge_declared_in_normalized(normalized: dict[str, object], source: str, target: str) -> bool:
    """Return whether ``source`` declares a direct route to ``target``.

    Operates on the normalized (block-compiled) pipeline data before Pydantic
    validation: phase definitions are ``PhaseDefinition`` objects while
    ``post_commit_routes`` may still be raw TOML dicts.
    """
    phases = normalized.get("phases")
    if isinstance(phases, Mapping):
        typed_phases: Mapping[str, PhaseDefinition] = cast(
            "Mapping[str, PhaseDefinition]", phases
        )  # cast-policy: seam: structural boundary (normalized pipeline mapping narrowed to its typed phase map)
        phase_def = typed_phases.get(source)
        if phase_def is not None and _phase_def_declares_edge(phase_def, target):
            return True
    routes = normalized.get("post_commit_routes")
    if isinstance(routes, list):
        for route in routes:
            if isinstance(route, Mapping):
                typed_route: Mapping[str, object] = cast("Mapping[str, object]", route)
                when_val = typed_route.get("when")
                if isinstance(when_val, Mapping):
                    typed_when: Mapping[str, object] = cast("Mapping[str, object]", when_val)
                    if typed_when.get("phase") == source and typed_route.get("target") == target:
                        return True
    return False


def _disable_incompatible_inherited_timebox(
    normalized: dict[str, object], name: str
) -> dict[str, object]:
    """Disable an inherited timebox that does not fit the compiled graph."""
    timebox = normalized.get(name)
    phases = normalized.get("phases")
    if not isinstance(timebox, Mapping) or not isinstance(phases, Mapping):
        return normalized
    phase_names = set(
        cast("Mapping[str, PhaseDefinition]", phases)
    )  # cast-policy: seam: structural boundary (normalized pipeline mapping narrowed to its typed phase map)
    for key in ("start_source", "start_entry", "guarded_entry", "end_entry", "finalization_target"):
        value = timebox.get(key)
        if not isinstance(value, str) or value not in phase_names:
            logger.warning(
                "{name} disabled: inherited default names {key}={value!r}, which the active graph does not declare.{deadline}",
                name=name,
                key=key,
                value=value,
                deadline=" This run has no cycle deadline." if name == "cycle_timebox" else "",
            )
            result = dict(normalized)
            result.pop(name, None)
            return result
    source, entry = timebox.get("start_source"), timebox.get("start_entry")
    if not _edge_declared_in_normalized(
        normalized,
        source if isinstance(source, str) else "",
        entry if isinstance(entry, str) else "",
    ):
        logger.warning(
            "{name} disabled: inherited default start edge does not fit the graph", name=name
        )
        result = dict(normalized)
        result.pop(name, None)
        return result
    return normalized


def _disable_incompatible_inherited_development_timebox(
    normalized: dict[str, object],
) -> dict[str, object]:
    """Disable an inherited development timebox that does not fit the compiled graph."""
    return _disable_incompatible_inherited_timebox(normalized, "development_timebox")


def _disable_incompatible_inherited_cycle_timebox(
    normalized: dict[str, object],
) -> dict[str, object]:
    """Disable an inherited cycle timebox that does not fit the compiled graph."""
    return _disable_incompatible_inherited_timebox(normalized, "cycle_timebox")


#: Public alias for :func:`_disable_incompatible_inherited_cycle_timebox`.
#: Tests pin this helper's behavior through the public name.
disable_incompatible_inherited_cycle_timebox = _disable_incompatible_inherited_cycle_timebox


def validate_pipeline(
    data: dict[str, object],
    *,
    timeboxes_explicit: bool = True,
    cycle_timebox_explicit: bool | None = None,
) -> PipelinePolicy:
    """Validate and return PipelinePolicy.

    Args:
        data: Raw TOML dictionary.
        timeboxes_explicit: When False, inherited timebox defaults that do
            not fit the compiled graph are disabled rather than rejected.
        cycle_timebox_explicit: Backward-compatible alias for
            ``timeboxes_explicit`` used by direct loader callers.

    Returns:
        Validated PipelinePolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    if cycle_timebox_explicit is not None:
        timeboxes_explicit = cycle_timebox_explicit
    normalized = _normalize_pipeline_data(data)
    if not timeboxes_explicit:
        normalized = _disable_incompatible_inherited_cycle_timebox(normalized)
        normalized = _disable_incompatible_inherited_development_timebox(normalized)
    try:
        return PipelinePolicy.model_validate(normalized)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise PolicyValidationError(
            "pipeline.toml validation failed:\n" + "\n".join(msgs),
            source="pipeline",
        ) from exc


def validate_artifacts(data: dict[str, object]) -> ArtifactsPolicy:
    """Validate and return ArtifactsPolicy.

    Args:
        data: Raw TOML dictionary.

    Returns:
        Validated ArtifactsPolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    try:
        return ArtifactsPolicy.model_validate(data)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise PolicyValidationError(
            "artifacts.toml validation failed:\n" + "\n".join(msgs),
            source="artifacts",
        ) from exc


_ARTIFACTS_TOP_LEVEL_FIELDS = frozenset({"artifacts"})

__all__ = [
    "PIPELINE_POLICY_FIELDS",
    "ValidationErrorDetail",
    "ValidationErrorDetails",
    "disable_incompatible_inherited_cycle_timebox",
    "format_validation_error_detail",
    "format_validation_error_messages",
    "format_validation_location",
    "format_validation_message",
    "load_toml",
    "validate_agents",
    "validate_artifacts",
    "validate_pipeline",
]
