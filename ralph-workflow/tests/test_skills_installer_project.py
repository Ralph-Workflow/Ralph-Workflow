"""Black-box tests for install_project_baseline_skills and _project_skills_need_install.

All tests use ``tmp_path`` (or a per-test monkeypatched ``Path.home()``) so
the real user-global root is never touched. Symlink verifications use
``Path.resolve()`` on BOTH sides to handle the macOS ``/tmp`` -> ``/private/tmp``
indirection safely (PA-007).

These tests are subprocess_e2e: they exercise the real
``install_project_baseline_skills`` entry point and its full filesystem +
symlink + git path. They cannot be mocked down to the per-test 1 s
budget without losing the end-to-end contract they assert.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]


def _fake_user_global_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_materializes_canonical_root(tmp_path: Path) -> None:
    """Canonical .opencode/skills/<name>/SKILL.md exists for every baseline skill after install."""
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
    """After install, every project-scope sibling is a symlink to the canonical."""
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)

    canonical = tmp_path / ".opencode" / "skills"
    sibling_specs: tuple[tuple[str, Path], ...] = (
        ("claude", tmp_path / ".claude" / "skills"),
        ("codex", tmp_path / ".codex" / "skills"),
        ("agy", tmp_path / ".gemini" / "antigravity-cli" / "skills"),
    )
    for agent, sibling_root in sibling_specs:
        for name in BASELINE_SKILL_NAMES:
            sibling_dir = sibling_root / name
            canonical_target = (canonical / name).resolve()
            assert sibling_dir.is_symlink(), f"{agent}: expected {sibling_dir} to be a symlink"
            # macOS /tmp -> /private/tmp indirection: resolve both sides
            # before equality check (PA-007).
            assert sibling_dir.resolve() == canonical_target, (
                f"{agent}: symlink target {sibling_dir.resolve()} != canonical {canonical_target}"
            )


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_preserves_user_edited_canonical_skill(
    tmp_path: Path,
) -> None:
    """A canonical SKILL.md without a managed marker is preserved (NEEDS_REPAIR)."""
    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"
    user_skill = canonical / "using-superpowers"
    user_skill.mkdir(parents=True, exist_ok=True)
    user_marker = user_skill / "SKILL.md"
    user_marker.write_text("# my custom override\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    assert result[1], "Expected at least one failure for the user-edited canonical"
    assert any(code == "skills-conflict-using-superpowers" for code in result[1]), (
        f"Expected skills-conflict-using-superpowers, got {result[1]}"
    )
    assert user_marker.read_text(encoding="utf-8") == "# my custom override\n", (
        "User-edited canonical SKILL.md must be preserved"
    )


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_preserves_user_edited_sibling_skill(
    tmp_path: Path,
) -> None:
    """A sibling SKILL.md that diverges from canonical must be preserved (NEEDS_REPAIR)."""
    home = _fake_user_global_home(tmp_path)
    sibling = tmp_path / ".claude" / "skills" / "using-superpowers"
    sibling.mkdir(parents=True, exist_ok=True)
    sibling_skill = sibling / "SKILL.md"
    sibling_skill.write_text("# sibling override\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        result = install_project_baseline_skills(tmp_path)

    assert result[1], "Expected at least one failure for the user-edited sibling"
    assert any(code == "sibling-conflict-using-superpowers" for code in result[1]), (
        f"Expected sibling-conflict-using-superpowers, got {result[1]}"
    )
    assert sibling_skill.read_text(encoding="utf-8") == "# sibling override\n", (
        "User-edited sibling SKILL.md must be preserved"
    )


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_is_idempotent(tmp_path: Path) -> None:
    """Calling install twice must return INSTALLED_HEALTHY on the second call with no failures."""
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
    """The project install must not modify the user-global skill root."""
    home = _fake_user_global_home(tmp_path)
    user_claude = home / ".claude" / "skills"
    user_claude.mkdir(parents=True, exist_ok=True)
    sentinel = user_claude / "sentinel.md"
    sentinel.write_text("# untouched\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)

    assert sentinel.exists(), "User-global sentinel file must be untouched"
    assert sentinel.read_text(encoding="utf-8") == "# untouched\n"


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_predicate(tmp_path: Path) -> None:
    """Predicate: True on fresh, False after install, True after a stale non-symlink sibling."""
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        assert _project_skills_need_install(tmp_path) is True
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False

    # Make one sibling a real directory (not a symlink) to force a re-install.
    stale = tmp_path / ".claude" / "skills" / "using-superpowers"
    if stale.is_symlink():
        stale.unlink()
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "SKILL.md").write_text(get_skill_content("using-superpowers"), encoding="utf-8")
    assert _project_skills_need_install(tmp_path) is True


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_detects_stale_managed_canonical_content(
    tmp_path: Path,
) -> None:
    """A managed project skill with stale content must trigger reinstall."""
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False

    skill_file = tmp_path / ".opencode" / "skills" / "using-superpowers" / "SKILL.md"
    skill_file.write_text("# stale managed skill copy\n", encoding="utf-8")

    assert _project_skills_need_install(tmp_path) is True


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_noop_when_canonical_and_all_siblings_have_symlinks(
    tmp_path: Path,
) -> None:
    """Predicate False when canonical and all siblings are present; install is a no-op."""
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False


@pytest.mark.timeout_seconds(5)
def test_project_skills_need_install_when_canonical_is_file(tmp_path: Path) -> None:
    """PA-005 regression: a regular file at the canonical path must trigger an install."""
    canonical_path = tmp_path / ".opencode" / "skills"
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.touch()
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        assert _project_skills_need_install(tmp_path) is True


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_invokes_self_improving_hook(tmp_path: Path) -> None:
    """install_project_baseline_skills calls self_improving_skills_hook once on the success path."""
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
    """The default self_improving_skills_hook body is a no-op and returns None."""
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
    """On a user-owned canonical conflict (NEEDS_REPAIR), the hook must NOT be called.

    The hook is only fired after a successful project fan-out. A conflict
    leaves the workspace untouched; running the hook on a NEEDS_REPAIR path
    would let it observe and write to the canonical prematurely.
    """
    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"
    user_skill = canonical / "using-superpowers"
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
    assert any(code == "skills-conflict-using-superpowers" for code in result[1]), (
        f"Expected skills-conflict-using-superpowers, got {result[1]}"
    )
    hook_mock.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_install_overwrites_stale_canonical_skill(tmp_path: Path) -> None:
    """PA-002 closure: project-scope bundle-update path overwrites stale canonical content.

    The locked conflict-resolution policy for the project-scope branch
    (driven by ``_sync_shipped_skills_on_pipeline_run``) is: bundled content
    always wins for hash-divergent canonical entries, even when the existing
    managed marker matches the on-disk stale sha. The pre-existing
    ``materialize_skills_to_claude_dir`` user-edit preservation contract
    only preserves a skill whose ``stored_sha`` DOES NOT match the on-disk
    sha (i.e. the user manually edited it after install).

    This test pre-stages a stale canonical SKILL.md with a managed marker
    whose ``installed_content_sha256`` matches the on-disk stale sha, so
    ``materialize_skills_to_claude_dir`` will overwrite it anyway (no
    user-edit preservation triggered) AND ``_materialize_canonical_skill``
    runs as a defensive no-op once the on-disk hash already matches the
    bundled sha after the materialize pass. The combined install must
    leave the canonical SKILL.md byte-for-byte identical to the bundled
    content.
    """
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
    post_marker = json.loads(
        (canonical / name / ".ralph-managed.json").read_text(encoding="utf-8")
    )
    bundled_sha = hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()
    assert post_marker.get("installed_content_sha256") == bundled_sha, (
        f"Managed marker sha must equal bundled sha after install; "
        f"got {post_marker.get('installed_content_sha256')!r}"
    )
    assert _materialize_canonical_skill(canonical, name) is False, (
        "Helper must be a no-op when the on-disk hash already matches the bundled sha"
    )


# --- Containment + wrong-target regression coverage (analysis feedback) ---------
#
# Two analyzed bugs require dedicated black-box tests:
#
#   1. CONTAINMENT (external symlink): project-scope canonical or sibling
#      root may resolve outside ``workspace_root`` when the user pre-creates
#      a symlink (e.g. ``./.agents`` -> ``/tmp/evil``). The install must
#      fail closed as NEEDS_REPAIR BEFORE any filesystem mutation and must
#      NOT write any skill content to the external directory.
#
#   2. WRONG-TARGET (sibling symlink pointing at non-canonical): the
#      ``_project_skills_need_install`` predicate must verify each
#      sibling skill symlink resolves to the canonical skill entry for
#      the SAME skill name. A sibling symlink pointing at a stale or
#      external canonical (the analyzed wrong-target regression) must
#      be surfaced as needing install so the repair path replaces it.
#
# Both regressions are pinned by the tests below; if either test passes
# but the analyzed bug repro returns the wrong status, the test suite
# must be expanded.


@pytest.mark.timeout_seconds(5)
def test_resolve_within_workspace_accepts_descendant_path(tmp_path: Path) -> None:
    """A path under workspace_root resolves to the resolved path (containment holds)."""
    inside = tmp_path / ".opencode" / "skills"
    resolved = _resolve_within_workspace(inside, tmp_path)
    assert resolved is not None, "Expected descendant path to be contained"
    assert resolved == inside.resolve(), (
        f"Expected resolved path to equal {inside.resolve()!r}, got {resolved!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_resolve_within_workspace_rejects_external_symlink(tmp_path: Path) -> None:
    """A symlinked canonical pointing outside workspace_root returns None."""
    outside_dir = tmp_path.parent / "outside-sibling-rejection"
    outside_dir.mkdir(parents=True, exist_ok=True)
    try:
        (tmp_path / ".opencode").symlink_to(outside_dir)
        resolved = _resolve_within_workspace(tmp_path / ".opencode" / "skills", tmp_path)
        assert resolved is None, (
            f"Expected external symlink to fail containment, got {resolved!r}"
        )
    finally:
        if (tmp_path / ".opencode").is_symlink():
            (tmp_path / ".opencode").unlink()
        shutil.rmtree(outside_dir, ignore_errors=True)


@pytest.mark.timeout_seconds(5)
def test_install_project_baseline_skills_fails_closed_when_canonical_outside_workspace(
    tmp_path: Path,
) -> None:
    """PA-009 closure: containment gate on the canonical project root.

    When ``workspace_root/.opencode`` is a symlink to a directory OUTSIDE
    the workspace, ``install_project_baseline_skills`` MUST fail closed as
    NEEDS_REPAIR with a single ``skills-outside-workspace-canonical``
    failure code, MUST NOT touch the external directory, and MUST NOT
    return INSTALLED_HEALTHY.
    """
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
        # External directory was not mutated
        assert (outside_dir / "sentinel.txt").read_text(encoding="utf-8") == "DO NOT TOUCH\n", (
            "External directory sentinel file was modified by install"
        )
        # External directory did not receive any skill content
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
    """PA-009 closure: containment gate on a sibling project root.

    When a sibling root (e.g. ``workspace_root/.agents``) is a symlink
    to a directory OUTSIDE the workspace, ``install_project_baseline_skills``
    MUST fail closed as NEEDS_REPAIR with a single
    ``skills-outside-workspace-<segment>`` failure code and MUST NOT
    create the skill tree under the external directory.
    """
    home = _fake_user_global_home(tmp_path)
    outside_dir = tmp_path.parent / "outside-sibling-install-rejection"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "sentinel.txt").write_text("DO NOT TOUCH\n", encoding="utf-8")
    # Poison the .agents sibling root (pi project scope).
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
        # External directory was not mutated
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
    """PA-009 closure: predicate must surface repair-needed when canonical is external."""
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
    """PA-009 closure: predicate must surface repair-needed when sibling is external."""
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
    """PA-009 closure: predicate surfaces repair-needed when sibling symlink target is wrong.

    After a clean install, every sibling skill symlink resolves to the
    matching canonical skill entry (``workspace_root/.opencode/skills/<name>``).
    If a sibling symlink is redirected to point at a different directory
    (e.g. an external ``poison_canonical``), the predicate MUST return
    True so the repair path replaces the wrong target with the correct
    canonical link. Without this check the wrong-target state would be
    silently reported as healthy and the project-scope skill mirror
    would stay misdirected.
    """
    home = _fake_user_global_home(tmp_path)
    poison = tmp_path / "poison_canonical"
    poison.mkdir(parents=True, exist_ok=True)
    # Populate poison with valid-looking skill directories so the wrong-target
    # state is purely about the symlink pointing somewhere unexpected.
    for skill_name in BASELINE_SKILL_NAMES:
        skill_dir = poison / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            get_skill_content(skill_name), encoding="utf-8"
        )

    with patch("pathlib.Path.home", return_value=home):
        # Clean install first.
        install_project_baseline_skills(tmp_path)
        assert _project_skills_need_install(tmp_path) is False, (
            "Sanity: clean install must report False"
        )

        # Redirect every sibling skill symlink at the poison canonical.
        for sibling in project_sibling_skill_roots_top(tmp_path):
            sibling_root = sibling.resolve(tmp_path)
            for skill_name in BASELINE_SKILL_NAMES:
                sibling_dir = sibling_root / skill_name
                if sibling_dir.is_symlink() or sibling_dir.exists():
                    sibling_dir.unlink()
                sibling_dir.symlink_to(
                    poison / skill_name, target_is_directory=True
                )

        # Predicate must detect the wrong target and return True.
        assert _project_skills_need_install(tmp_path) is True, (
            "Predicate must return True when sibling symlink target != canonical"
        )


@pytest.mark.timeout_seconds(5)
def test_project_root_outside_workspace_returns_none_on_clean_tree(tmp_path: Path) -> None:
    """PA-009 closure: containment helper returns None when every root is inside the workspace.

    Sanity check for ``_project_root_outside_workspace``: a fresh workspace
    (no pre-existing symlinks) must return None so neither the install
    nor the predicate treats a clean tree as misdirected.
    """
    home = _fake_user_global_home(tmp_path)
    with patch("pathlib.Path.home", return_value=home):
        assert _project_root_outside_workspace(tmp_path) is None, (
            "Clean workspace must return None from containment helper"
        )


@pytest.mark.timeout_seconds(5)
def test_install_overwrites_user_edited_canonical_skill(tmp_path: Path) -> None:
    """PA-002 closure: project-scope conflict-resolution OVERWRITES user-edited content.

    The locked conflict-resolution policy for the project-scope branch
    (driven by ``_sync_shipped_skills_on_pipeline_run``) is: bundled content
    always wins for hash-divergent canonical entries. ``_materialize_canonical_skill``
    overwrites a user-edited canonical SKILL.md whenever the on-disk hash
    differs from the bundled sha, replacing the content with the bundled
    content and updating the managed marker to the new bundled sha.

    This is the OVERWRITE branch of the conflict-resolution rule -- the
    user-edit preservation contract is honored on the USER-GLOBAL path
    (via ``materialize_skills_to_claude_dir``) but NOT on the PROJECT-SCOPE
    pre-pipeline sync path, where bundled content always wins.
    """
    home = _fake_user_global_home(tmp_path)
    canonical = tmp_path / ".opencode" / "skills"
    name = BASELINE_SKILL_NAMES[0]
    canonical.mkdir(parents=True, exist_ok=True)
    skill_dir = canonical / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    user_edited_content = (
        f"# user-edited version of {name}\n"  # NOT the bundled content
    )
    skill_dir.joinpath("SKILL.md").write_text(user_edited_content, encoding="utf-8")
    user_edited_sha = hashlib.sha256(user_edited_content.encode("utf-8")).hexdigest()
    skill_dir.joinpath(".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "installed_content_sha256": user_edited_sha,  # matches on-disk user edit
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
    post_marker = json.loads(
        (canonical / name / ".ralph-managed.json").read_text(encoding="utf-8")
    )
    bundled_sha = hashlib.sha256(get_skill_content(name).encode("utf-8")).hexdigest()
    assert post_marker.get("installed_content_sha256") == bundled_sha
