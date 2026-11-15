from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ralph.policy.loader import load_policy
from ralph.policy.validation import PolicyValidationError

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"

_STALE_ARTIFACT_TYPE = "commit_" + "cleanup"


def _config_dir_with_artifact_type(tmp_path: Path, artifact_type: str) -> Path:
    config_dir = tmp_path / ".agent"
    config_dir.mkdir(parents=True)
    for filename in ("agents.toml", "pipeline.toml", "artifacts.toml"):
        shutil.copy(DEFAULT_POLICY_DIR / filename, config_dir / filename)
    artifacts_path = config_dir / "artifacts.toml"
    source = artifacts_path.read_text(encoding="utf-8")
    assert 'artifact_type = "commit_review"' in source
    artifacts_path.write_text(
        source.replace(
            'artifact_type = "commit_review"',
            f'artifact_type = "{artifact_type}"',
        ),
        encoding="utf-8",
    )
    return config_dir


def test_default_policy_loads_with_supported_artifact_types() -> None:
    bundle = load_policy(DEFAULT_POLICY_DIR)

    assert bundle.artifacts.artifacts["development_commit_review_artifact"].artifact_type == (
        "commit_review"
    )


@pytest.mark.parametrize(
    "artifact_type",
    (_STALE_ARTIFACT_TYPE, "not_a_registered_artifact"),
)
def test_unsupported_artifact_type_fails_the_policy_load(
    tmp_path: Path,
    artifact_type: str,
) -> None:
    config_dir = _config_dir_with_artifact_type(tmp_path, artifact_type)

    with pytest.raises(PolicyValidationError) as excinfo:
        load_policy(config_dir)

    message = excinfo.value.message
    assert artifact_type in message
    assert "commit" in message
    assert "commit_review" in message
    assert "development_commit_review" in message
    assert excinfo.value.source == "artifacts"
