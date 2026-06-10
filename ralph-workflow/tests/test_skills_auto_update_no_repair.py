"""Black-box tests pinning the no-auto-update contract for SkillManager.check_skills_for_updates.

Per the prompt, on a normal ``ralph`` run the user-global baseline skills
MUST NOT be auto-repaired. ``check_skills_for_updates`` is a SIGNAL ONLY
function: it records the update availability in state and the run prints
a ``ralph --force-init-skills`` hint. The function MUST NOT call
``install_baseline_skills`` (which would mutate ``~/.claude/skills/``).

All tests use only ``tmp_path``, ``monkeypatch``, and ``unittest.mock``.
No real subprocess, no real I/O outside ``tmp_path``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._recheck_policy import RecheckPolicy
from ralph.skills.manager import SkillManager

if TYPE_CHECKING:
    from pathlib import Path


def test_check_skills_for_updates_does_not_call_install_when_healthy(
    tmp_path: Path,
) -> None:
    """Healthy state: install_baseline_skills is NEVER called."""
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.check_skills_update_available", return_value=False),
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
    ):
        result = manager.check_skills_for_updates()

    assert result is False
    mock_install.assert_not_called()
    saved = manager._load_state()
    assert saved.skills.status == CapabilityStatus.INSTALLED_HEALTHY
    assert saved.skills.update_available is False
    assert saved.skills.last_check_ok_iso != ""


def test_check_skills_for_updates_does_not_call_install_when_update_available(
    tmp_path: Path,
) -> None:
    """Update available: install_baseline_skills is NEVER called (the contract is surface-only)."""
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.check_skills_update_available", return_value=True),
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
    ):
        result = manager.check_skills_for_updates()

    assert result is True
    mock_install.assert_not_called()
    saved = manager._load_state()
    assert saved.skills.update_available is True
    assert saved.skills.last_check_ok_iso != ""


def test_check_skills_for_updates_skipped_during_ttl_window(
    tmp_path: Path,
) -> None:
    """Healthy entry with a recent check ISO: recheck is skipped, install never called."""
    state_path = tmp_path / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    recent_iso = datetime.now(tz=UTC).isoformat()
    fresh_state = CapabilityEntry(
        status=CapabilityStatus.INSTALLED_HEALTHY,
        last_check_ok_iso=recent_iso,
        update_available=False,
    )
    state_path.write_text(
        f'{{"skills": {fresh_state.model_dump_json()}}}',
        encoding="utf-8",
    )
    manager = SkillManager(
        state_path=state_path,
        policy=RecheckPolicy(healthy_recheck_hours=24.0),
    )
    with (
        patch(
            "ralph.skills.manager.check_skills_update_available",
            side_effect=AssertionError("recheck should be skipped during TTL window"),
        ),
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
    ):
        result = manager.check_skills_for_updates()

    assert result is False
    mock_install.assert_not_called()


def test_check_skills_for_updates_writes_state_on_no_update_path(tmp_path: Path) -> None:
    """State is written on the no-update path so the runtime can track the check happened."""
    manager = SkillManager(state_path=tmp_path / "state.json")
    pre_state = manager._load_state()
    pre_ok_iso = pre_state.skills.last_check_ok_iso

    with patch("ralph.skills.manager.check_skills_update_available", return_value=False):
        result = manager.check_skills_for_updates()

    assert result is False
    post_state = manager._load_state()
    assert post_state.skills.last_check_ok_iso != pre_ok_iso
    assert post_state.skills.status == CapabilityStatus.INSTALLED_HEALTHY


def test_check_skills_for_updates_does_not_mutate_user_global_root(
    tmp_path: Path,
) -> None:
    """The check must never write to the user-global canonical root under Path.home()."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    sentinel = fake_home / ".claude" / "skills" / "sentinel.md"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("# untouched sentinel\n", encoding="utf-8")

    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("pathlib.Path.home", return_value=fake_home),
        patch("ralph.skills.manager.check_skills_update_available", return_value=True),
    ):
        result = manager.check_skills_for_updates()

    assert result is True
    assert sentinel.exists(), "User-global canonical sentinel file must be untouched"
    assert sentinel.read_text(encoding="utf-8") == "# untouched sentinel\n", (
        "User-global canonical contents must not be overwritten by check_skills_for_updates"
    )


def test_check_skills_for_updates_is_silent_when_install_raises(
    tmp_path: Path,
) -> None:
    """A raising install call would be a regression — verify the check does not call it.

    This is a defense-in-depth test on top of the no-call test: if a future
    refactor re-introduces an install call, the regression suite must catch
    it without depending on a particular exception type.
    """
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.check_skills_update_available", return_value=True),
        patch(
            "ralph.skills.manager.install_baseline_skills",
            side_effect=RuntimeError("install was called"),
        ) as mock_install,
    ):
        manager.check_skills_for_updates()
    mock_install.assert_not_called()
