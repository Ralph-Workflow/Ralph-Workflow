"""Runtime integration tests for the run-start skill-sync seam.

The seam under test is the PUBLIC ``sync_shipped_skills`` entry point
re-exported by :mod:`ralph.cli.commands.run`. Every case drives it through
the PUBLIC ``SetupDependencies`` dataclass, so the tests reach no private
Ralph module and no private attribute:

* the universal-skill updater is observed through the PUBLIC
  ``ralph.skills.upstream.maybe_update_universal_skills`` name;
* the user-global retired-skill migration is observed through the
  FILESYSTEM it produces (a retired managed directory disappears, a
  user-edited or user-owned directory survives).

The tests are hermetic: ``HOME``, ``XDG_CONFIG_HOME`` and
``XDG_CACHE_HOME`` point into ``tmp_path``, so no real home directory and
no real upstream request is ever touched.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import run as run_module
from ralph.skills import get_skill_content
from ralph.skills.upstream import UpdateOutcome

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = [pytest.mark.timeout_seconds(15)]

#: A skill name that the shipped bundle does NOT contain, so the prune
#: treats a managed directory with that name as RETIRED.
RETIRED_SKILL_NAME = "brainstorming"


@dataclass(frozen=True)
class _Sandbox:
    """The isolated user-global roots every case runs against.

    Attributes:
        home: The fake ``$HOME`` the user-global skill roots resolve from.
        config: The fake ``$XDG_CONFIG_HOME`` the kill-switch config lives in.
        cache: The fake ``$XDG_CACHE_HOME`` the updater attempt cache lives in.
    """

    home: Path
    config: Path
    cache: Path


@dataclass
class _UpdaterProbe:
    """What the faked universal-skill updater observed.

    Attributes:
        calls: One entry per updater invocation.
        event: Set by the fake as soon as the updater was invoked.
    """

    calls: list[dict[str, object]] = field(default_factory=list)
    event: threading.Event = field(default_factory=threading.Event)


class _StubSkillManager:
    """A SkillManager stand-in whose user-global tree is always current."""

    def check_skills_for_updates(self) -> bool:
        """Report that the user-global baseline needs no repair."""
        return False


def _is_current_baseline_skill(name: str) -> bool:
    """Return True when ``name`` is still part of the shipped bundle."""
    try:
        get_skill_content(name)
    except ValueError:
        return False
    return True


def _write_managed_skill(skill_dir: Path, name: str, content: str, digest: str) -> None:
    """Create a skill directory carrying a legacy Ralph managed marker.

    Args:
        skill_dir: The directory to create.
        name: The skill name the marker records.
        content: The ``SKILL.md`` content to write.
        digest: The digest the marker records for ``SKILL.md``.
    """
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": digest,
            }
        ),
        encoding="utf-8",
    )


def _build_dependencies(
    warnings: list[str],
    *,
    install_calls: list[Path] | None = None,
    need_install: bool = False,
    warning_hook: Callable[[str], None] | None = None,
) -> run_module.SetupDependencies:
    """Build the PUBLIC setup dependencies the seam consumes.

    Args:
        warnings: The list every emitted warning is appended to.
        install_calls: The list every project-scope install root is added to.
        need_install: Whether the project-scope install stage runs.
        warning_hook: An optional extra callback invoked per warning.

    Returns:
        The dependency bundle the seam is driven with.
    """

    def _emit(message: str) -> None:
        warnings.append(message)
        if warning_hook is not None:
            warning_hook(message)

    def _retention_sweep(
        root: Path,
        *,
        keep_run_id: str | None,
        retention_max_age_seconds: float | None,
    ) -> None:
        _ = (root, keep_run_id, retention_max_age_seconds)

    def _install(workspace_root: Path) -> tuple[object, list[str]]:
        if install_calls is not None:
            install_calls.append(workspace_root)
        return (object(), [])

    return run_module.SetupDependencies(
        emit_warning=_emit,
        print_user_global_update_hint=lambda: None,
        print_project_skill_conflict_hint=lambda failures: None,
        run_retention_sweep=_retention_sweep,
        skill_manager_factory=_StubSkillManager,
        project_skills_need_install=lambda root: need_install,
        install_project_skills=_install,
    )


@pytest.fixture(autouse=True)
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Sandbox:
    """Isolate every user-global root into ``tmp_path``.

    The fixture also clears the env kill switch, so a case decides the
    kill-switch state explicitly instead of inheriting the developer's
    environment.
    """
    home = tmp_path / "home"
    config = tmp_path / "config"
    cache = tmp_path / "cache"
    for directory in (home, config, cache):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.delenv("RALPH_DISABLE_SKILL_UPDATES", raising=False)
    return _Sandbox(home=home, config=config, cache=cache)


@pytest.fixture
def updater_probe(monkeypatch: pytest.MonkeyPatch) -> _UpdaterProbe:
    """Replace the PUBLIC updater with a recording fake.

    The fake is the ONLY path to the network in these tests: when the
    kill switch is on, the fake is never called, so no request is made.
    """
    probe = _UpdaterProbe()
    # The default test lane disables the updater so no test can reach the
    # real upstream; the cases that assert the ENABLED path re-enable it.
    monkeypatch.delenv("RALPH_DISABLE_SKILL_UPDATES", raising=False)

    def _fake(
        *,
        enabled: bool,
        now: datetime,
        http: Callable[[str, float], object],
    ) -> UpdateOutcome:
        probe.calls.append({"enabled": enabled, "now": now, "http": http})
        probe.event.set()
        return UpdateOutcome(
            status="up-to-date",
            messages=("the universal skills are up to date (v-test)",),
        )

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _fake)
    return probe


def _run_seam(tmp_path: Path, dependencies: run_module.SetupDependencies) -> None:
    """Drive the PUBLIC run-start seam once."""
    run_module.sync_shipped_skills(
        workspace_root=tmp_path,
        keep_run_id=None,
        retention_max_age_seconds=None,
        dependencies=dependencies,
    )


def test_run_start_invokes_universal_skill_update_exactly_once_when_enabled(
    tmp_path: Path,
    updater_probe: _UpdaterProbe,
) -> None:
    """The updater runs once per run, on a background thread, with enabled=True."""
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert updater_probe.event.wait(timeout=5), "the universal-skill updater never ran"
    assert len(updater_probe.calls) == 1, (
        f"the updater must run exactly once per run; got {updater_probe.calls!r}"
    )
    assert updater_probe.calls[0]["enabled"] is True


def test_run_start_swallows_a_raising_universal_skill_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising updater never propagates out of the seam."""
    probe = _UpdaterProbe()

    def _raising(
        *,
        enabled: bool,
        now: datetime,
        http: Callable[[str, float], object],
    ) -> UpdateOutcome:
        probe.calls.append({"enabled": enabled, "now": now, "http": http})
        probe.event.set()
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _raising)
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert probe.event.wait(timeout=5), "the raising updater never ran"
    assert len(probe.calls) == 1


def test_run_start_reports_a_preserved_universal_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preserved universal skill is reported to the operator."""
    probe = _UpdaterProbe()
    reported = threading.Event()

    def _fake(
        *,
        enabled: bool,
        now: datetime,
        http: Callable[[str, float], object],
    ) -> UpdateOutcome:
        probe.calls.append({"enabled": enabled, "now": now, "http": http})
        probe.event.set()
        return UpdateOutcome(
            status="preserved",
            preserved=("systematic-debugging",),
            messages=(),
        )

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _fake)
    warnings: list[str] = []

    def _hook(message: str) -> None:
        if "systematic-debugging" in message:
            reported.set()

    _run_seam(tmp_path, _build_dependencies(warnings, warning_hook=_hook))

    assert probe.event.wait(timeout=5), "the universal-skill updater never ran"
    assert reported.wait(timeout=5), (
        f"a preserved universal skill must be reported; got {warnings!r}"
    )


def test_run_start_migrates_retired_user_global_skills(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
) -> None:
    """The user-global migration runs on every ordinary run.

    A retired directory whose managed marker still matches its content is
    REMOVED; a user-edited directory and a user-owned directory (no marker)
    both SURVIVE, and the edited one is reported.
    """
    assert not _is_current_baseline_skill(RETIRED_SKILL_NAME), (
        f"{RETIRED_SKILL_NAME!r} must be a RETIRED skill name for this case"
    )
    skills_root = sandbox.home / ".claude" / "skills"
    retired_dir = skills_root / RETIRED_SKILL_NAME
    retired_content = "# retired skill\n"
    _write_managed_skill(
        retired_dir,
        RETIRED_SKILL_NAME,
        retired_content,
        hashlib.sha256(retired_content.encode("utf-8")).hexdigest(),
    )
    edited_dir = skills_root / "edited-skill"
    _write_managed_skill(edited_dir, "edited-skill", "# edited\n", "0" * 64)
    unmanaged_dir = skills_root / "unmanaged-skill"
    unmanaged_dir.mkdir(parents=True)
    (unmanaged_dir / "SKILL.md").write_text("# mine\n", encoding="utf-8")

    warnings: list[str] = []
    _run_seam(tmp_path, _build_dependencies(warnings))

    assert not retired_dir.exists(), (
        "a retired Ralph-managed skill must be pruned from the user-global root"
    )
    assert edited_dir.exists(), "a user-edited managed skill must survive the migration"
    assert unmanaged_dir.exists(), "a user-owned skill directory must survive the migration"
    assert any("edited-skill" in warning for warning in warnings), (
        f"the preserved entry must be reported; got {warnings!r}"
    )


def test_run_start_makes_no_updater_call_when_the_env_kill_switch_is_set(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env kill switch makes ZERO updater calls (and so no request)."""
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert updater_probe.calls == [], (
        f"the updater must not be called when the kill switch is set; got {updater_probe.calls!r}"
    )


def test_run_start_makes_no_updater_call_when_the_config_kill_switch_is_set(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
) -> None:
    """The config kill switch makes ZERO updater calls (and so no request)."""
    (sandbox.config / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert updater_probe.calls == [], (
        f"the updater must not be called when the config disables updates; "
        f"got {updater_probe.calls!r}"
    )


def test_run_start_makes_no_updater_call_when_both_kill_switches_are_set(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both kill switches together still make ZERO updater calls."""
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")
    (sandbox.config / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert updater_probe.calls == [], (
        f"the updater must not be called when both kill switches are set; "
        f"got {updater_probe.calls!r}"
    )


def test_run_start_survives_a_failing_user_global_migration(
    tmp_path: Path,
    updater_probe: _UpdaterProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing migration is non-fatal and never skips the project install.

    The migration resolves the user-global roots through ``Path.home()``,
    so a home that cannot be resolved makes the migration raise.
    """

    def _raising_home() -> Path:
        raise RuntimeError("simulated home resolution failure")

    monkeypatch.setattr("pathlib.Path.home", _raising_home)
    warnings: list[str] = []
    install_calls: list[Path] = []

    _run_seam(
        tmp_path,
        _build_dependencies(warnings, install_calls=install_calls, need_install=True),
    )

    assert install_calls, "the project-scope install must still run after a migration failure"
    assert any("migration" in warning for warning in warnings), (
        f"the migration failure must be reported; got {warnings!r}"
    )
