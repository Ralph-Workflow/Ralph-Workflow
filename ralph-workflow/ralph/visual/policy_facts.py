"""Parse design-system policy facts into typed, validated structures.

The visual module enforces that any visual capture session is built
from a project's verified design-system policy, never from agent
intuition. This module is the single source of truth for that parsing
step: it pulls ``RALPH-FACT`` lines out of the policy markdown,
validates the bare minimum for a usable capture matrix (default plus
empty/loading/error/overflow states across narrow and wide viewports),
and rejects unsafe command fragments that would let a policy author
smuggle shell behaviour into the visual pipeline.

Single-screenshot defaults are explicitly rejected. A default that
captures only the happy path cannot tell the agent whether the empty,
loading, error, or overflow state regressed; anything less than the
canonical five-state matrix is a misconfiguration, not a preference.
Shell fragments (``;``, ``&&``, ``|``, ``>``, ``<``, backticks,
``$(...)``, newlines) and path-traversal sequences (``..``) inside
``design_capture_command`` are rejected so a misconfigured policy
cannot turn a capture into a shell escape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants — the canonical minimum a visual capture policy must declare.
# ---------------------------------------------------------------------------

# Required states. A policy that omits any of these collapses the
# visual review to a single-screenshot happy path check, which the
# module rejects.
REQUIRED_STATES: tuple[str, ...] = ("default", "empty", "loading", "error", "overflow")

# Default viewport sizes. 375x812 is the iPhone-class narrow viewport
# used by the multimodal package; 1440x900 is the wide desktop class
# the design-system policy starter recommends.
VIEWPORT_DEFAULT_WIDTH_NARROW: int = 375
VIEWPORT_DEFAULT_HEIGHT_NARROW: int = 812
VIEWPORT_DEFAULT_WIDTH_WIDE: int = 1440
VIEWPORT_DEFAULT_HEIGHT_WIDE: int = 900

# Default themes. The design-system policy starter enforces light +
# dark; we keep that contract here so the visual layer cannot drift
# back to light-only reviews.
DEFAULT_THEMES: tuple[str, ...] = ("light", "dark")

# Minimum number of policy-declared viewports. Narrow + wide is the
# bare minimum; a single-viewport matrix is a single-screenshot
# default in disguise and is rejected upstream.
MIN_VIEWPORTS: int = 2

# Minimum number of whitespace-separated tokens required inside
# ``design_capture_command``. A capture command must carry at least an
# executable plus a target (test path, URL, etc.); anything shorter is
# a single-screenshot default in disguise.
MIN_CAPTURE_COMMAND_TOKENS: int = 2

# Characters / fragments that signal a shell escape attempt inside
# ``design_capture_command``. The set is conservative on purpose: any
# ambiguity defaults to rejection so a misconfigured policy cannot
# promote ``design_capture_command`` into a shell primitive.
_SHELL_METACHARS: frozenset[str] = frozenset({";", "&", "|", ">", "<", "`", "$", "\n"})

# A double-dot segment anywhere in a path component is treated as
# path traversal and rejected.
_TRAVERSAL_SEGMENT = re.compile(r"(^|/)\.\.?(/|$)")

# RALPH-FACT line grammar. Captures the key + value; whitespace around
# the value is trimmed. The line MUST start with ``RALPH-FACT:`` so a
# body paragraph that happens to contain ``RALPH-FACT:`` mid-line is
# not picked up.
_FACT_LINE = re.compile(r"^RALPH-FACT:\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$")

# Known fact keys. Unknown keys are ignored (other modules may
# consume them) but a misspelling of the canonical keys surfaces as
# an unresolved fact.
_KEY_DESIGN_CAPTURE_COMMAND = "design_capture_command"
_KEY_DESIGN_TARGET = "design_target"
_KEY_NARROW_VIEWPORT = "narrow_viewport"
_KEY_WIDE_VIEWPORT = "wide_viewport"
_KEY_VIEWPORTS = "viewports"
_KEY_THEMES = "themes"
_KEY_STATES = "states"

_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        _KEY_DESIGN_CAPTURE_COMMAND,
        _KEY_DESIGN_TARGET,
        _KEY_NARROW_VIEWPORT,
        _KEY_WIDE_VIEWPORT,
        _KEY_VIEWPORTS,
        _KEY_THEMES,
        _KEY_STATES,
    }
)


# ---------------------------------------------------------------------------
# Typed structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Viewport:
    """A named capture viewport (resolution) declared in policy."""

    name: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Viewport.name must be a non-empty string")
        if self.width <= 0:
            raise ValueError(f"Viewport.width must be positive, got {self.width}")
        if self.height <= 0:
            raise ValueError(f"Viewport.height must be positive, got {self.height}")


@dataclass(frozen=True)
class _PolicyFacts:
    """Validated design-system policy facts for a single capture target."""

    design_capture_command: str
    target: str
    viewports: tuple[Viewport, ...]
    themes: tuple[str, ...]
    states: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.target or not self.target.strip():
            raise ValueError("PolicyFacts.target must be a non-empty string")
        if len(self.viewports) < MIN_VIEWPORTS:
            raise ValueError(
                f"PolicyFacts.viewports must contain at least {MIN_VIEWPORTS} entries "
                f"(narrow + wide); got {len(self.viewports)}"
            )
        if not self.themes:
            raise ValueError("PolicyFacts.themes must contain at least one theme")
        if len(self.states) < len(REQUIRED_STATES):
            raise ValueError(
                "PolicyFacts.states must include the full canonical set "
                f"{list(REQUIRED_STATES)}; got {list(self.states)} — "
                "single-screenshot defaults are rejected"
            )
        missing = [state for state in REQUIRED_STATES if state not in self.states]
        if missing:
            raise ValueError(
                f"PolicyFacts.states is missing required states {missing}; "
                "single-screenshot defaults are rejected"
            )


PolicyFacts = _PolicyFacts
PolicyFacts.__name__ = "PolicyFacts"
PolicyFacts.__qualname__ = "PolicyFacts"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_viewport_token(token: str, *, fallback_name: str) -> Viewport:
    """Parse a ``WxH`` token (optionally prefixed with a name) into a Viewport."""
    cleaned = token.strip()
    if not cleaned:
        raise ValueError(f"viewport token must not be empty (got {fallback_name!r})")
    name: str
    resolution: str
    if "x" not in cleaned and "X" not in cleaned:
        raise ValueError(
            f"viewport {fallback_name!r} must contain a WxH resolution (got {cleaned!r})"
        )
    if cleaned[0].isdigit():
        name = fallback_name
        resolution = cleaned
    else:
        head, _, tail = cleaned.rpartition("x")
        if not head or "x" in head:
            raise ValueError(
                f"viewport {fallback_name!r} must be in the form NAME=WxH or WxH "
                f"(got {cleaned!r})"
            )
        name = head.strip().lower()
        resolution = tail.strip()
    match = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)", resolution)
    if match is None:
        raise ValueError(
            f"viewport {fallback_name!r} must declare positive integer width and height "
            f"(got {resolution!r})"
        )
    width = int(match.group(1))
    height = int(match.group(2))
    return Viewport(name=name, width=width, height=height)


def _split_csv(value: str) -> list[str]:
    """Split a comma-separated value into trimmed, deduplicated tokens."""
    seen: dict[str, None] = {}
    for raw in value.split(","):
        token = raw.strip()
        if token and token not in seen:
            seen[token] = None
    return list(seen)


def _validate_capture_command(command: str) -> None:
    """Reject shell fragments and path-traversal inside ``design_capture_command``."""
    if not command or not command.strip():
        raise ValueError("design_capture_command must be a non-empty string")
    bad = sorted(c for c in _SHELL_METACHARS if c in command)
    if bad:
        raise ValueError(
            "design_capture_command must not contain shell metacharacters "
            f"(rejected: {bad!r}); this is a capture command, not a shell snippet"
        )
    if _TRAVERSAL_SEGMENT.search(command):
        raise ValueError(
            "design_capture_command must not contain path-traversal sequences ('..'); "
            "capture paths must stay within the project root"
        )
    tokens = command.split()
    if len(tokens) < MIN_CAPTURE_COMMAND_TOKENS:
        raise ValueError(
            "design_capture_command must contain at least an executable and a target "
            f"(got {command!r}); single-screenshot defaults are rejected"
        )


def _extract_facts(policy_markdown: str) -> dict[str, str]:
    """Walk a policy markdown and pull out the ``RALPH-FACT:`` lines."""
    if not isinstance(policy_markdown, str):
        raise ValueError("policy_markdown must be a string")
    facts: dict[str, str] = {}
    for line in policy_markdown.splitlines():
        stripped = line.strip()
        match = _FACT_LINE.match(stripped)
        if match is None:
            continue
        key, value = match.group(1), match.group(2)
        # First occurrence wins; later occurrences are ignored so a
        # policy cannot redefine a fact mid-document.
        facts.setdefault(key, value)
    return facts


def parse_policy_facts(policy_markdown: str, *, target: str | None = None) -> PolicyFacts:
    """Parse and validate a managed-repo policy markdown into PolicyFacts.

    Raises ``ValueError`` for any of:

    * Missing required RALPH-FACT entries (command, target, viewports, themes, states).
    * ``design_capture_command`` that contains shell metacharacters or ``..`` traversal.
    * A state set that omits one or more canonical states (single-screenshot default).
    * A viewport set with fewer than two entries (single-viewport default).
    * An empty themes list.

    The ``target`` keyword overrides any ``design_target`` fact in the
    markdown; supply it when the caller already resolved the target
    upstream (e.g. from a plan item).
    """
    facts = _extract_facts(policy_markdown)

    if _KEY_DESIGN_CAPTURE_COMMAND not in facts:
        raise ValueError(
            "policy is missing required fact "
            f"{_KEY_DESIGN_CAPTURE_COMMAND!r}; the visual pipeline cannot "
            "construct a capture command from intuition"
        )
    command = facts[_KEY_DESIGN_CAPTURE_COMMAND]
    _validate_capture_command(command)

    resolved_target = target if target is not None else facts.get(_KEY_DESIGN_TARGET)
    if not resolved_target or not resolved_target.strip():
        raise ValueError(
            "policy is missing required fact "
            f"{_KEY_DESIGN_TARGET!r}; pass target= explicitly or set "
            "RALPH-FACT: design_target: ... in the policy"
        )

    # Viewports: accept either individual ``narrow_viewport`` /
    # ``wide_viewport`` facts OR a combined ``viewports`` CSV. Either
    # path produces the same canonical tuple.
    viewports: list[Viewport] = []
    if _KEY_VIEWPORTS in facts:
        tokens = _split_csv(facts[_KEY_VIEWPORTS])
        if len(tokens) < MIN_VIEWPORTS:
            raise ValueError(
                f"{_KEY_VIEWPORTS} fact must list at least {MIN_VIEWPORTS} "
                f"viewports; got {len(tokens)}"
            )
        for index, token in enumerate(tokens):
            viewports.append(_parse_viewport_token(token, fallback_name=f"viewport_{index}"))
    else:
        if _KEY_NARROW_VIEWPORT not in facts or _KEY_WIDE_VIEWPORT not in facts:
            raise ValueError(
                "policy must declare either "
                f"{_KEY_VIEWPORTS!r} or both "
                f"{_KEY_NARROW_VIEWPORT!r} and {_KEY_WIDE_VIEWPORT!r}"
            )
        viewports.append(
            _parse_viewport_token(
                facts[_KEY_NARROW_VIEWPORT], fallback_name=_KEY_NARROW_VIEWPORT
            )
        )
        viewports.append(
            _parse_viewport_token(
                facts[_KEY_WIDE_VIEWPORT], fallback_name=_KEY_WIDE_VIEWPORT
            )
        )

    if _KEY_THEMES not in facts:
        raise ValueError(
            f"policy is missing required fact {_KEY_THEMES!r}; a theme-less "
            "capture cannot surface contrast regressions"
        )
    themes = tuple(_split_csv(facts[_KEY_THEMES]))
    if not themes:
        raise ValueError(f"{_KEY_THEMES} fact must list at least one theme")

    if _KEY_STATES not in facts:
        raise ValueError(
            f"policy is missing required fact {_KEY_STATES!r}; a single-screenshot "
            "default is rejected — the policy must enumerate at least "
            f"{list(REQUIRED_STATES)}"
        )
    states = tuple(_split_csv(facts[_KEY_STATES]))

    return PolicyFacts(
        design_capture_command=command,
        target=resolved_target.strip(),
        viewports=tuple(viewports),
        themes=themes,
        states=states,
    )


def default_policy_facts(*, target: str, command: str) -> PolicyFacts:
    """Build a PolicyFacts using the canonical defaults.

    Useful for tests and for the project bootstrap path where the
    policy has not yet been rewritten from its starter template.
    Raises ``ValueError`` for unsafe commands (same rules as
    :func:`parse_policy_facts`).
    """
    _validate_capture_command(command)
    if not target or not target.strip():
        raise ValueError("target must be a non-empty string")
    return PolicyFacts(
        design_capture_command=command,
        target=target.strip(),
        viewports=(
            Viewport(
                name="narrow",
                width=VIEWPORT_DEFAULT_WIDTH_NARROW,
                height=VIEWPORT_DEFAULT_HEIGHT_NARROW,
            ),
            Viewport(
                name="wide",
                width=VIEWPORT_DEFAULT_WIDTH_WIDE,
                height=VIEWPORT_DEFAULT_HEIGHT_WIDE,
            ),
        ),
        themes=DEFAULT_THEMES,
        states=REQUIRED_STATES,
    )


__all__ = [
    "DEFAULT_THEMES",
    "MIN_VIEWPORTS",
    "REQUIRED_STATES",
    "VIEWPORT_DEFAULT_HEIGHT_NARROW",
    "VIEWPORT_DEFAULT_HEIGHT_WIDE",
    "VIEWPORT_DEFAULT_WIDTH_NARROW",
    "VIEWPORT_DEFAULT_WIDTH_WIDE",
    "PolicyFacts",
    "Viewport",
    "default_policy_facts",
    "parse_policy_facts",
]
