"""Discoverability pinning for the auto-integration block in both bundled TOML defaults.

AC-51 / AC-52 / AC-53 / AC-54 from ``.agent/PRODUCT_CRITERIA.md``:

* Every ``auto_integrate_*`` key (including the new remote-sync keys)
  is discoverable at the TOP of ``[general]`` in BOTH bundled
  templates, under a bannered block titled
  ``AUTO-INTEGRATION (auto-rebase) — keep the feature branch and the
  mainline in lockstep``.
* The block is preceded by prose that explains what the feature does,
  when it runs (the seams), what it never does, and the on-by-default
  local tier vs the opt-in remote-sync tier.
* Both templates parse as TOML and, when the keyed lines are
  uncommented at their documented defaults, load to the exact
  defaults the model ships with.

These tests pin the discoverability contract from outside the docs and
TOML parsers; a regression that drops a key, scatters the keys across
the file, or omits the banner is caught here.

The bundled defaults directory is resolved via the same lazy helper
``ralph.config.bootstrap._get_bundled_defaults_dir`` so a packaged
``ralph-workflow.toml`` is found whether it runs from the wheel or
from the repo working tree.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator
from pathlib import Path

from ralph.config import bootstrap as _config_bootstrap


def _bundled_paths() -> tuple[Path, Path]:
    base = _config_bootstrap._get_bundled_defaults_dir()
    return (base / "ralph-workflow.toml", base / "ralph-workflow-local.toml")


def _bundled() -> Iterator[tuple[str, Path]]:
    for path in _bundled_paths():
        yield path.name, path


#: The four operator-facing auto-integration keys, in documented order.
_AUTO_INTEGRATE_KEYS = (
    "auto_integrate_enabled",
    "auto_integrate_target",
    "auto_integrate_remote_enabled",
    "auto_integrate_remote",
)

#: Removed names must not reappear in bundled operator templates.
_RETIRED_AUTO_INTEGRATE_KEYS = (
    "auto_integrate_fetch_enabled",
    "auto_integrate_push_enabled",
    "auto_integrate_remote_sync_enabled",
    "auto_integrate_remote_target",
    "auto_integrate_fetch_timeout_seconds",
    "auto_integrate_push_timeout_seconds",
    "auto_integrate_resolve_timeout_seconds",
    "auto_integrate_remote_sync_interval_seconds",
    "auto_integrate_remote_backoff_max_seconds",
    "auto_integrate_remote_wait_seconds",
)

#: Banner header the discoverability rubric requires at the TOP of
#: the auto-integration block. The exact wording is the operator-visible
#: contract.
_REQUIRED_BANNER_HEADER = "AUTO-INTEGRATION (auto-rebase)"

#: Prose the discoverability rubric requires immediately above the
#: auto-integration block: what the feature does, when it runs (the
#: seams), what it never does, and the on-by-default local tier vs
#: the opt-in remote tier. Phrase tokens are short, individually
#: greppable, and stable.
_REQUIRED_PROSE_TOKENS: tuple[str, ...] = (
    "what the feature does",
    "the seams",
    "what it never does",
    "on-by-default local tier",
    "opt-in remote tier",
)

#: Pattern to find a commented line that starts with ``# key =`` so we can
#: extract the documented default. Lines that start with the key but are
#: not commented are not part of this discovery surface (a key must be
#: commented out so the operator's edit is unambiguous).
_COMMENTED_KEY_LINE_RE = re.compile(
    r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*(?:#.*)?$"
)


def _read_text(path: Path) -> str:
    return path.read_text()


def _commented_defaults(text: str) -> dict[str, str]:
    """Extract ``(key -> rhs-string)`` pairs from commented ``# key = rhs`` lines.

    Lines with a ``#`` comment that begins a line are scanned; lines that
    start with whitespace then ``#`` count too -- the file's existing
    style wraps ``# key = value`` either with or without leading
    whitespace. The result is a stable mapping from config key to the
    raw RHS string written by the editor so the test can compare
    against the model default.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        body = stripped[1:].lstrip()
        match = _COMMENTED_KEY_LINE_RE.match(body)
        if not match:
            continue
        key = match.group("key")
        if not key.startswith("auto_integrate_"):
            continue
        if key in out:
            raise AssertionError(f"duplicate commented key {key!r}; rubric requires one entry")
        out[key] = match.group("value")
    return out


def _general_section_block(content: str) -> str:
    """Return the text of the ``[general]`` table for assertions.

    Starts at the first ``[general]`` header (NOT ``[general.workflow]``)
    and ends at the next ``[``-prefixed header. The end-marker
    requirement stops a regression that inserts a duplicate
    ``[general]`` table later in the file.
    """
    lines = content.splitlines()
    header_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "[general]":
            header_idx = idx
            break
    if header_idx is None:
        raise AssertionError("no [general] header found; the rubrick requires one per file")
    end_at: int | None = None
    for idx in range(header_idx + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end_at = idx
            break
    if end_at is None:
        end_at = len(lines)
    return "\n".join(lines[header_idx:end_at])


def _general_header_index(content: str) -> int:
    """Return the 0-based line index of the first ``[general]`` header.

    Counts only ``[general]`` exactly -- not ``[general.workflow]`` --
    so the discoverability rubric cannot be accidentally met by a
    sub-section that lives under ``[general.workflow]``.
    """
    for idx, line in enumerate(content.splitlines()):
        if line.strip() == "[general]":
            return idx
    raise AssertionError("no [general] header line found")


def test_both_bundled_toml_templates_exist() -> None:
    """Both bundled TOML defaults must exist on disk."""
    for _, path in _bundled():
        assert path.is_file(), f"missing bundled TOML default: {path}"


def test_each_template_has_exactly_one_general_header() -> None:
    """Each bundled template carries exactly ONE top-level ``[general]`` header.

    A regression that duplicates the header (e.g. a copy-paste from a
    fork) would silently lose keys; the test catches it.
    """
    for _, path in _bundled():
        text = _read_text(path)
        count = sum(1 for line in text.splitlines() if line.strip() == "[general]")
        assert count == 1, f"{path}: expected exactly one [general] header, found {count}"


def test_general_section_starts_with_auto_integration_banner() -> None:
    """The ``[general]`` block MUST start with the auto-integration banner.

    The banner must appear at the TOP of ``[general]`` so the
    on-by-default local tier and the opt-in remote-sync tier are the
    FIRST thing an operator sees when editing the configuration.
    """
    for _, path in _bundled():
        text = _read_text(path)
        block = _general_section_block(text).strip()
        first_lines = "\n".join(block.splitlines()[:8])
        assert _REQUIRED_BANNER_HEADER.lower() in first_lines.lower(), (
            f"{path}: auto-integration block must start with the "
            f"{_REQUIRED_BANNER_HEADER!r} banner; first 8 lines: "
            f"{first_lines!r}"
        )


def test_general_section_contains_required_prose_tokens() -> None:
    """The block's prose MUST enumerate the four discoverability rubric sections.

    AC-52 / AC-53 require prose on: what the feature does, when it
    runs (the seams), what it never does, and the on-by-default local
    tier vs the opt-in remote tier.
    """
    for _, path in _bundled():
        text = _read_text(path)
        block = _general_section_block(text).lower()
        missing = tuple(t for t in _REQUIRED_PROSE_TOKENS if t not in block)
        assert not missing, f"{path}: prose missing tokens: {missing!r}"


def test_templates_list_exactly_four_live_auto_integrate_keys_in_order() -> None:
    """S-7: templates expose the exact four-key surface in documented order."""
    for _, path in _bundled():
        commented = _commented_defaults(_read_text(path))
        assert tuple(commented) == _AUTO_INTEGRATE_KEYS


def test_templates_omit_retired_auto_integrate_keys() -> None:
    """S-7: removed key names are not discoverable from bundled templates."""
    for _, path in _bundled():
        text = _read_text(path)
        assert not any(key in text for key in _RETIRED_AUTO_INTEGRATE_KEYS)


def test_chains_precede_general_settings() -> None:
    """Routing comes before tuning knobs in both main templates.

    Supersedes the earlier S-7 ordering rule, which required ``[agents.*]``
    to precede ``[agent_chains]``. Agent definitions are transport plumbing
    (binary, flags, output parser) and no longer live in these files at all
    -- they moved to ``ralph-workflow-agents.toml`` so the main config opens
    on the one section operators actually edit. Their absence here is pinned
    by ``tests/config/test_agents_config_file.py``.
    """
    for _, path in _bundled():
        text = _read_text(path)
        assert text.index("[agent_chains]") < text.index("[general]")


def test_no_duplicate_auto_integrate_key_in_a_template() -> None:
    """Each ``auto_integrate_*`` key must appear EXACTLY once per template.

    A regression where a key is duplicated (e.g. an operator copy-pasting
    the block) leaves the operator with two commented lines. This guard
    forces a duplicate to be resolved into one entry.
    """
    for _, path in _bundled():
        commented = _commented_defaults(_read_text(path))
        for key in _AUTO_INTEGRATE_KEYS:
            assert key in commented, f"{path}: missing unique default for {key!r}"


def test_each_template_documents_model_defaults_in_comment_lines() -> None:
    """For every auto_integrate key the commented ``# key = ...`` line must
    mention the key AND parse as valid TOML.

    AC-54 / S-10 requires the commented lines to match the model
    defaults when uncommented. ``None`` defaults (e.g.
    ``auto_integrate_target``) are not representable in TOML, so the
    test requires a TOML-parseable RHS for non-``None`` defaults and
    requires the parsed value to equal the model default. ``None``
    defaults only require the key to be present in a commented line.
    """
    for _, path in _bundled():
        text = _read_text(path)
        commented = _commented_defaults(text)
        defaults = {
            "auto_integrate_enabled": True,
            "auto_integrate_target": "main",
            "auto_integrate_remote_enabled": False,
            "auto_integrate_remote": "origin",
        }
        for key, default_value in defaults.items():
            rhs = commented[key]
            synthesized = f"_probe_ = {rhs}\n"
            try:
                parsed = tomllib.loads(synthesized)
            except tomllib.TOMLDecodeError as exc:
                raise AssertionError(
                    f"{path}: commented default for {key!r} is not valid "
                    f"TOML ({exc!r}); got: {rhs!r}"
                ) from exc
            assert parsed["_probe_"] == default_value, (
                f"{path}: commented default for {key!r} = {rhs!r} "
                f"(parsed {parsed['_probe_']!r}) does not match documented "
                f"default {default_value!r}"
            )


def test_each_template_parses_as_toml() -> None:
    """Both templates must parse as TOML without raising.

    A regression that introduces invalid TOML syntax (e.g. an unclosed
    block comment around a banner) fails here before any other assertion.
    """
    for _, path in _bundled():
        with path.open("rb") as fp:
            tomllib.load(fp)


def test_banner_appears_at_top_of_general_block_not_middle() -> None:
    """The banner must appear directly after the ``[general]`` header.

    Catches the precise failure mode the rubric guards against: keys
    placed AFTER dozens of unrelated ``[general]`` settings, so an
    operator opening the file never gets to them. The banner header
    (the words ``AUTO-INTEGRATION (auto-rebase)``) is allowed to be
    preceded by a decorative separator line of ``#``/``=``/``-``
    characters; any meaningful ``[general]`` setting before the
    banner fails the test.
    """
    for _, path in _bundled():
        text = _read_text(path)
        lines = text.splitlines()
        header_idx = _general_header_index(text)
        decorative = re.compile(r"^[\s#=_-]+$")
        banner_at: int | None = None
        for offset, line in enumerate(lines[header_idx + 1 :], start=header_idx + 1):
            stripped = line.strip()
            if not stripped:
                continue
            if decorative.match(stripped):
                # Tolerate a single decorative separator line that
                # often introduces a bannered section.
                continue
            if _REQUIRED_BANNER_HEADER.lower() in stripped.lower():
                banner_at = offset
                break
            # First non-blank, non-banner, non-decorative line means
            # the banner was placed AFTER unrelated settings; the
            # failure message below names the offending line.
            first_unrelated = offset
            first_unrelated_text = stripped
            break
        else:
            first_unrelated = None
            first_unrelated_text = None
        assert banner_at is not None, (
            f"{path}: the {_REQUIRED_BANNER_HEADER!r} banner must "
            f"appear directly after the [general] header (line "
            f"{header_idx + 1}); the first non-decorative line after "
            f"the header is at {first_unrelated + 1 if first_unrelated is not None else '(end)'} "
            f"with content: {first_unrelated_text!r}"
        )


def test_no_unrelated_subsection_inside_general_block() -> None:
    """The ``[general]`` block must NOT contain unrelated sub-tables.

    AC-51 says auto-integration lands directly after ``[agent_chains]`` /
    ``[agent_drains]`` and BEFORE every other ``[general]`` key. A
    regression where another table appears INSIDE the auto-integration
    block is caught.
    """
    for _, path in _bundled():
        block = _general_section_block(_read_text(path))
        for forbidden in ("[agent_drains]", "[agent_chains]", "[agents"):
            assert forbidden not in block, (
                f"{path}: [general] block contains a forbidden sub-table "
                f"header {forbidden!r}; the auto-integration block must be "
                f"the FIRST content under [general]"
            )
