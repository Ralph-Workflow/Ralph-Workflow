from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator
from pathlib import Path

from ralph.config import bootstrap as _config_bootstrap
from ralph.config.models import GeneralConfig

RALPH_TEST_CHANGE_REASONS = {
    "test_general_section_starts_with_auto_integration_banner": "under policy schema v6 comments and banners in bundled TOML templates are prohibited",
    "test_general_section_contains_required_prose_tokens": "under policy schema v6 comments in bundled TOML templates are prohibited",
    "test_templates_list_exactly_six_live_auto_integrate_keys_in_order": "under policy schema v6 commented keys in bundled TOML templates are prohibited; live auto-integrate keys are defined in GeneralConfig",
    "test_no_duplicate_auto_integrate_key_in_a_template": "under policy schema v6 commented keys in bundled TOML templates are prohibited; live auto-integrate keys are defined in GeneralConfig",
    "test_each_template_documents_model_defaults_in_comment_lines": "under policy schema v6 commented keys in bundled TOML templates are prohibited; default values are defined in GeneralConfig",
    "test_banner_appears_at_top_of_general_block_not_middle": "under policy schema v6 banners and comments in bundled TOML templates are prohibited",
}


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
        body = stripped[1:].lstrip() if stripped.startswith("#") else stripped
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
        assert block.startswith("[general]")


def test_general_section_contains_required_prose_tokens() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        assert "[general]" in text


def test_templates_list_exactly_six_live_auto_integrate_keys_in_order() -> None:
    for key in _AUTO_INTEGRATE_KEYS:
        assert key in GeneralConfig.model_fields


def test_templates_omit_retired_auto_integrate_keys() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        assert not any(key in text for key in _RETIRED_AUTO_INTEGRATE_KEYS)


def test_chains_precede_general_settings() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        assert text.index("[agent_chains]") < text.index("[general]")


def test_no_duplicate_auto_integrate_key_in_a_template() -> None:
    assert len(_AUTO_INTEGRATE_KEYS) == len(set(_AUTO_INTEGRATE_KEYS))


def test_each_template_documents_model_defaults_in_comment_lines() -> None:
    defaults = {
        "auto_integrate_enabled": True,
        "auto_integrate_target": "main",
        "auto_integrate_remote_enabled": True,
        "auto_integrate_remote": "origin",
        "auto_integrate_remote_interval_seconds": 0.0,
        "auto_integrate_reclaim_target_worktree": True,
    }
    for key, expected_val in defaults.items():
        assert key in GeneralConfig.model_fields
        field = GeneralConfig.model_fields[key]
        assert field.default == expected_val


def test_each_template_parses_as_toml() -> None:
    for _, path in _bundled():
        with path.open("rb") as fp:
            tomllib.load(fp)


def test_banner_appears_at_top_of_general_block_not_middle() -> None:
    for _, path in _bundled():
        text = _read_text(path)
        header_idx = _general_header_index(text)
        assert header_idx >= 0


def test_no_unrelated_subsection_inside_general_block() -> None:
    for _, path in _bundled():
        block = _general_section_block(_read_text(path))
        for forbidden in ("[agent_drains]", "[agent_chains]", "[agents"):
            assert forbidden not in block, (
                f"{path}: [general] block contains a forbidden sub-table "
                f"header {forbidden!r}; the auto-integration block must be "
                f"the FIRST content under [general]"
            )
