from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph.skills._agent_paths import project_sibling_skill_roots as project_sibling_skill_roots_top
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content
from ralph.skills._installer import (
    _materialize_canonical_skill,
    _project_root_outside_workspace,
    _project_skills_need_install,
    _resolve_within_workspace,
    install_project_baseline_skills,
    self_improving_skills_hook,
)

pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]


def _fake_user_global_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _project_skill_roots(workspace: Path) -> list[Path]:
    roots = [workspace / ".opencode" / "skills"]
    roots.extend(
        sibling.resolve(workspace) for sibling in project_sibling_skill_roots_top(workspace)
    )
    return roots


@pytest.mark.timeout_seconds(3)
def test_install_project_baseline_skills_materializes_canonical_root(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)
    assert not result[1], f"Expected no failures, got {result[1]}"
    canonical = tmp_path / ".opencode" / "skills"
    for name in BASELINE_SKILL_NAMES:
        skill_md = canonical / name / "SKILL.md"
        assert skill_md.exists(), f"Missing canonical SKILL.md: {skill_md}"
        marker = canonical / name / ".ralph-managed.json"
        assert marker.exists(), f"Missing managed marker: {marker}"


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_creates_sibling_symlinks(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)

    canonical = tmp_path / ".opencode" / "skills"
    sibling_specs: tuple[tuple[str, Path], ...] = (
        ("claude", tmp_path / ".claude" / "skills"),
        ("codex", tmp_path / ".codex" / "skills"),
        ("agy", tmp_path / ".gemini" / "antigravity-cli" / "skills"),
        ("pi", tmp_path / ".agents" / "skills"),
    )
    for agent, sibling_root in sibling_specs:
        for name in BASELINE_SKILL_NAMES:
            sibling_dir = sibling_root / name
            canonical_target = (canonical / name).resolve()
            assert sibling_dir.is_symlink(), f"{agent}: expected {sibling_dir} to be a symlink"
            assert sibling_dir.resolve() == canonical_target, (
                f"{agent}: symlink target {sibling_dir.resolve()} != canonical {canonical_target}"
            )
            link_target = str(sibling_dir.readlink())
            assert not Path(link_target).is_absolute(), (
                f"{agent}: sibling symlink must use a RELATIVE path, got absolute: {link_target}"
            )
            assert link_target.endswith(f".opencode/skills/{name}"), (
                f"{agent}: relative symlink must target canonical "
                f".opencode/skills/{name}, got: {link_target}"
            )


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_preserves_user_edited_canonical_skill(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"
    user_skill = canonical / "submit-artifact"
    user_skill.mkdir(parents=True, exist_ok=True)
    user_marker = user_skill / "SKILL.md"
    user_marker.write_text("# my custom override\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    assert result[1], "Expected at least one failure for the user-edited canonical"
    assert any(code == "skills-conflict-submit-artifact" for code in result[1]), (
        f"Expected skills-conflict-submit-artifact, got {result[1]}"
    )
    assert user_marker.read_text(encoding="utf-8") == "# my custom override\n", (
        "User-edited canonical SKILL.md must be preserved"
    )


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_preserves_user_edited_sibling_skill(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    sibling = tmp_path / ".claude" / "skills" / "submit-artifact"
    sibling.mkdir(parents=True, exist_ok=True)
    sibling_skill = sibling / "SKILL.md"
    sibling_skill.write_text("# sibling override\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    assert result[1], "Expected at least one failure for the user-edited sibling"
    assert any(code == "sibling-conflict-submit-artifact" for code in result[1]), (
        f"Expected sibling-conflict-submit-artifact, got {result[1]}"
    )
    assert sibling_skill.read_text(encoding="utf-8") == "# sibling override\n", (
        "User-edited sibling SKILL.md must be preserved"
    )


@pytest.mark.timeout_seconds(3)
def test_install_project_baseline_skills_is_idempotent(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        first = install_project_baseline_skills(tmp_path)
        second = install_project_baseline_skills(tmp_path)
    assert not first[1], f"First install must succeed, got {first[1]}"
    assert not second[1], f"Second install must succeed, got {second[1]}"


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_does_not_touch_user_global_root(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    user_claude = home / ".claude" / "skills"
    user_claude.mkdir(parents=True, exist_ok=True)
    sentinel = user_claude / "sentinel.md"
    sentinel.write_text("# untouched\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)

    assert sentinel.exists(), "User-global sentinel file must be untouched"
    assert sentinel.read_text(encoding="utf-8") == "# untouched\n"


@pytest.mark.timeout_seconds(3)
def test_project_skills_need_install_predicate(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        assert _project_skills_need_install(tmp_path) is True
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False

    stale = tmp_path / ".claude" / "skills" / "submit-artifact"
    if stale.is_symlink():
        stale.unlink()
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "SKILL.md").write_text(get_skill_content("submit-artifact"), encoding="utf-8")
    assert _project_skills_need_install(tmp_path) is True


@pytest.mark.timeout_seconds(3)
def test_project_skills_need_install_detects_stale_managed_canonical_content(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False

    skill_file = tmp_path / ".opencode" / "skills" / "submit-artifact" / "SKILL.md"
    skill_file.write_text("# stale managed skill copy\n", encoding="utf-8")

    assert _project_skills_need_install(tmp_path) is True


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_noop_when_canonical_and_all_siblings_have_symlinks(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_when_canonical_is_file(tmp_path: Path) -> None:
    canonical_path = tmp_path / ".opencode" / "skills"
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.touch()
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        assert _project_skills_need_install(tmp_path) is True


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_invokes_self_improving_hook(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    with (
        patch("pathlib.Path.home", return_value=home),
        patch("ralph.skills._installer.self_improving_skills_hook") as hook_mock,
    ):
        result = install_project_baseline_skills(tmp_path)
    hook_mock.assert_called_once_with(
        workspace_root=tmp_path,
        canonical_root=tmp_path / ".opencode" / "skills",
    )
    assert not result[1], f"Expected no failures, got {result[1]}"
    assert result[0].status.value == "installed_healthy", (
        f"Expected INSTALLED_HEALTHY status, got {result[0]!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_self_improving_skills_hook_is_no_op_by_default(tmp_path: Path) -> None:
    result = self_improving_skills_hook(
        workspace_root=tmp_path,
        canonical_root=tmp_path,
    )
    assert result is None
    assert list(tmp_path.iterdir()) == [], (
        f"No-op hook must NOT write anything; got: {list(tmp_path.iterdir())!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_does_not_call_self_improving_hook_on_conflict(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"
    user_skill = canonical / "submit-artifact"
    user_skill.mkdir(parents=True, exist_ok=True)
    (user_skill / "SKILL.md").write_text(
        "# my custom override\n",
        encoding="utf-8",
    )

    with (
        patch("pathlib.Path.home", return_value=home),
        patch("ralph.skills._installer.self_improving_skills_hook") as hook_mock,
    ):
        result = install_project_baseline_skills(tmp_path)

    assert result[0].status == CapabilityStatus.NEEDS_REPAIR
    assert any(code == "skills-conflict-submit-artifact" for code in result[1]), (
        f"Expected skills-conflict-submit-artifact, got {result[1]}"
    )
    hook_mock.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_install_overwrites_stale_canonical_skill(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"
    name = BASELINE_SKILL_NAMES[0]
    canonical.mkdir(parents=True, exist_ok=True)
    skill_dir = canonical / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text("# stale version\n", encoding="utf-8")
    stale_sha = hashlib.sha256(b"# stale version\n").hexdigest()
    skill_dir.joinpath(".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "installed_content_sha256": stale_sha,
                "skill_name": name,
            }
        ),
        encoding="utf-8",
    )

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    assert result[1] == [], f"Expected empty failures, got {result[1]!r}"
    post_md = (canonical / name / "SKILL.md").read_text(encoding="utf-8")
    assert post_md == get_skill_content(name), (
        "Stale canonical SKILL.md was NOT overwritten with bundled content"
    )
    post_marker = json.loads((canonical / name / ".ralph-managed.json").read_text(encoding="utf-8"))
    bundled_sha = hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()
    assert post_marker.get("installed_content_sha256") == bundled_sha, (
        f"Managed marker sha must equal bundled sha after install; "
        f"got {post_marker.get('installed_content_sha256')!r}"
    )
    assert _materialize_canonical_skill(canonical, name) is False, (
        "Helper must be a no-op when the on-disk hash already matches the bundled sha"
    )


@pytest.mark.timeout_seconds(5)
def test_resolve_within_workspace_accepts_descendant_path(tmp_path: Path) -> None:
    inside = tmp_path / ".opencode" / "skills"
    resolved = _resolve_within_workspace(inside, tmp_path)
    assert resolved is not None, "Expected descendant path to be contained"
    assert resolved == inside.resolve(), (
        f"Expected resolved path to equal {inside.resolve()!r}, got {resolved!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_resolve_within_workspace_rejects_external_symlink(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / "outside-sibling-rejection"
    outside_dir.mkdir(parents=True, exist_ok=True)
    try:
        (tmp_path / ".opencode").symlink_to(outside_dir)
        resolved = _resolve_within_workspace(tmp_path / ".opencode" / "skills", tmp_path)
        assert resolved is None, f"Expected external symlink to fail containment, got {resolved!r}"
    finally:
        if (tmp_path / ".opencode").is_symlink():
            (tmp_path / ".opencode").unlink()
        shutil.rmtree(outside_dir, ignore_errors=True)


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_fails_closed_when_canonical_outside_workspace(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    outside_dir = tmp_path.parent / "outside-canonical-rejection"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "sentinel.txt").write_text("DO NOT TOUCH\n", encoding="utf-8")
    (tmp_path / ".opencode").symlink_to(outside_dir)
    try:
        with patch("pathlib.Path.home", return_value=home):
            result = install_project_baseline_skills(tmp_path)

        assert result[0].status == CapabilityStatus.NEEDS_REPAIR, (
            f"Expected NEEDS_REPAIR, got {result[0].status!r}"
        )
        assert result[1] == ["skills-outside-workspace-canonical"], (
            f"Expected ['skills-outside-workspace-canonical'], got {result[1]!r}"
        )
        assert (outside_dir / "sentinel.txt").read_text(encoding="utf-8") == "DO NOT TOUCH\n", (
            "External directory sentinel file was modified by install"
        )
        outside_listing = sorted(p.name for p in outside_dir.iterdir())
        assert "skills" not in outside_listing, (
            f"External directory gained unexpected entry; listing={outside_listing!r}"
        )
        assert outside_listing == ["sentinel.txt"], (
            f"External directory listing should be unchanged; got {outside_listing!r}"
        )
    finally:
        if (tmp_path / ".opencode").is_symlink():
            (tmp_path / ".opencode").unlink()
        shutil.rmtree(outside_dir, ignore_errors=True)


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_fails_closed_when_sibling_outside_workspace(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    outside_dir = tmp_path.parent / "outside-sibling-install-rejection"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "sentinel.txt").write_text("DO NOT TOUCH\n", encoding="utf-8")
    (tmp_path / ".agents").symlink_to(outside_dir)
    try:
        with patch("pathlib.Path.home", return_value=home):
            result = install_project_baseline_skills(tmp_path)

        assert result[0].status == CapabilityStatus.NEEDS_REPAIR, (
            f"Expected NEEDS_REPAIR, got {result[0].status!r}"
        )
        assert result[1] == ["skills-outside-workspace-.agents/skills"], (
            f"Expected ['skills-outside-workspace-.agents/skills'], got {result[1]!r}"
        )
        outside_listing = sorted(p.name for p in outside_dir.iterdir())
        assert outside_listing == ["sentinel.txt"], (
            f"External directory listing should be unchanged; got {outside_listing!r}"
        )
    finally:
        if (tmp_path / ".agents").is_symlink():
            (tmp_path / ".agents").unlink()
        shutil.rmtree(outside_dir, ignore_errors=True)


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_true_when_canonical_outside_workspace(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    outside_dir = tmp_path.parent / "outside-canonical-predicate"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".opencode").symlink_to(outside_dir)
    try:
        with patch("pathlib.Path.home", return_value=home):
            assert _project_skills_need_install(tmp_path) is True, (
                "Predicate must return True when canonical resolves outside workspace"
            )
            assert _project_root_outside_workspace(tmp_path) == (
                "skills-outside-workspace-canonical"
            )
    finally:
        if (tmp_path / ".opencode").is_symlink():
            (tmp_path / ".opencode").unlink()
        shutil.rmtree(outside_dir, ignore_errors=True)


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_true_when_sibling_outside_workspace(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    outside_dir = tmp_path.parent / "outside-sibling-predicate"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agents").symlink_to(outside_dir)
    try:
        with patch("pathlib.Path.home", return_value=home):
            assert _project_skills_need_install(tmp_path) is True, (
                "Predicate must return True when sibling resolves outside workspace"
            )
            failure = _project_root_outside_workspace(tmp_path)
            assert failure is not None and failure.startswith("skills-outside-workspace-")
    finally:
        if (tmp_path / ".agents").is_symlink():
            (tmp_path / ".agents").unlink()
        shutil.rmtree(outside_dir, ignore_errors=True)


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_true_when_sibling_points_to_wrong_target(
    tmp_path: Path,
) -> None:
    home = _fake_user_global_home(tmp_path)
    poison = tmp_path / "poison_canonical"
    poison.mkdir(parents=True, exist_ok=True)
    for skill_name in BASELINE_SKILL_NAMES:
        skill_dir = poison / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(get_skill_content(skill_name), encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False, (
            "Sanity: clean install must report False"
        )

        for sibling in project_sibling_skill_roots_top(tmp_path):
            sibling_root = sibling.resolve(tmp_path)
            for skill_name in BASELINE_SKILL_NAMES:
                sibling_dir = sibling_root / skill_name
                if sibling_dir.is_symlink() or sibling_dir.exists():
                    sibling_dir.unlink()
                sibling_dir.symlink_to(poison / skill_name, target_is_directory=True)

        assert _project_skills_need_install(tmp_path) is True, (
            "Predicate must return True when sibling symlink target != canonical"
        )


@pytest.mark.timeout_seconds(5)
def test_project_root_outside_workspace_returns_none_on_clean_tree(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        assert _project_root_outside_workspace(tmp_path) is None, (
            "Clean workspace must return None from containment helper"
        )


@pytest.mark.timeout_seconds(5)
def test_install_overwrites_user_edited_canonical_skill(tmp_path: Path) -> None:
    """PA-002 closure: project-scope reconcile OVERWRITES a PRISTINE managed copy.

    OWNERSHIP CONTRACT (wt-05 WU-1): the project-scope reconcile refreshes
    a canonical entry Ralph can PROVE it manages -- the marker records the
    digest of the on-disk content, so the entry is a stale-but-pristine
    Ralph-managed copy and the bundled content replaces it. The entry is
    therefore refreshed to the bundled content and the managed marker is
    updated to the new bundled sha.

    A canonical entry whose recorded digest does NOT match the on-disk
    content (a real user edit) is USER-EDITED: it is preserved and never
    overwritten (see ``tests/test_skills_prune_ownership.py``).
    """
    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"
    name = BASELINE_SKILL_NAMES[0]
    canonical.mkdir(parents=True, exist_ok=True)
    skill_dir = canonical / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    user_edited_content = f"# user-edited version of {name}\n"
    skill_dir.joinpath("SKILL.md").write_text(user_edited_content, encoding="utf-8")
    user_edited_sha = hashlib.sha256(user_edited_content.encode("utf-8")).hexdigest()
    skill_dir.joinpath(".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "installed_content_sha256": user_edited_sha,
                "skill_name": name,
            }
        ),
        encoding="utf-8",
    )

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    assert result[1] == [], f"Expected empty failures, got {result[1]!r}"
    post_md = (canonical / name / "SKILL.md").read_text(encoding="utf-8")
    assert post_md == get_skill_content(name), (
        "Project-scope reconcile must overwrite user-edited SKILL.md "
        "with bundled content (per locked conflict-resolution policy)"
    )
    post_marker = json.loads((canonical / name / ".ralph-managed.json").read_text(encoding="utf-8"))
    bundled_sha = hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()
    assert post_marker.get("installed_content_sha256") == bundled_sha


@pytest.mark.timeout_seconds(5)
def test_project_sibling_symlinks_survive_workspace_relocation(tmp_path: Path) -> None:
    original_ws = tmp_path / "original_ws"
    original_ws.mkdir(parents=True, exist_ok=True)
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(original_ws)

    for sibling in project_sibling_skill_roots_top(original_ws):
        sibling_root = sibling.resolve(original_ws)
        for name in BASELINE_SKILL_NAMES:
            sibling_dir = sibling_root / name
            assert sibling_dir.is_symlink(), (
                f"{sibling.agent}: expected {sibling_dir} to be a symlink after install"
            )
            link_target = str(sibling_dir.readlink())
            assert not Path(link_target).is_absolute(), (
                f"{sibling.agent}: sibling symlink must be RELATIVE; got absolute: {link_target}"
            )

    relocated_ws = tmp_path / "relocated_ws"
    original_ws.rename(relocated_ws)

    canonical_relocated = relocated_ws / ".opencode" / "skills"
    for sibling in project_sibling_skill_roots_top(relocated_ws):
        sibling_root = sibling.resolve(relocated_ws)
        for name in BASELINE_SKILL_NAMES:
            sibling_dir = sibling_root / name
            assert sibling_dir.is_symlink(), (
                f"{sibling.agent}: symlink missing after rename: {sibling_dir}"
            )
            resolved = sibling_dir.resolve()
            expected = (canonical_relocated / name).resolve()
            assert resolved == expected, (
                f"{sibling.agent}: relocated symlink {resolved} != canonical {expected}"
            )

    assert _project_skills_need_install(relocated_ws) is False, (
        "Relative symlinks MUST keep _project_skills_need_install=False after "
        "workspace relocation (no worktree-switch churn)"
    )


@pytest.mark.timeout_seconds(3)
def test_install_prunes_managed_skill_removed_from_baseline(tmp_path: Path) -> None:
    from ralph.skills._content import _MANAGED_MARKER
    from ralph.skills._installer import _prune_removed_baseline_skills

    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)
    for name in BASELINE_SKILL_NAMES:
        assert (canonical / name).is_dir(), f"baseline skill {name!r} missing after install"

    stale_name = "submit-plan-step-edits"
    assert stale_name not in BASELINE_SKILL_NAMES, (
        "this test assumes the retired skill is no longer in the baseline"
    )
    stale_dir = canonical / stale_name
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_content = "stale"
    (stale_dir / "SKILL.md").write_text(stale_content, encoding="utf-8")
    (stale_dir / _MANAGED_MARKER).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": stale_name,
                "installed_content_sha256": hashlib.sha256(
                    stale_content.encode("utf-8")
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    assert stale_dir.is_dir()

    user_owned_target = "user-owned-skill"
    for sibling in project_sibling_skill_roots_top(tmp_path):
        sibling_root = sibling.resolve(tmp_path)
        user_owned = sibling_root / user_owned_target
        user_owned.mkdir(parents=True, exist_ok=True)
        (user_owned / "SKILL.md").write_text("user content", encoding="utf-8")

    pruned = _prune_removed_baseline_skills(canonical)
    assert stale_name in pruned.pruned, (
        f"expected the stale managed skill to be pruned; got {pruned!r}"
    )
    assert not stale_dir.exists(), "stale managed skill must be removed from the canonical"

    for sibling in project_sibling_skill_roots_top(tmp_path):
        sibling_root = sibling.resolve(tmp_path)
        user_owned = sibling_root / user_owned_target
        assert user_owned.is_dir(), (
            f"{sibling.agent}: user-owned skill without marker must survive the prune"
        )
        assert (user_owned / "SKILL.md").read_text(encoding="utf-8") == "user content"

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)
    for sibling in project_sibling_skill_roots_top(tmp_path):
        sibling_root = sibling.resolve(tmp_path)
        stale_symlink = sibling_root / stale_name
        assert not stale_symlink.exists(), (
            f"{sibling.agent}: stale symlink to retired skill must be removed; saw {stale_symlink}"
        )
        assert (sibling_root / user_owned_target).is_dir(), (
            f"{sibling.agent}: user-owned skill must survive the full install"
        )


@pytest.mark.timeout_seconds(3)
def test_install_preserves_user_edited_retired_managed_skill(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    retired_name = "submit-plan-step-edits"
    assert retired_name not in BASELINE_SKILL_NAMES

    retired_dirs: list[Path] = []
    for root in _project_skill_roots(tmp_path):
        retired_dir = root / retired_name
        retired_dir.mkdir(parents=True)
        (retired_dir / "SKILL.md").write_text("stale\n", encoding="utf-8")
        (retired_dir / ".ralph-managed.json").write_text(
            json.dumps(
                {
                    "managed_by": "ralph-workflow",
                    "skill": retired_name,
                    "installed_content_sha256": hashlib.sha256(b"stale\n").hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        retired_dirs.append(retired_dir)
    for retired_dir in retired_dirs:
        (retired_dir / "SKILL.md").write_text("# my edited retired skill\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    for retired_dir in retired_dirs:
        assert retired_dir.is_dir(), "a user-edited retired skill must never be deleted"
        assert (retired_dir / "SKILL.md").read_text(encoding="utf-8") == (
            "# my edited retired skill\n"
        )
    assert f"skills-prune-preserved-{retired_name}" in result[1], (
        f"the preserved retired skill must be reported; got {result[1]!r}"
    )


@pytest.mark.timeout_seconds(3)
def test_install_preserves_retired_managed_skill_with_companion_file(tmp_path: Path) -> None:
    home = _fake_user_global_home(tmp_path)
    retired_name = "submit-plan-step-edits"
    assert retired_name not in BASELINE_SKILL_NAMES

    retired_dirs: list[Path] = []
    for root in _project_skill_roots(tmp_path):
        retired_dir = root / retired_name
        retired_dir.mkdir(parents=True)
        (retired_dir / "SKILL.md").write_text("stale\n", encoding="utf-8")
        (retired_dir / ".ralph-managed.json").write_text(
            json.dumps(
                {
                    "managed_by": "ralph-workflow",
                    "skill": retired_name,
                    "installed_content_sha256": hashlib.sha256(b"stale\n").hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        (retired_dir / "notes.md").write_text("# my notes\n", encoding="utf-8")
        retired_dirs.append(retired_dir)

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    for retired_dir in retired_dirs:
        assert retired_dir.is_dir(), "a managed directory with a companion file must survive"
        assert (retired_dir / "notes.md").read_text(encoding="utf-8") == "# my notes\n"
    assert f"skills-prune-preserved-{retired_name}" in result[1], (
        f"the preserved retired skill must be reported; got {result[1]!r}"
    )


@pytest.mark.timeout_seconds(3)
def test_prune_gates_broken_symlinks_on_ownership_evidence(tmp_path: Path) -> None:
    """A broken symlink is unlinked ONLY when the prune can attribute it.

    OWNERSHIP CONTRACT (wt-05 WU-1): the prune unlinks a broken symlink
    ONLY when it can attribute the missing target to a Ralph-managed
    canonical entry the SAME prune pass just removed. A link whose target
    Ralph knows nothing about is PRESERVED and REPORTED through the
    ``skills-prune-unknown-*`` surface; a LIVE link to a surviving retired
    entry is left intact. The earlier unconditional unlink destroyed links
    Ralph could not prove were its own.
    """
    from ralph.skills._content import BASELINE_SKILL_NAMES
    from ralph.skills._installer import _prune_removed_baseline_skills

    root = tmp_path / "skills"
    root.mkdir()
    retired_name = "submit-plan-step-edits"
    assert retired_name not in BASELINE_SKILL_NAMES

    # A broken link whose target Ralph knows nothing about.
    unknown_link = root / "unknown-broken-link"
    unknown_link.symlink_to(root / "does-not-exist", target_is_directory=True)

    # A managed retired entry plus the stale link that pointed at it.
    retired_dir = root / retired_name
    retired_dir.mkdir()
    (retired_dir / "SKILL.md").write_text("stale\n", encoding="utf-8")
    (retired_dir / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": retired_name,
                "installed_content_sha256": hashlib.sha256(b"stale\n").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    stale_link = root / "stale-sibling-link"
    stale_link.symlink_to(retired_dir, target_is_directory=True)

    # A USER-EDITED retired entry survives the prune, so a LIVE link to it
    # stays live and must be left intact.
    edited_dir = root / "edited-retired-entry"
    edited_dir.mkdir()
    (edited_dir / "SKILL.md").write_text("# user edited\n", encoding="utf-8")
    (edited_dir / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": "edited-retired-entry",
                "installed_content_sha256": hashlib.sha256(b"stale\n").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    live_link = root / "live-sibling-link"
    live_link.symlink_to(edited_dir, target_is_directory=True)

    pruned = _prune_removed_baseline_skills(root)

    assert not retired_dir.exists(), "the retired managed entry must be removed"
    assert not stale_link.is_symlink(), (
        f"a broken symlink to a just-pruned managed entry must be unlinked; got {pruned!r}"
    )
    assert "stale-sibling-link" in pruned.pruned, (
        f"the unlinked stale symlink must be reported as pruned; got {pruned!r}"
    )
    assert unknown_link.is_symlink(), "a broken symlink Ralph cannot attribute must survive"
    assert "unknown-broken-link" in pruned.unknown, (
        f"the unattributable broken symlink must be reported; got {pruned!r}"
    )
    assert live_link.is_symlink(), "a LIVE symlink to a surviving retired entry must be left intact"
    assert edited_dir.is_dir(), "the user-edited retired entry must survive the prune"
