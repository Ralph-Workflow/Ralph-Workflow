"""Plain-language configuration validation messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path

    from pydantic import ValidationError

    ValidationErrorDetail = Mapping[str, object]
    ValidationErrorDetails = Sequence[ValidationErrorDetail]


def warn_unknown_top_level_fields(
    data: dict[str, object], path: Path, known_fields: Collection[str]
) -> None:
    """Warn about unknown top-level TOML tables without inspecting subtables."""
    for field in data:
        if field not in known_fields:
            logger.warning("Unknown configuration field `{}` in {}.", field, path)


def _corrected_example(field: str) -> str:
    """Return the smallest useful TOML example for a failed field."""
    if field.startswith("agents.") and field.endswith(".cmd"):
        agent_name = field.split(".")[1]
        return f'add cmd = "<binary>" under [agents.{agent_name}]'
    leaf = field.rsplit(".", maxsplit=1)[-1]
    return f"set {leaf} to a value matching its documented type"


def format_config_validation_error(exc: ValidationError, source_path: Path) -> str:
    """Translate a Pydantic validation error into Ralph's what/why/fix shape.

    Reuses :func:`ralph.pydantic_validation_errors.format_validation_error_messages`
    so the per-field line lists the rejected value and (for closed enums)
    the allowed values — the bare first-field-only message the old
    formatter produced hides both. The envelope still mirrors the
    ``ConfigTomlError`` shape so callers can render either with the same
    UI plumbing.
    """
    from ralph.pydantic_validation_errors import format_validation_error_messages

    details = format_validation_error_messages(exc)
    # Pydantic raised without per-field details: fall back to a single
    # generic line so the operator still gets a fix path.
    body = (
        "\n".join(details)
        if details
        else "  - configuration has invalid settings"
    )
    return (
        f"What failed: {source_path} has invalid configuration:\n"
        f"{body}\n"
        "Why it matters: Ralph cannot safely apply a configuration it cannot validate.\n"
        f"Fix: correct the listed field(s) in {source_path}, then run `ralph --check-config`."
    )


__all__ = [
    "format_config_validation_error",
    "warn_unknown_top_level_fields",
]
