"""Concrete proof checks for Markdown plan criteria and verification."""

from __future__ import annotations

import re
import shlex

_COMMANDS = frozenset(
    {
        "bundle",
        "cargo",
        "go",
        "make",
        "npm",
        "npx",
        "pnpm",
        "python",
        "python3",
        "pytest",
        "ruff",
        "ruby",
        "uv",
        "yarn",
    }
)
_VAGUE = re.compile(
    r"^(?:(?:the )?(?:code|behavior|result|system|output|everything|it) )?"
    r"(?:(?:is|looks) )?(?:clean|good|correct|successful|valid|working)"
    r"(?: correctly| as expected)?$|^looks good$|^works(?: correctly| as expected)?$",
    re.IGNORECASE,
)
_LOCATOR = re.compile(
    r"(?:^|[\s:])(?:\.{0,2}/)?[\w.-]+/[\w./-]+|"
    r"\b[\w.-]+\.(?:json|xml|ya?ml|toml|txt|log|md|html?|csv|pdf|png|jpe?g)\b",
    re.IGNORECASE,
)


def is_concrete_command(value: str) -> bool:
    """Return whether ``value`` has observable command syntax."""
    if _VAGUE.fullmatch(value.strip()):
        return False
    try:
        tokens = shlex.split(value)
    except ValueError:
        return False
    if not tokens:
        return False
    executable = tokens[0].removeprefix("./")
    return (
        executable in _COMMANDS
        or "/" in tokens[0]
        or tokens[0].endswith((".py", ".rb", ".sh"))
        or any(token.startswith("-") for token in tokens[1:])
    )


def is_specific_artifact(value: str) -> bool:
    """Return whether ``value`` names a concrete file or artifact locator."""
    stripped = value.strip()
    return bool(
        not _VAGUE.fullmatch(stripped)
        and (
            _LOCATOR.search(stripped)
            or stripped.casefold().startswith(("artifact:", "file:", "report:"))
        )
    )


def is_concrete_verification(method: str, expected: str) -> bool:
    """Return whether a verification has an observable method and outcome."""
    if _VAGUE.fullmatch(expected.strip()):
        return False
    if is_concrete_command(method):
        return True
    lowered = method.casefold()
    return lowered.startswith(("inspect ", "read ", "compare ", "review ")) and bool(
        _LOCATOR.search(method)
    )


__all__ = ["is_concrete_command", "is_concrete_verification", "is_specific_artifact"]
