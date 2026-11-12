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
    for field in data:
        if field not in known_fields:
            logger.warning("Unknown configuration field `{}` in {}.", field, path)


def _corrected_example(field: str) -> str:
    if field.startswith("agents.") and field.endswith(".cmd"):
        agent_name = field.split(".")[1]
        return f'add cmd = "<binary>" under [agents.{agent_name}]'
    leaf = field.rsplit(".", maxsplit=1)[-1]
    return f"set {leaf} to a value matching its documented type"


def format_config_validation_error(exc: ValidationError, source_path: Path) -> str:
    from ralph.pydantic_validation_errors import format_validation_error_messages

    details = format_validation_error_messages(exc)
    body = "\n".join(details) if details else "  - configuration has invalid settings"
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
