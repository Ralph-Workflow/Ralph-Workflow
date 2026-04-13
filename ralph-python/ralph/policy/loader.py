"""TOML policy loader with fallback to bundled defaults.

Loads agents.toml, pipeline.toml, and artifacts.toml from the user's .agent/
config directory, falling back to the packaged defaults when files are absent.

All loading goes through Pydantic validation so any malformed config surfaces
as a PolicyValidationError with field-level detail.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

import ralph.policy
from ralph.policy.models import (
    AgentsPolicy,
    ArtifactsPolicy,
    PipelinePolicy,
    PolicyBundle,
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
        msgs = [f"  {e['loc']}: {e['msg']}" for e in exc.errors()]
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
        return PipelinePolicy.model_validate(data)
    except ValidationError as exc:
        msgs = [f"  {e['loc']}: {e['msg']}" for e in exc.errors()]
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
        msgs = [f"  {e['loc']}: {e['msg']}" for e in exc.errors()]
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
        return PolicyBundle(
            agents=agents_policy,
            pipeline=pipeline_policy,
            artifacts=artifacts_policy,
        )
    except ValidationError as exc:
        msgs = [f"  {e['loc']}: {e['msg']}" for e in exc.errors()]
        raise PolicyValidationError(
            "Cross-policy validation failed (drain bindings / analysis contracts):\n"
            + "\n".join(msgs),
            source=None,
        ) from exc


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
