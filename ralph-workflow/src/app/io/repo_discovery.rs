//! Repository root discovery IO boundary.
//!
//! Performs imperative git discovery using mutable effect handler state.
//! Lives in the `io/` boundary module because it performs real filesystem
//! and git side effects.

use crate::app::effect::{AppEffect, AppEffectHandler, AppEffectResult};
use std::path::{Path, PathBuf};

/// Discover the git repository root, optionally changing the working directory first.
///
/// Returns the resolved repository root path, or an error if the current (or
/// overridden) directory is not inside a git repository.
pub fn discover_repo_root(working_dir_override: Option<&Path>) -> anyhow::Result<PathBuf> {
    let mut h = crate::app::runtime_factory::create_effect_handler();
    if let Some(dir) = working_dir_override {
        set_current_dir_effect(&mut h, dir)?;
    }
    require_git_repo_effect(&mut h)?;
    get_repo_root_effect(&mut h)
}

fn set_current_dir_effect(h: &mut dyn AppEffectHandler, dir: &Path) -> anyhow::Result<()> {
    match h.execute(AppEffect::SetCurrentDir {
        path: dir.to_path_buf(),
    }) {
        AppEffectResult::Ok => Ok(()),
        AppEffectResult::Error(e) => anyhow::bail!(e),
        other => anyhow::bail!("unexpected result from SetCurrentDir: {other:?}"),
    }
}

fn require_git_repo_effect(h: &mut dyn AppEffectHandler) -> anyhow::Result<()> {
    match h.execute(AppEffect::GitRequireRepo) {
        AppEffectResult::Ok => Ok(()),
        AppEffectResult::Error(e) => anyhow::bail!("Not in a git repository: {e}"),
        other => anyhow::bail!("unexpected result from GitRequireRepo: {other:?}"),
    }
}

fn get_repo_root_effect(h: &mut dyn AppEffectHandler) -> anyhow::Result<PathBuf> {
    match h.execute(AppEffect::GitGetRepoRoot) {
        AppEffectResult::Path(p) => Ok(p),
        AppEffectResult::Error(e) => anyhow::bail!("Failed to get repo root: {e}"),
        other => anyhow::bail!("unexpected result from GitGetRepoRoot: {other:?}"),
    }
}
