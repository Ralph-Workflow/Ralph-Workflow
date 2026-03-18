// Repository discovery functions.
//
// This module contains:
// - discover_repo_root_for_workspace: Discovers repo root using effect handler

pub(crate) fn discover_repo_root_for_workspace<H: crate::app::effect::AppEffectHandler>(
    override_dir: Option<&std::path::Path>,
    handler: &mut H,
) -> anyhow::Result<std::path::PathBuf> {
    use crate::app::effect::{AppEffect, AppEffectResult};

    if let Some(dir) = override_dir {
        match handler.execute(AppEffect::SetCurrentDir {
            path: dir.to_path_buf(),
        }) {
            AppEffectResult::Ok => {}
            AppEffectResult::Error(e) => anyhow::bail!(e),
            other => anyhow::bail!("unexpected result from SetCurrentDir: {other:?}"),
        }
    }

    match handler.execute(AppEffect::GitRequireRepo) {
        AppEffectResult::Ok => {}
        AppEffectResult::Error(e) => anyhow::bail!("Not in a git repository: {e}"),
        other => anyhow::bail!("unexpected result from GitRequireRepo: {other:?}"),
    }

    match handler.execute(AppEffect::GitGetRepoRoot) {
        AppEffectResult::Path(p) => Ok(p),
        AppEffectResult::Error(e) => anyhow::bail!("Failed to get repo root: {e}"),
        other => anyhow::bail!("unexpected result from GitGetRepoRoot: {other:?}"),
    }
}
