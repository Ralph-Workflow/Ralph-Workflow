from importlib.resources import files


def test_shipped_skill_manifest_is_not_a_mirror_manifest() -> None:
    import json

    document = json.loads(files("ralph.skills").joinpath("content/metadata.json").read_text())
    assert set(document) == {"skills"}
    assert isinstance(document["skills"], list)
    assert all(isinstance(name, str) for name in document["skills"])
