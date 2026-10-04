"""Codex alias syntax translation without a release-specific model catalog.

Ralph validates alias structure and safely encodes CLI options. Codex owns
the available model IDs and reasoning effort vocabulary.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.agent_config import AgentConfig


def resolve_codex_alias(name: str, base_config: AgentConfig | None) -> AgentConfig | None:
    """Translate a model alias and optional effort into Codex CLI arguments."""
    alias = _parse_alias(name.removeprefix("codex/"))
    if alias is None or base_config is None:
        return None
    model_id, effort = alias
    model_flag = f"--model {shlex.quote(model_id)}"
    if effort is not None:
        effort_override = f'model_reasoning_effort = "{effort}"'
        model_flag += f" -c {shlex.quote(effort_override)}"
    return base_config.model_copy(update={"model": model_id, "model_flag": model_flag})


def _parse_alias(alias_value: str) -> tuple[str, str | None] | None:
    """Accept effort=value and effort-value suffixes with safe TOML tokens."""
    model_id, separator, suffix = alias_value.partition("[")
    if (
        not model_id
        or any(char.isspace() for char in model_id)
        or "]" in model_id
        or not all(segment for segment in model_id.split("/"))
    ):
        return None
    if not separator:
        return model_id, None
    if not suffix.endswith("]"):
        return None
    parameter = suffix[:-1]
    if parameter.startswith("effort="):
        effort = parameter.removeprefix("effort=")
    elif parameter.startswith("effort-"):
        effort = parameter.removeprefix("effort-")
    else:
        return None
    if not effort or not all(
        char.isascii() and (char.isalnum() or char in "-_") for char in effort
    ):
        return None
    return model_id, effort
