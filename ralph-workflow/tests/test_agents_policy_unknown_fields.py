from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from ralph.policy.loader import PolicyValidationError, default_dir, load_policy


def test_bundled_development_timebox_does_not_warn_as_unknown() -> None:
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        bundle = load_policy(default_dir())
    finally:
        logger.remove(sink_id)

    assert bundle.pipeline.development_timebox is not None
    assert not any("development_timebox" in record for record in records)


def test_development_timebox_in_agents_toml_names_its_correct_home(tmp_path: Path) -> None:
    (tmp_path / "agents.toml").write_text(
        "[development_timebox]\nduration_seconds = 600\n",
        encoding="utf-8",
    )

    with pytest.raises(PolicyValidationError, match="development_timebox.*pipeline\\.toml"):
        load_policy(tmp_path, strict_optional_files=True)


def test_unknown_agents_toml_field_warns_and_is_rejected(tmp_path: Path) -> None:
    agents_path = tmp_path / "agents.toml"
    agents_path.write_text("unexpected_setting = true\n", encoding="utf-8")
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        with pytest.raises(PolicyValidationError, match="unexpected_setting"):
            load_policy(tmp_path, strict_optional_files=True)
    finally:
        logger.remove(sink_id)

    assert any("unexpected_setting" in record and str(agents_path) in record for record in records)
