"""Plain-language configuration validation messages."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

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


def _field_path(exc: ValidationError) -> str:
    details = cast("ValidationErrorDetails", exc.errors())
    if not details:
        return "configuration"
    location = details[0].get("loc", ())
    if isinstance(location, tuple):
        return ".".join(str(part) for part in location) or "configuration"
    return str(location)


def _corrected_example(field: str) -> str:
    """Return the smallest useful TOML example for a failed field."""
    if field.startswith("agents.") and field.endswith(".cmd"):
        agent_name = field.split(".")[1]
        return f'add cmd = "<binary>" under [agents.{agent_name}]'
    leaf = field.rsplit(".", maxsplit=1)[-1]
    return f"set {leaf} to a value matching its documented type"


def format_config_validation_error(exc: ValidationError, source_path: Path) -> str:
    """Translate a Pydantic validation error into Ralph's what/why/fix shape."""
    field = _field_path(exc)
    return (
        f"What failed: {source_path} has an invalid `{field}` setting.\n"
        "Why it matters: Ralph cannot safely apply a configuration it cannot validate.\n"
        f"Fix: {_corrected_example(field)} in {source_path}."
    )
