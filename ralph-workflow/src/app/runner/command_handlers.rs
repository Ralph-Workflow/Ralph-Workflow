// Command handlers for listing and plumbing operations.
//
// This module contains:
// - handle_listing_commands: Handles --list-agents, --list-providers, etc.
// - handle_plumbing_commands: Handles --show-commit-msg, --apply-commit, etc.

/// Handles listing commands that don't require the full pipeline.
///
/// Returns `true` if a listing command was handled and we should exit.
fn handle_listing_commands(args: &Args, registry: &AgentRegistry, colors: Colors) -> bool {
    if args.agent_list.list_agents {
        handle_list_agents(registry);
        return true;
    }
    if args.agent_list.list_available_agents {
        handle_list_available_agents(registry);
        return true;
    }
    if args.provider_list.list_providers {
        handle_list_providers(colors);
        return true;
    }

    // Handle template commands
    let template_cmds = &args.template_commands;
    if template_cmds.init_templates_enabled()
        || template_cmds.validate
        || template_cmds.show.is_some()
        || template_cmds.list
        || template_cmds.list_all
        || template_cmds.variables.is_some()
        || template_cmds.render.is_some()
    {
        let _ = handle_template_commands(template_cmds, colors);
        return true;
    }

    false
}

/// Handles plumbing commands that require git repo but not full validation.
///
/// Returns `Ok(true)` if a plumbing command was handled and we should exit.
/// Returns `Ok(false)` if we should continue to the main pipeline.
///
/// # Workspace Support
///
/// When `workspace` is `Some`, the workspace-aware versions of plumbing commands
/// are used, enabling testing with `MemoryWorkspace`. When `None`, the direct
/// filesystem versions are used (production behavior).
fn handle_plumbing_commands<H: effect::AppEffectHandler>(
    args: &Args,
    logger: &Logger,
    colors: Colors,
    handler: &mut H,
    workspace: Option<&dyn crate::workspace::Workspace>,
) -> anyhow::Result<bool> {
    use plumbing::{get_commit_message_from_workspace, handle_apply_commit_with_handler};

    // Helper to set up working directory for plumbing commands using the effect handler
    fn setup_working_dir_via_handler<H: effect::AppEffectHandler>(
        override_dir: Option<&std::path::Path>,
        handler: &mut H,
    ) -> anyhow::Result<()> {
        use effect::{AppEffect, AppEffectResult};

        if let Some(dir) = override_dir {
            match handler.execute(AppEffect::SetCurrentDir {
                path: dir.to_path_buf(),
            }) {
                AppEffectResult::Ok => Ok(()),
                AppEffectResult::Error(e) => anyhow::bail!(e),
                other => anyhow::bail!("unexpected result from SetCurrentDir: {other:?}"),
            }
        } else {
            // Require git repo
            match handler.execute(AppEffect::GitRequireRepo) {
                AppEffectResult::Ok => {}
                AppEffectResult::Error(e) => anyhow::bail!(e),
                other => anyhow::bail!("unexpected result from GitRequireRepo: {other:?}"),
            }
            // Get repo root
            let repo_root = match handler.execute(AppEffect::GitGetRepoRoot) {
                AppEffectResult::Path(p) => p,
                AppEffectResult::Error(e) => anyhow::bail!(e),
                other => anyhow::bail!("unexpected result from GitGetRepoRoot: {other:?}"),
            };
            // Set current dir to repo root
            match handler.execute(AppEffect::SetCurrentDir { path: repo_root }) {
                AppEffectResult::Ok => Ok(()),
                AppEffectResult::Error(e) => anyhow::bail!(e),
                other => anyhow::bail!("unexpected result from SetCurrentDir: {other:?}"),
            }
        }
    }

    // Show commit message
    if args.commit_display.show_commit_msg {
        setup_working_dir_via_handler(args.working_dir_override.as_deref(), handler)?;
        let ws = workspace.ok_or_else(|| {
            anyhow::anyhow!(
                "--show-commit-msg requires workspace context. Run this command after the pipeline has initialized."
            )
        })?;
        let msg = get_commit_message_from_workspace(ws)?;
        logger.info(&msg);
        return Ok(true);
    }

    // Apply commit
    if args.commit_plumbing.apply_commit {
        setup_working_dir_via_handler(args.working_dir_override.as_deref(), handler)?;
        let ws = workspace.ok_or_else(|| {
            anyhow::anyhow!(
                "--apply-commit requires workspace context. Run this command after the pipeline has initialized."
            )
        })?;
        return handle_apply_commit_with_handler(ws, handler, logger, colors).map(|()| true);
    }

    // Reset start commit
    if args.commit_display.reset_start_commit {
        setup_working_dir_via_handler(args.working_dir_override.as_deref(), handler)?;

        // Use the effect handler for reset_start_commit
        return match handler.execute(effect::AppEffect::GitResetStartCommit) {
            effect::AppEffectResult::String(oid) => {
                // Simple case - just got the OID back
                let short_oid = &oid[..8.min(oid.len())];
                logger.success(&format!("Starting commit reference reset ({short_oid})"));
                logger.info(".agent/start_commit has been updated");
                Ok(true)
            }
            effect::AppEffectResult::Error(e) => {
                logger.error(&format!("Failed to reset starting commit: {e}"));
                anyhow::bail!("Failed to reset starting commit");
            }
            other => anyhow::bail!("unexpected result from GitResetStartCommit: {other:?}"),
        };
    }

    // Show baseline state
    if args.commit_display.show_baseline {
        setup_working_dir_via_handler(args.working_dir_override.as_deref(), handler)?;

        return match handle_show_baseline() {
            Ok(()) => Ok(true),
            Err(e) => {
                logger.error(&format!("Failed to show baseline: {e}"));
                anyhow::bail!("Failed to show baseline");
            }
        };
    }

    Ok(false)
}
