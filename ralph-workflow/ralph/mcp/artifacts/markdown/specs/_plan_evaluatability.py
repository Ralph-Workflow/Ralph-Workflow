"""Concrete proof checks for Markdown plan criteria and verification."""

from __future__ import annotations

import re
import shlex

_MIN_SPECIFIC_OUTCOME_WORDS = 2
_VAGUE = re.compile(
    r"^(?:(?:the )?(?:code|behavior|result|system|output|everything|it) )?"
    r"(?:(?:is|looks) )?(?:clean|good|correct|successful|valid|working)"
    r"(?: correctly| as expected)?$|^looks good$|^works(?: correctly| as expected)?$",
    re.IGNORECASE,
)
_VAGUE_EXPECTED = re.compile(
    r"^(?:done|ok(?:ay)?|pass(?:es|ed)?|success(?:ful)?|no problems?(?: found)?|"
    r"all good|everything (?:passes|works)|works?(?: correctly| as expected)?)\.?$",
    re.IGNORECASE,
)
_LOCATOR = re.compile(
    r"(?:^|[\s:])(?:\.{0,2}/)?[\w.-]+/[\w./-]+|"
    r"\b[\w.-]+\.(?:json|xml|ya?ml|toml|txt|log|md|html?|csv|pdf|png|jpe?g)\b",
    re.IGNORECASE,
)
_EXECUTABLE = re.compile(
    r"^(?:\.{0,2}/)?[A-Za-z0-9_.+@-]+(?:/[A-Za-z0-9_.+@-]+)*$"
)
_NON_COMMAND_LEADS = frozenset(
    {
        "check",
        "code",
        "compare",
        "confirm",
        "ensure",
        "everything",
        "inspect",
        "it",
        "read",
        "review",
        "run",
        "test",
        "tests",
        "the",
        "verify",
    }
)
_OUTCOME_SIGNAL = re.compile(
    r"\b(?:contain(?:s|ed)?|equal(?:s|ed)?|exit|fail(?:s|ed|ures?)?|"
    r"match(?:es|ed)?|pass(?:es|ed)?|produc(?:e|es|ed)|report(?:s|ed)?|"
    r"return(?:s|ed)?|show(?:s|ed)?|zero)\b",
    re.IGNORECASE,
)
_SHELL_INVOCATION = re.compile(r"^(?:bash|sh)\s+-c(?:\s|$)|^eval(?:\s|$)")


def is_concrete_command(value: str) -> bool:
    """Return whether ``value`` begins with plausible direct executable syntax."""
    stripped = value.strip()
    if (
        _VAGUE.fullmatch(stripped)
        or _VAGUE_EXPECTED.fullmatch(stripped)
        or _SHELL_INVOCATION.match(stripped)
    ):
        return False
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False
    if not tokens:
        return False
    executable = tokens[0]
    return bool(
        _EXECUTABLE.fullmatch(executable)
        and executable.casefold().removeprefix("./") not in _NON_COMMAND_LEADS
    )


def is_forbidden_shell_invocation(value: str) -> bool:
    """Return whether ``value`` delegates verification through a shell string."""
    return _SHELL_INVOCATION.match(value.strip()) is not None


def is_specific_artifact(value: str) -> bool:
    """Return whether ``value`` names a concrete file or artifact locator."""
    stripped = value.strip()
    if not stripped or _VAGUE.fullmatch(stripped):
        return False
    if _LOCATOR.search(stripped):
        return True
    prefix, separator, locator = stripped.partition(":")
    return bool(
        separator
        and prefix.casefold() in {"artifact", "file", "report"}
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", locator.strip())
        and not _VAGUE_EXPECTED.fullmatch(locator.strip())
    )


def is_specific_expected_output(value: str) -> bool:
    """Return whether ``value`` names an observable, non-subjective result."""
    stripped = value.strip()
    if not stripped or _VAGUE.fullmatch(stripped) or _VAGUE_EXPECTED.fullmatch(stripped):
        return False
    return bool(
        len(stripped.split()) >= _MIN_SPECIFIC_OUTCOME_WORDS
        and (
            any(character.isdigit() for character in stripped)
            or _LOCATOR.search(stripped)
            or _OUTCOME_SIGNAL.search(stripped)
            or re.search(r"\b(?:created|exists?|present|absent|unchanged)\b", stripped, re.I)
        )
    )


def is_concrete_verification(method: str, expected: str) -> bool:
    """Return whether a verification has an observable method and outcome."""
    if not is_specific_expected_output(expected):
        return False
    if is_concrete_command(method):
        return True
    lowered = method.casefold()
    return lowered.startswith(("inspect ", "read ", "compare ", "review ")) and bool(
        _LOCATOR.search(method)
    )


__all__ = [
    "is_concrete_command",
    "is_concrete_verification",
    "is_forbidden_shell_invocation",
    "is_specific_artifact",
    "is_specific_expected_output",
]
