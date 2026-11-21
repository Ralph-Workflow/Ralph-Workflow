from __future__ import annotations

import os
import tempfile
import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytest_plugins = ("pytester",)

_LEAKY_SESSION = textwrap.dedent(
    """
    import functools
    import os
    import tempfile
    from pathlib import Path


    @functools.lru_cache(maxsize=1)
    def _cached_template():
        return Path(tempfile.mkdtemp(prefix="cached-template-"))


    def test_a_bare_mkdtemp_is_never_removed_by_the_test():
        (Path(tempfile.mkdtemp(prefix="bare-")) / "payload").write_text("x")
        tempfile.NamedTemporaryFile(delete=False).close()


    def test_a_process_lifetime_template_is_never_removed():
        assert _cached_template().is_dir()


    def test_child_processes_inherit_the_session_temp_directory():
        (Path(os.environ["TMPDIR"]) / "child-scratch").mkdir()
        assert os.environ["TMPDIR"] == tempfile.gettempdir()


    def test_a_failing_test_still_leaves_temp_files_behind():
        tempfile.mkdtemp(prefix="failing-")
        raise AssertionError("expected failure")
    """
)


@pytest.mark.timeout_seconds(10)
def test_temp_files_a_session_leaves_behind_are_removed_when_it_ends(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_tmp = tmp_path / "observed-tmp"
    observed_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(observed_tmp))
    monkeypatch.setenv("TMPDIR", str(observed_tmp))
    pytester.makepyfile(test_leaky=_LEAKY_SESSION)

    result = pytester.runpytest_inprocess(
        "-p", "tests._session_temp_containment", "-p", "no:randomly", "-p", "no:cacheprovider"
    )

    result.assert_outcomes(passed=3, failed=1)
    assert sorted(entry.name for entry in observed_tmp.iterdir()) == []
    assert tempfile.gettempdir() == str(observed_tmp)
    assert os.environ["TMPDIR"] == str(observed_tmp)


def test_the_suite_session_contains_its_temp_files(request: pytest.FixtureRequest) -> None:
    assert request.config.pluginmanager.has_plugin("tests._session_temp_containment")
