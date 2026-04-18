"""TOML policy loader with fallback to bundled defaults.

Loads agents.toml, pipeline.toml, and artifacts.toml from the user's .agent/
config directory, falling back to the packaged defaults when files are absent.

All loading goes through Pydantic validation so any malformed config surfaces
as a PolicyValidationError with field-level detail.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from loguru import logger
from pydantic import ValidationError

import ralph.policy
from ralph.policy.models import (
    AgentsPolicy,
    ArtifactsPolicy,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.validation import (
    PolicyValidationError as PolicyContractValidationError,
)
from ralph.policy.validation import (
    validate_drain_contracts,
)


class PolicyValidationError(Exception):
    """Raised when policy TOML fails Pydantic validation.

    Attributes:
        errors: List of validation error messages.
        source: Which policy file failed (agents, pipeline, artifacts).
    """

    def __init__(self, message: str, source: str | None = None) -> None:
        self.message = message
        self.source = source
        super().__init__(message)


def _load_toml(path: Path) -> dict[str, object]:
    """Load a TOML file, returning empty dict if absent.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed TOML content or empty dict if file doesn't exist.

    Raises:
        PolicyValidationError: If TOML parsing fails.
    """
    if not path.exists():
        logger.debug("Policy file not found, using defaults: {}", path)
        return {}

    try:
        with path.open("rb") as fh:
            data: dict[str, object] = tomllib.load(fh)
        logger.debug("Loaded policy from {}", path)
        return data
    except Exception as exc:
        raise PolicyValidationError(
            f"Failed to parse TOML at {path}: {exc}",
            source=str(path.name),
        ) from exc


ValidationErrorDetail = Mapping[str, object]
ValidationErrorDetails = Sequence[ValidationErrorDetail]
PIPELINE_POLICY_FIELDS = frozenset(
    {
        "phases",
        "entry_phase",
        "terminal_phase",
        "post_commit_routes",
        "parallel_execution",
    }
)


def _normalize_pipeline_data(data: dict[str, object]) -> dict[str, object]:
    """Accept legacy `[pipeline]` TOML wrappers as flat PipelinePolicy input."""
    nested_pipeline = data.get("pipeline")
    if not isinstance(nested_pipeline, Mapping):
        return data

    if PIPELINE_POLICY_FIELDS.intersection(data):
        return data

    return dict(cast("Mapping[str, object]", nested_pipeline))


def _format_validation_error_messages(exc: ValidationError) -> list[str]:
    details = cast("ValidationErrorDetails", exc.errors())
    return [_format_validation_error_detail(detail) for detail in details]


def _format_validation_error_detail(detail: ValidationErrorDetail) -> str:
    loc = detail.get("loc")
    msg = detail.get("msg")
    return f"  {_format_validation_location(loc)}: {_format_validation_message(msg)}"


def _format_validation_location(raw_loc: object | None) -> str:
    if raw_loc is None:
        return "<root>"
    if isinstance(raw_loc, (list, tuple)):
        if not raw_loc:
            return "<root>"
        return ".".join(str(component) for component in raw_loc)
    return str(raw_loc)


def _format_validation_message(raw_msg: object | None) -> str:
    if isinstance(raw_msg, str):
        return raw_msg
    if raw_msg is None:
        return "<missing message>"
    return str(raw_msg)


def _validate_agents(data: dict[str, object]) -> AgentsPolicy:
    """Validate and return AgentsPolicy.

    Args:
        data: Raw TOML dictionary.

    Returns:
        Validated AgentsPolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    try:
        return AgentsPolicy.model_validate(data)
    except ValidationError as exc:
        msgs = _format_validation_error_messages(exc)
        raise PolicyValidationError(
            "agents.toml validation failed:\n" + "\n".join(msgs),
            source="agents",
        ) from exc


def _validate_pipeline(data: dict[str, object]) -> PipelinePolicy:
    """Validate and return PipelinePolicy.

    Args:
        data: Raw TOML dictionary.

    Returns:
        Validated PipelinePolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    try:
        return PipelinePolicy.model_validate(_normalize_pipeline_data(data))
    except ValidationError as exc:
        msgs = _format_validation_error_messages(exc)
        raise PolicyValidationError(
            "pipeline.toml validation failed:\n" + "\n".join(msgs),
            source="pipeline",
        ) from exc


def _validate_artifacts(data: dict[str, object]) -> ArtifactsPolicy:
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
        msgs = _format_validation_error_messages(exc)
        raise PolicyValidationError(
            "artifacts.toml validation failed:\n" + "\n".join(msgs),
            source="artifacts",
        ) from exc


def load_policy(config_dir: Path) -> PolicyBundle:
    """Load all three policy TOML files and return a validated PolicyBundle.

    Files are loaded from ``config_dir`` (the .agent/ directory). Any absent
    file is silently replaced with the bundled default.

    Args:
        config_dir: Path to the .agent/ configuration directory.

    Returns:
        Validated PolicyBundle with all three policy documents.

    Raises:
        PolicyValidationError: If any TOML file fails validation.
    """
    agents_path = config_dir / "agents.toml"
    pipeline_path = config_dir / "pipeline.toml"
    artifacts_path = config_dir / "artifacts.toml"

    agents_data = _load_toml(agents_path)
    pipeline_data = _load_toml(pipeline_path)
    artifacts_data = _load_toml(artifacts_path)

    # If pipeline.toml is absent, use defaults
    if not pipeline_data:
        pipeline_data = _load_toml(_default_dir() / "pipeline.toml")

    # If agents.toml is absent, use defaults
    if not agents_data:
        agents_data = _load_toml(_default_dir() / "agents.toml")

    # If artifacts.toml is absent, use defaults
    if not artifacts_data:
        artifacts_data = _load_toml(_default_dir() / "artifacts.toml")

    agents_policy = _validate_agents(agents_data)
    pipeline_policy = _validate_pipeline(pipeline_data)
    artifacts_policy = _validate_artifacts(artifacts_data)

    # Cross-policy validation
    try:
        bundle = PolicyBundle(
            agents=agents_policy,
            pipeline=pipeline_policy,
            artifacts=artifacts_policy,
        )
    except ValidationError as exc:
        msgs = _format_validation_error_messages(exc)
        raise PolicyValidationError(
            "Cross-policy validation failed (drain bindings / analysis contracts):\n"
            + "\n".join(msgs),
            source=None,
        ) from exc

    try:
        validate_drain_contracts(bundle)
    except PolicyContractValidationError as exc:
        raise PolicyValidationError(
            exc.message,
            source="agents",
        ) from exc

    return bundle


def _default_dir() -> Path:
    """Return the path to the bundled default policy files."""
    return Path(ralph.policy.__file__).parent / "defaults"


def load_policy_or_die(config_dir: Path) -> PolicyBundle:
    """Load policy, exiting with a user-friendly message on failure.

    Args:
        config_dir: Path to the .agent/ configuration directory.

    Returns:
        Validated PolicyBundle.
    """
    try:
        return load_policy(config_dir)
    except PolicyValidationError as exc:
        logger.error("Policy validation failed: {}", exc.message)
        if exc.source:
            logger.error("  Source: {}", exc.source)
        raise SystemExit(1) from exc
