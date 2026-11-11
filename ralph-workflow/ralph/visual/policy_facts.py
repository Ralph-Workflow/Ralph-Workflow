
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


REQUIRED_STATES: tuple[str, ...] = ("default", "empty", "loading", "error", "overflow")

VIEWPORT_DEFAULT_WIDTH_NARROW: int = 375
VIEWPORT_DEFAULT_HEIGHT_NARROW: int = 812
VIEWPORT_DEFAULT_WIDTH_WIDE: int = 1440
VIEWPORT_DEFAULT_HEIGHT_WIDE: int = 900

DEFAULT_THEMES: tuple[str, ...] = ("light", "dark")

MIN_VIEWPORTS: int = 2

MIN_CAPTURE_COMMAND_TOKENS: int = 2

MAX_CAPTURE_COMMAND_TOKENS: int = 32

DESIGN_SYSTEM_POLICY_RELPATH: str = "docs/ralph-workflow-policy/design-system-policy.md"

_SHELL_METACHARS: frozenset[str] = frozenset({";", "&", "|", ">", "<", "`", "$", "\n"})

_TRAVERSAL_SEGMENT = re.compile(r"(^|/)\.\.?(/|$)")

_TRAVERSAL_ARGV_SEGMENT = re.compile(r"(^|[/=])\.\.(/|$)")

_FACT_LINE = re.compile(r"^RALPH-FACT:\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$")

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




@dataclass(frozen=True)
class Viewport:

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




def _parse_viewport_token(token: str, *, fallback_name: str) -> Viewport:
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
                f"viewport {fallback_name!r} must be in the form NAME=WxH or WxH (got {cleaned!r})"
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
    seen: dict[str, None] = {}
    for raw in value.split(","):
        token = raw.strip()
        if token and token not in seen:
            seen[token] = None
    return list(seen)


def validate_capture_command(command: str) -> None:
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
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"design_capture_command must be shell-parseable: {exc}") from exc
    if any(_TRAVERSAL_ARGV_SEGMENT.search(token) for token in tokens):
        raise ValueError(
            "design_capture_command must not contain path-traversal sequences ('..'); "
            "capture paths must stay within the project root"
        )
    if len(tokens) < MIN_CAPTURE_COMMAND_TOKENS:
        raise ValueError(
            "design_capture_command must contain at least an executable and a target "
            f"(got {command!r}); single-screenshot defaults are rejected"
        )
    if len(tokens) > MAX_CAPTURE_COMMAND_TOKENS:
        raise ValueError(
            "design_capture_command declares too many argv tokens "
            f"({len(tokens)} > {MAX_CAPTURE_COMMAND_TOKENS})"
        )


def _extract_facts(policy_markdown: str) -> dict[str, str]:
    if not isinstance(policy_markdown, str):
        raise ValueError("policy_markdown must be a string")
    facts: dict[str, str] = {}
    for line in policy_markdown.splitlines():
        stripped = line.strip()
        match = _FACT_LINE.match(stripped)
        if match is None:
            continue
        key, value = match.group(1), match.group(2)
        facts.setdefault(key, value)
    return facts


def capture_command_problem(policy_markdown: str) -> str | None:
    command = _extract_facts(policy_markdown).get(_KEY_DESIGN_CAPTURE_COMMAND)
    if command is None:
        return None
    try:
        validate_capture_command(command)
    except ValueError as exc:
        return str(exc)
    return None


def parse_policy_facts(policy_markdown: str, *, target: str | None = None) -> PolicyFacts:
    facts = _extract_facts(policy_markdown)

    if _KEY_DESIGN_CAPTURE_COMMAND not in facts:
        raise ValueError(
            "policy is missing required fact "
            f"{_KEY_DESIGN_CAPTURE_COMMAND!r}; the visual pipeline cannot "
            "construct a capture command from intuition"
        )
    command = facts[_KEY_DESIGN_CAPTURE_COMMAND]
    validate_capture_command(command)

    resolved_target = target if target is not None else facts.get(_KEY_DESIGN_TARGET)
    if not resolved_target or not resolved_target.strip():
        raise ValueError(
            "policy is missing required fact "
            f"{_KEY_DESIGN_TARGET!r}; pass target= explicitly or set "
            "RALPH-FACT: design_target: ... in the policy"
        )

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
            _parse_viewport_token(facts[_KEY_NARROW_VIEWPORT], fallback_name=_KEY_NARROW_VIEWPORT)
        )
        viewports.append(
            _parse_viewport_token(facts[_KEY_WIDE_VIEWPORT], fallback_name=_KEY_WIDE_VIEWPORT)
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
    validate_capture_command(command)
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
    "DESIGN_SYSTEM_POLICY_RELPATH",
    "MAX_CAPTURE_COMMAND_TOKENS",
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
