from importlib.resources import files


def test_mirror_freshness_audit_is_not_shipped() -> None:
    assert not files("ralph.testing").joinpath("audit_skill_mirror_freshness.py").is_file()


def test_artifact_skill_types_audit_is_shipped() -> None:
    assert files("ralph.testing").joinpath("audit_artifact_skill_types.py").is_file()
