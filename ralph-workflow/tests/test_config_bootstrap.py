"""Black-box tests for ralph.config.bootstrap and related first-run behavior."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from git import Repo

import ralph.config.loader as loader_module
import ralph.policy
from ralph.config.bootstrap import (
    _DEFAULT_GIT_EXCLUDE_PATTERNS,
    _DEFAULT_GITIGNORE_PATTERNS,
    auto_seed_default_git_exclude,
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_global_policy_configs,
    ensure_local_configs,
    regenerate_all,
    resolve_global_config_dir,
)
from ralph.policy.loader import (
    PolicyValidationError as LoaderPolicyValidationError,
)
from ralph.policy.loader import (
    load_policy,
)
from ralph.workspace.scope import WorkspaceScope

_EXPECTED_LOCAL_CONFIG_COUNT = 4
_EXPECTED_REGENERATE_COUNT = 9
_EXPECTED_DEFAULT_GITIGNORE_LINES = (
    # Ralph-local
    ".agent/",
    "/PROMPT*",
    "wt-*/",
    "/checkpoint.json",
    # Python
    "__pycache__/",
    "*.py[codz]",
    "*$py.class",
    ".venv/",
    "venv/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "htmlcov/",
    "dist/",
    "build/*",
    "!build/.gitkeep",
    "*.egg-info/",
    # Python extras
    ".tox/",
    ".nox/",
    ".pdm-build/",
    "*.pyo",
    ".ipynb_checkpoints/",
    "pip-wheel-metadata/",
    # Node
    "node_modules/",
    ".next/",
    ".nuxt/",
    # Node extras
    "coverage/",
    "*.tsbuildinfo",
    ".parcel-cache/",
    ".cache/",
    ".env",
    ".env.local",
    ".env.*.local",
    # Rust (Cargo.lock intentionally NOT ignored)
    "target/*",
    "!target/.gitkeep",
    "**/*.rs.bk",
    # Go (vendor/ is opt-in, go.sum intentionally NOT ignored)
    "vendor/*",
    # Ruby
    "vendor/bundle/*",
    ".bundle/",
    "log/",
    "tmp/",
    "*.gem",
    # PHP
    ".phpunit.cache/",
    "/storage/*.key",
    "composer.phar",
    # Java/Kotlin extras
    ".gradle/*",
    "!.gradle/.gitkeep",
    "*.class",
    "*.jar",
    # .NET
    "bin/*",
    "!bin/.gitkeep",
    "obj/*",
    "!obj/.gitkeep",
    "*.user",
    "*.suo",
    "*.userosscache",
    "*.sln.docstates",
    "[Dd]ebug/",
    "[Rr]elease/",
    "x64/",
    "x86/",
    "*.dll",
    "*.exe",
    "*.pdb",
    # Dart / Flutter
    ".dart_tool/",
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
    ".packages",
    ".pub-cache/",
    ".pub/",
    # Elixir
    "_build/*",
    "!_build/.gitkeep",
    "deps/*",
    "!deps/.gitkeep",
    ".elixir_ls/",
    "cover/",
    "doc/",
    "fetch.*.exs",
    # Scala / Metals / BSP
    ".bsp/",
    ".metals/",
    "project/target/",
    "project/project/",
    # Terraform
    ".terraform/",
    "*.tfstate",
    "*.tfstate.*",
    ".terraform.lock.hcl",
    "terraform.tfvars",
    "crash.log",
    "crash.*.log",
    "*.tfvars",
    # Editors / IDEs extra
    ".fleet/",
    ".cursor/",
    ".windsurf/",
    ".idea_modules/",
    "*.iml",
    # Editors (NOTE: .vscode/ intentionally NOT included — upstream repo
    # has tracked files under .vscode/.)
    ".idea/",
    "*.swp",
    "*.swo",
    # OS metadata
    ".DS_Store",
    "Thumbs.db",
    # OS metadata extras
    "Desktop.ini",
    "ehthumbs.db",
    "$RECYCLE.BIN/",
)
_EXPECTED_IGNORED_LOCAL_PATHS = (
    ".agent/mcp.toml",
    ".agent/checkpoint.json",
    ".agent/artifacts/plan.json",
    ".agent/tmp/last_retry_error_development.txt",
    ".agent/raw/unit-a.log",
    ".agent/workers/unit-a/artifacts/development_result.json",
    ".agent/CURRENT_PROMPT.md",
    "PROMPT.md",
    "wt-123/worktree-file.txt",
)
# Per-category positive matches (one representative path per new gitignore category).
# These MUST be ignored by the expanded _DEFAULT_GITIGNORE_PATTERNS after
# ensure_local_configs(agent_dir) runs in a git repo.
_EXPECTED_NEW_CATEGORY_POSITIVE_IGNORED_PATHS = (
    # Rust
    "target/main.o",
    # Go (vendor/)
    "vendor/gopkg.in/yaml.v2/",
    # Java/Kotlin extras
    "build/output.jar",
    # .NET
    "bin/Release/MyApp.dll",
    # Dart / Flutter
    ".dart_tool/package_config.json",
    # Elixir
    "_build/dev/lib/myapp/ebin/myapp.app",
    # Scala / Metals / BSP
    ".bsp/sbt.json",
    # Terraform
    ".terraform/terraform.tfstate",
    # Node extras
    "coverage/lcov.info",
    # Ruby
    "tmp/cache/redis.rdb",
    # PHP
    "vendor/autoload.php",
    # Python extras
    ".tox/pytest",
    # Editor extras
    ".idea/workspace.xml",
    # OS extras
    "Desktop.ini",
)
# Per-category tracked-file / convention non-matches. These MUST NOT be ignored
# after ensure_local_configs(agent_dir) runs — they are common tracked files
# (or source-controlled markers) that the new patterns must preserve.
#
# NOTE: `vendor/bundle/.gitkeep` is intentionally omitted from this list.
# The Go `vendor/*` and Ruby `vendor/bundle/*` patterns are mutually
# exclusive in gitignore semantics: a single `vendor/*` line matches
# `vendor/bundle/.gitkeep` and there is no way to allowlist a marker file
# inside an ignored dir. The Go convention (ignore everything in `vendor/`)
# is the dominant case, so the default gitignore honors that. A user who
# tracks `vendor/bundle/.gitkeep` can add a one-line `!vendor/bundle/.gitkeep`
# to their project-local gitignore.
_EXPECTED_TRACKED_NON_IGNORED_PATHS = (
    ".vscode/launch.json",
    "Cargo.lock",
    "go.sum",
    "target/.gitkeep",
    "build/.gitkeep",
    "bin/.gitkeep",
    "obj/.gitkeep",
    "_build/.gitkeep",
    "deps/.gitkeep",
    ".gradle/.gitkeep",
)


def test_ensure_global_config_creates_when_absent(tmp_path: Path) -> None:
    result = ensure_global_config(tmp_path)
    target = tmp_path / "ralph-workflow.toml"

    assert target.exists()
    assert result.action == "created"
    assert result.backup is None
    assert isinstance(tomllib.loads(target.read_text()), dict)


def test_ensure_global_config_idempotent(tmp_path: Path) -> None:
    ensure_global_config(tmp_path)
    target = tmp_path / "ralph-workflow.toml"
    mtime_after_first = target.stat().st_mtime

    result2 = ensure_global_config(tmp_path)
    assert result2.action == "skipped"
    assert target.stat().st_mtime == mtime_after_first


def test_ensure_global_config_force_creates_backup(tmp_path: Path) -> None:
    target = tmp_path / "ralph-workflow.toml"
    target.write_text("# MINE", encoding="utf-8")

    result = ensure_global_config(tmp_path, force=True)

    backup = target.with_suffix(".toml.bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "# MINE"
    assert target.read_text(encoding="utf-8").startswith("#")
    assert result.action == "regenerated"
    assert result.backup == backup


def test_ensure_global_mcp_config_creates(tmp_path: Path) -> None:
    result = ensure_global_mcp_config(tmp_path)
    target = tmp_path / "ralph-workflow-mcp.toml"

    assert target.exists()
    assert result.action == "created"
    assert result.backup is None
    assert isinstance(tomllib.loads(target.read_text()), dict)


def test_ensure_global_policy_configs_create_branded_global_policy_files(tmp_path: Path) -> None:
    results = ensure_global_policy_configs(tmp_path)

    expected_files = (
        "ralph-workflow-pipeline.toml",
        "ralph-workflow-artifacts.toml",
    )
    for fname in expected_files:
        target = tmp_path / fname
        assert target.exists(), f"{fname} should exist"
        assert isinstance(tomllib.loads(target.read_text()), dict)

    assert [result.path.name for result in results] == list(expected_files)


def test_ensure_global_policy_configs_ignore_legacy_global_policy_files(
    tmp_path: Path,
) -> None:
    defaults_dir = Path(ralph.policy.__file__).parent / "defaults"
    legacy_pipeline = tmp_path / "pipeline.toml"
    legacy_artifacts = tmp_path / "artifacts.toml"
    legacy_pipeline.write_text(
        '[phases.planning]\ndrain = "planning"\nrole = "execution"\n',
        encoding="utf-8",
    )
    legacy_artifacts.write_text("# legacy artifacts\n", encoding="utf-8")

    results = ensure_global_policy_configs(tmp_path)

    assert (tmp_path / "ralph-workflow-pipeline.toml").read_text(encoding="utf-8") == (
        defaults_dir / "pipeline.toml"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "ralph-workflow-artifacts.toml").read_text(encoding="utf-8") == (
        defaults_dir / "artifacts.toml"
    ).read_text(encoding="utf-8")
    assert all(result.action == "created" for result in results)


def test_ensure_local_configs_creates_all_five(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    results = ensure_local_configs(agent_dir)

    expected_files = (
        "ralph-workflow.toml",
        "mcp.toml",
        "pipeline.toml",
        "artifacts.toml",
    )
    for fname in expected_files:
        assert (agent_dir / fname).exists(), f"{fname} should exist"
        assert isinstance(tomllib.loads((agent_dir / fname).read_text()), dict)

    assert len(results) == _EXPECTED_LOCAL_CONFIG_COUNT
    assert all(r.action == "created" for r in results)

    # Verify result list contains all policy files
    result_names = [r.path.name for r in results]
    for fname in expected_files:
        assert fname in result_names, f"{fname} should be in results"


def test_ensure_local_configs_includes_runtime_policy_files(tmp_path: Path) -> None:
    """Verify the default runtime policy TOMLs are in the result list."""
    agent_dir = tmp_path / ".agent"
    results = ensure_local_configs(agent_dir)

    policy_files = {"pipeline.toml", "artifacts.toml"}
    result_names = {r.path.name for r in results}
    assert policy_files.issubset(result_names), (
        f"Policy files {policy_files} not all in results {result_names}"
    )
    assert "agents.toml" not in result_names


def test_regenerate_all_force_creates_backups(tmp_path: Path) -> None:
    global_dir = tmp_path / "g"
    agent_dir = tmp_path / "a"
    global_dir.mkdir()
    agent_dir.mkdir()

    sentinel = "# SENTINEL"
    (global_dir / "ralph-workflow.toml").write_text(sentinel, encoding="utf-8")
    (global_dir / "ralph-workflow-mcp.toml").write_text(sentinel, encoding="utf-8")
    (global_dir / "ralph-workflow-pipeline.toml").write_text(sentinel, encoding="utf-8")
    (global_dir / "ralph-workflow-artifacts.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "ralph-workflow.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "mcp.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "agents.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "artifacts.toml").write_text(sentinel, encoding="utf-8")

    results = regenerate_all(global_dir=global_dir, agent_dir=agent_dir)

    assert len(results) == _EXPECTED_REGENERATE_COUNT
    assert all(r.action == "regenerated" for r in results)

    for result in results:
        assert result.backup is not None
        assert result.backup.exists()
        assert result.backup.read_text(encoding="utf-8") == sentinel
        assert isinstance(tomllib.loads(result.path.read_text()), dict)


def test_resolve_global_config_dir_honors_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert resolve_global_config_dir() == tmp_path


def test_resolve_global_config_dir_defaults_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert resolve_global_config_dir() == Path.home() / ".config"


def test_ensure_global_config_round_trips_through_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_global_config(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    scope = WorkspaceScope(tmp_path)
    cfg = loader_module.load_config(workspace_scope=scope)
    assert cfg.general.verbosity is not None


def test_bundled_global_template_parses_as_valid_toml() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow.toml"
    content = template.read_text(encoding="utf-8")
    result = tomllib.loads(content)
    assert isinstance(result, dict)


def test_bundled_mcp_template_describes_broad_multimodal_support() -> None:
    """Default mcp.toml must describe the broad multimodal surface, not image-only."""
    template = Path(ralph.policy.__file__).parent / "defaults" / "mcp.toml"
    content = template.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "read_image" in content
    assert "compatibility" in content
    # Must not imply image-only support
    assert "Multimodal image reading support" not in content


def test_local_template_defines_active_runtime_drain_bindings() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    drains = data["agent_drains"]

    for drain_name, chain_name in (
        ("planning", "planning"),
        ("development", "development"),
        ("development_analysis", "analysis"),
        ("development_commit", "commit"),
    ):
        assert drains.get(drain_name) == chain_name, (
            f"Expected active local template drain binding {drain_name!r} -> {chain_name!r}"
        )


def test_local_template_defines_active_agent_chain_defaults() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    chains = data["agent_chains"]

    assert chains["planning"] == ["claude/opus"]
    assert chains["development"] == [
        "opencode/minimax/MiniMax-M2.7-highspeed",
        "codex",
        "claude/sonnet",
    ]
    assert chains["analysis"] == ["opencode/openai/gpt-5.4"]
    assert chains["commit"] == ["claude/haiku"]

    # Review-era chains must not appear in the active default local template.
    review_era_chains = {"review", "fix"}
    assert not review_era_chains.intersection(chains), (
        f"Review-era chains still active in local template: "
        f"{review_era_chains.intersection(chains)}"
    )


def test_local_template_does_not_expose_review_era_drain_bindings() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    drains = data["agent_drains"]

    review_era_drains = {"review", "review_analysis", "review_commit", "fix"}
    assert not review_era_drains.intersection(drains), (
        f"Review-era drains still active in local template: "
        f"{review_era_drains.intersection(drains)}"
    )


def test_local_template_mentions_ccs_alternative() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow-local.toml"
    content = template.read_text(encoding="utf-8")
    assert "ccs/work" in content


def test_generated_local_template_validates_against_bundled_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults_dir = Path(ralph.policy.__file__).parent / "defaults"
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        (defaults_dir / "ralph-workflow-local.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (agent_dir / "pipeline.toml").write_text(
        (defaults_dir / "pipeline.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (agent_dir / "artifacts.toml").write_text(
        (defaults_dir / "artifacts.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", agent_dir / "ralph-workflow.toml")
    config = loader_module.load_config(workspace_scope=WorkspaceScope(tmp_path))
    bundle = load_policy(agent_dir, config=config)

    for phase_name, phase_def in bundle.pipeline.phases.items():
        if phase_def.role == "terminal":
            continue
        assert phase_def.drain in bundle.agents.agent_drains, (
            f"Generated local template left phase {phase_name!r} drain {phase_def.drain!r} unbound"
        )


def test_generated_local_template_missing_required_drain_fails_policy_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults_dir = Path(ralph.policy.__file__).parent / "defaults"
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    broken = (
        (defaults_dir / "ralph-workflow-local.toml")
        .read_text(encoding="utf-8")
        .replace('development_commit = "commit"\n', "")
    )
    (agent_dir / "ralph-workflow.toml").write_text(broken, encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text(
        (defaults_dir / "pipeline.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (agent_dir / "artifacts.toml").write_text(
        (defaults_dir / "artifacts.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", agent_dir / "ralph-workflow.toml")
    config = loader_module.load_config(workspace_scope=WorkspaceScope(tmp_path))
    with pytest.raises(LoaderPolicyValidationError, match="unbound drains"):
        load_policy(agent_dir, config=config)


def test_ensure_local_configs_adds_default_gitignore_entries(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"

    ensure_local_configs(agent_dir)

    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    for line in _EXPECTED_DEFAULT_GITIGNORE_LINES:
        assert line in content


def test_ensure_local_configs_preserves_gitignore_without_duplicate_default_entries(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / ".agent"
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".agent/\n", encoding="utf-8")

    ensure_local_configs(agent_dir)
    ensure_local_configs(agent_dir)

    content = gitignore.read_text(encoding="utf-8")
    # The parent ``.agent/`` rule is in the file exactly once (the
    # explicit ``.agent/ralph-explore/`` child rule is permitted
    # alongside it because the seeder appends the child only when
    # it is absent).
    assert content.count(".agent/\n") == 1
    assert content.count("/PROMPT*") == 1
    assert content.count("wt-*/") == 1


def test_ensure_local_configs_gitignore_includes_python_patterns(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"

    ensure_local_configs(agent_dir)

    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    for python_pattern in ("__pycache__/", ".venv/", ".mypy_cache/", ".pytest_cache/"):
        assert python_pattern in content, (
            f"Expected Python pattern {python_pattern!r} in .gitignore, got: {content!r}"
        )


def test_ensure_local_configs_gitignore_includes_node_patterns(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"

    ensure_local_configs(agent_dir)

    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert ".next/" in content


def test_ensure_local_configs_gitignore_preserves_user_owned_existing_entries(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / ".agent"
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        "# my custom rule\n__pycache__/\n",
        encoding="utf-8",
    )

    ensure_local_configs(agent_dir)

    content = gitignore.read_text(encoding="utf-8")
    assert "# my custom rule" in content
    assert "node_modules/" in content


def test_ensure_local_configs_gitignore_dedup_when_pattern_already_present(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / ".agent"
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("__pycache__/\n", encoding="utf-8")

    ensure_local_configs(agent_dir)
    # Re-run idempotency: pattern already present, must not be added again.
    ensure_local_configs(agent_dir)

    content = gitignore.read_text(encoding="utf-8")
    assert content.count("__pycache__/") == 1


def test_ensure_local_configs_gitignore_covers_representative_local_paths(
    tmp_git_repo: Path,
) -> None:
    agent_dir = tmp_git_repo / ".agent"
    ensure_local_configs(agent_dir)

    with Repo(tmp_git_repo) as repo:
        raw = repo.git.check_ignore(*_EXPECTED_IGNORED_LOCAL_PATHS).splitlines()
    ignored = {p.strip() for p in raw}
    missing = [p for p in _EXPECTED_IGNORED_LOCAL_PATHS if p not in ignored]
    assert not missing, f"Paths not covered by .gitignore: {missing}"


def _paths_ignored_by_check_ignore(cwd: Path, paths: tuple[str, ...]) -> set[str]:
    """Return the subset of paths git check-ignore -v reports as ignored.

    A path is considered ignored iff a non-negation pattern matched it.
    The matching pattern is the third colon-separated segment of the first
    stdout line (after a tab).
    """
    if not paths:
        return set()
    result = Repo(cwd).git.check_ignore("-v", "--", *paths)
    ignored: set[str] = set()
    for line in result.splitlines():
        if not line:
            continue
        prefix, _, path = line.partition("\t")
        pattern = prefix.rsplit(":", 1)[-1] if ":" in prefix else prefix
        if not pattern.startswith("!"):
            ignored.add(path.strip())
    return ignored


def test_ensure_local_configs_gitignore_matches_per_category_positive_paths(
    tmp_git_repo: Path,
) -> None:
    """Each new gitignore category must cover at least one representative path."""
    agent_dir = tmp_git_repo / ".agent"
    ensure_local_configs(agent_dir)

    ignored = _paths_ignored_by_check_ignore(
        tmp_git_repo, _EXPECTED_NEW_CATEGORY_POSITIVE_IGNORED_PATHS
    )
    missing = [p for p in _EXPECTED_NEW_CATEGORY_POSITIVE_IGNORED_PATHS if p not in ignored]
    assert not missing, f"New-category paths not covered by .gitignore: {missing}"


def test_ensure_local_configs_does_not_overignore_tracked_conventions(
    tmp_git_repo: Path,
) -> None:
    """Tracked files and source-controlled .gitkeep markers must NOT be ignored.

    Specifically locks in:
    * .vscode/ — upstream repo has tracked files under .vscode/
    * Cargo.lock — Rust apps intentionally check it in
    * go.sum — Go modules intentionally check it in
    * <dir>/.gitkeep — every dir-pattern added in step 3 has a paired
      `!<dir>/.gitkeep` allowlist so source-controlled marker files stay
      tracked even when the dir itself is ignored.
    """
    agent_dir = tmp_git_repo / ".agent"
    ensure_local_configs(agent_dir)

    ignored = _paths_ignored_by_check_ignore(tmp_git_repo, _EXPECTED_TRACKED_NON_IGNORED_PATHS)
    over_ignored = [p for p in _EXPECTED_TRACKED_NON_IGNORED_PATHS if p in ignored]
    assert not over_ignored, (
        f"Tracked files or .gitkeep markers that the new gitignore must NOT ignore: {over_ignored}"
    )


def test_ensure_local_configs_gitignore_idempotent_for_new_categories(
    tmp_git_repo: Path,
) -> None:
    """Re-running ensure_local_configs must not duplicate the new gitignore lines."""
    agent_dir = tmp_git_repo / ".agent"
    ensure_local_configs(agent_dir)
    ensure_local_configs(agent_dir)

    content = (tmp_git_repo / ".gitignore").read_text(encoding="utf-8")
    for new_pattern in (
        "target/*",
        "vendor/bundle/*",
        ".dart_tool/",
        "_build/*",
        ".bsp/",
        ".terraform/",
        "coverage/",
        "tmp/",
        ".tox/",
        ".idea/",
        "Desktop.ini",
    ):
        assert content.count(new_pattern) == 1, (
            f"Pattern {new_pattern!r} duplicated in .gitignore: {content!r}"
        )


def test_ensure_local_configs_bootstraps_a_valid_policy_bundle(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    ensure_local_configs(agent_dir)

    bundle = load_policy(agent_dir)

    assert bundle.pipeline.entry_phase == "planning"
    assert bundle.pipeline.phases["development"].parallelization is not None


def test_regenerate_all_bootstraps_a_valid_policy_bundle(tmp_path: Path) -> None:
    global_dir = tmp_path / "g"
    agent_dir = tmp_path / "a"
    global_dir.mkdir()
    agent_dir.mkdir()

    regenerate_all(global_dir=global_dir, agent_dir=agent_dir)

    bundle = load_policy(agent_dir)

    assert bundle.pipeline.terminal_phase == "complete"
    assert bundle.pipeline.phases["development"].parallelization is not None


def test_global_template_bootstraps_first_run_policy_without_local_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh installs must start from the generated user-global config alone.

    This covers the exact first-run path users hit after installation: Ralph creates
    ~/.config/ralph-workflow.toml, no project-local .agent/ralph-workflow.toml exists yet,
    and the runtime falls back to bundled pipeline/artifact defaults. If the generated
    global template drops any active drain binding, startup fails before the user can even
    run `ralph --init`, which is why this must stay protected by an integration-style test.
    """
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    ensure_global_config(global_dir)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))

    project_root = tmp_path / "project"
    project_root.mkdir()

    config = loader_module.load_config(workspace_scope=WorkspaceScope(project_root))
    bundle = load_policy(project_root / ".agent", config=config)

    required_drains = {
        "planning",
        "planning_analysis",
        "development",
        "development_analysis",
        "development_commit",
    }
    assert required_drains.issubset(bundle.agents.agent_drains), (
        "Fresh-install global config must bind every non-terminal drain used by the "
        "bundled default pipeline so Ralph works out of the box before any local "
        "override exists."
    )


def test_global_template_missing_active_drain_breaks_first_run_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protect the exact startup regression users see when global drain bindings drift.

    The failure mode is severe: Ralph rejects startup during policy loading, before any
    planning or development can begin. This test documents the contract by simulating the
    broken fresh-install config and asserting the runtime surfaces the same unbound-drain
    class of failure the user would see on day zero.
    """
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    created = ensure_global_config(global_dir)
    config_path = created.path
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('development_commit = "commit"\n', ""),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))

    project_root = tmp_path / "project"
    project_root.mkdir()

    config = loader_module.load_config(workspace_scope=WorkspaceScope(project_root))
    with pytest.raises(LoaderPolicyValidationError, match="unbound drains"):
        load_policy(project_root / ".agent", config=config)


# ---------------------------------------------------------------------------
# Git-exclude auto-seed (PA-002, PA-003, PA-004)
# ---------------------------------------------------------------------------
#
# Mirrors the ``auto_seed_default_gitignore`` surface for ``.git/info/exclude``
# so engine-owned runtime artifacts never enter the repo in the first place.
# Canonical patterns use root-anchored ``/checkpoint.json`` (NOT bare
# ``checkpoint.json`` which would match every nested subdirectory) and the
# ``.agent/completion_seen_*.json`` glob (NOT the Python abstraction
# identifier ``completion_sentinel_*.json``).


def test_ensure_local_configs_seeds_git_exclude(tmp_path: Path) -> None:
    """``ensure_local_configs`` must seed ``.git/info/exclude`` with the canonical patterns.

    Note: ``.git/info/exclude`` only lives inside an actual git repo, so this
    test uses a real ``Repo.init``-created repo instead of a bare tmp_path.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    Repo.init(repo_root)
    agent_dir = repo_root / ".agent"

    ensure_local_configs(agent_dir)

    exclude_path = repo_root / ".git" / "info" / "exclude"
    assert exclude_path.exists(), ".git/info/exclude must be created by bootstrap"
    content = exclude_path.read_text(encoding="utf-8")
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in content, f"Missing canonical exclude pattern: {pattern!r}"


def test_ensure_local_configs_git_exclude_idempotent(tmp_path: Path) -> None:
    """Re-running bootstrap must not duplicate any canonical exclude pattern."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    Repo.init(repo_root)
    agent_dir = repo_root / ".agent"

    ensure_local_configs(agent_dir)
    ensure_local_configs(agent_dir)

    exclude_path = repo_root / ".git" / "info" / "exclude"
    lines = set(exclude_path.read_text(encoding="utf-8").splitlines())
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in lines, (
            f"Pattern {pattern!r} missing from .git/info/exclude: {sorted(lines)!r}"
        )
        assert sum(1 for line in lines if line == pattern) == 1, (
            f"Pattern {pattern!r} duplicated in .git/info/exclude: {sorted(lines)!r}"
        )


def test_ensure_local_configs_git_exclude_covers_canonical_paths(tmp_git_repo: Path) -> None:
    """A representative agent-runtime artifact path is listed in the exclude file."""
    agent_dir = tmp_git_repo / ".agent"
    ensure_local_configs(agent_dir)

    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    content = exclude_path.read_text(encoding="utf-8")
    # The canonical on-disk completion-sentinel filename glob MUST be present.
    assert ".agent/completion_seen_*.json" in content, (
        f"Canonical completion-sentinel glob missing from .git/info/exclude:\n{content}"
    )
    # The root-anchored ``/checkpoint.json`` MUST be present in exclude too.
    assert "/checkpoint.json" in content, (
        f"Root-anchored /checkpoint.json missing from .git/info/exclude:\n{content}"
    )


def test_auto_seed_default_git_exclude_creates_file_when_missing(tmp_path: Path) -> None:
    """Without an existing ``.git/info/exclude``, the helper creates one.

    Note: the helper works on ``repo_root/.git/info/exclude`` directly -- it
    does NOT require an actual git working tree (it creates the parent dirs).
    """
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    assert not (repo_root / ".git" / "info" / "exclude").exists()

    appended = auto_seed_default_git_exclude(repo_root)

    exclude_path = repo_root / ".git" / "info" / "exclude"
    assert exclude_path.exists()
    content = exclude_path.read_text(encoding="utf-8")
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in content, f"Missing default exclude pattern: {pattern!r}"
    assert appended == list(_DEFAULT_GIT_EXCLUDE_PATTERNS)


def test_auto_seed_default_git_exclude_is_idempotent(tmp_path: Path) -> None:
    """Calling the helper twice returns ``[]`` on the second call and does not duplicate."""
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    auto_seed_default_git_exclude(repo_root)

    appended = auto_seed_default_git_exclude(repo_root)

    assert appended == [], f"Expected empty list on idempotent call, got {appended!r}"
    lines = set((repo_root / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines())
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in lines, f"Pattern {pattern!r} missing: {sorted(lines)!r}"
        assert sum(1 for line in lines if line == pattern) == 1, (
            f"Pattern {pattern!r} duplicated: {sorted(lines)!r}"
        )


def test_auto_seed_default_git_exclude_preserves_user_entries(tmp_path: Path) -> None:
    """A user-customized exclude file with the full default set is preserved."""
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    exclude_path = repo_root / ".git" / "info" / "exclude"
    user_block = "\n".join(("# my custom exclude", *_DEFAULT_GIT_EXCLUDE_PATTERNS))
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text(user_block + "\n", encoding="utf-8")

    appended = auto_seed_default_git_exclude(repo_root)

    assert appended == []
    content_lines = exclude_path.read_text(encoding="utf-8").splitlines()
    assert content_lines[0] == "# my custom exclude"
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in content_lines, f"Missing pattern: {pattern!r}"


def test_auto_seed_default_git_exclude_handles_missing_git_repo(tmp_path: Path) -> None:
    """When the repo_root has no ``.git/`` directory, the helper still works.

    It creates the ``.git/info/`` parents and writes the file. This is the
    "Ralph is invoked in a non-git project" case -- bootstrap must not raise.
    """
    repo_root = tmp_path / "no_git"
    repo_root.mkdir()
    assert not (repo_root / ".git").exists()

    appended = auto_seed_default_git_exclude(repo_root)

    assert appended == list(_DEFAULT_GIT_EXCLUDE_PATTERNS)
    assert (repo_root / ".git" / "info" / "exclude").exists()


def test_default_gitignore_includes_root_anchored_checkpoint_json() -> None:
    """``_DEFAULT_GITIGNORE_PATTERNS`` MUST contain root-anchored ``/checkpoint.json``.

    PA-002 regression: bare ``checkpoint.json`` (without leading slash) would
    match every nested directory. The root-anchored form ``/checkpoint.json``
    follows the existing ``/PROMPT*`` and ``/storage/*.key`` convention.
    """
    assert "/checkpoint.json" in _DEFAULT_GITIGNORE_PATTERNS, (
        f"/checkpoint.json MUST be in _DEFAULT_GITIGNORE_PATTERNS, got: "
        f"{_DEFAULT_GITIGNORE_PATTERNS!r}"
    )
    bare_match = [p for p in _DEFAULT_GITIGNORE_PATTERNS if p == "checkpoint.json"]
    assert not bare_match, (
        f"bare 'checkpoint.json' MUST NOT appear (would match every subdir); "
        f"got bare match in: {_DEFAULT_GITIGNORE_PATTERNS!r}"
    )
