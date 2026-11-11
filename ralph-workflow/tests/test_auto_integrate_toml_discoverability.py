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


_AUTO_INTEGRATE_KEYS = (
    "auto_integrate_enabled",
    "auto_integrate_target",
    "auto_integrate_remote_enabled",
    "auto_integrate_remote",
    "auto_integrate_remote_interval_seconds",
    "auto_integrate_reclaim_target_worktree",
)

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

_REQUIRED_BANNER_HEADER = "AUTO-INTEGRATION (auto-rebase)"

_REQUIRED_PROSE_TOKENS: tuple[str, ...] = (
    "what the feature does",
    "the seams",
    "what it never does",
    "on-by-default local tier",
    "configured remote tier",
)

_COMMENTED_KEY_LINE_RE = re.compile(
    r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*(?:#.*)?$"
)


def _read_text(path: Path) -> str:
    return path.read_text()


def _commented_defaults(text: str) -> dict[str, str]:
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
    for idx, line in enumerate(content.splitlines()):
        if line.strip() == "[general]":
            return idx
    raise AssertionError("no [general] header line found")


def test_both_bundled_toml_templates_exist() -> None:
    for _, path in _bundled():
        assert path.is_file(), f"missing bundled TOML default: {path}"


def test_each_template_has_exactly_one_general_header() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        count = sum(1 for line in text.splitlines() if line.strip() == "[general]")
        assert count == 1, f"{path}: expected exactly one [general] header, found {count}"


def test_general_section_starts_with_auto_integration_banner() -> None:
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
    for _, path in _bundled():
        text = _read_text(path)
        block = _general_section_block(text).lower()
        missing = tuple(t for t in _REQUIRED_PROSE_TOKENS if t not in block)
        assert not missing, f"{path}: prose missing tokens: {missing!r}"


def test_templates_list_exactly_six_live_auto_integrate_keys_in_order() -> None:
    for _, path in _bundled():
        commented = _commented_defaults(_read_text(path))
        assert tuple(commented) == _AUTO_INTEGRATE_KEYS


def test_templates_omit_retired_auto_integrate_keys() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        assert not any(key in text for key in _RETIRED_AUTO_INTEGRATE_KEYS)


def test_chains_precede_general_settings() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        assert text.index("[agent_chains]") < text.index("[general]")


def test_no_duplicate_auto_integrate_key_in_a_template() -> None:
    for _, path in _bundled():
        commented = _commented_defaults(_read_text(path))
        for key in _AUTO_INTEGRATE_KEYS:
            assert key in commented, f"{path}: missing unique default for {key!r}"


def test_each_template_documents_model_defaults_in_comment_lines() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        commented = _commented_defaults(text)
        defaults = {
            "auto_integrate_enabled": True,
            "auto_integrate_target": "main",
            "auto_integrate_remote_enabled": True,
            "auto_integrate_remote": "origin",
            "auto_integrate_remote_interval_seconds": 0.0,
            "auto_integrate_reclaim_target_worktree": True,
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
    for _, path in _bundled():
        with path.open("rb") as fp:
            tomllib.load(fp)


def test_banner_appears_at_top_of_general_block_not_middle() -> None:
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
                continue
            if _REQUIRED_BANNER_HEADER.lower() in stripped.lower():
                banner_at = offset
                break
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
    for _, path in _bundled():
        block = _general_section_block(_read_text(path))
        for forbidden in ("[agent_drains]", "[agent_chains]", "[agents"):
            assert forbidden not in block, (
                f"{path}: [general] block contains a forbidden sub-table "
                f"header {forbidden!r}; the auto-integration block must be "
                f"the FIRST content under [general]"
            )
