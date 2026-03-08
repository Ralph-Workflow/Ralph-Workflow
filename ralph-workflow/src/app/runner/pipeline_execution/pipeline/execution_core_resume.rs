// Pipeline Resume and Config State Loading
//
// This module handles loading the resume state and configuration for the pipeline,
// including checkpoint restoration, environment variable restoration, and cloud config
// initialization.

struct ResumeAndConfigState {
    config: crate::config::Config,
    run_context: crate::checkpoint::RunContext,
    resume_checkpoint: Option<crate::checkpoint::PipelineCheckpoint>,
}


fn load_resume_and_config_state(ctx: &PipelineContext) -> anyhow::Result<ResumeAndConfigState> {
    use crate::checkpoint::RunContext;

    let resume_result = offer_resume_if_checkpoint_exists(
        &ctx.args,
        &ctx.config,
        &ctx.registry,
        &ctx.logger,
        &ctx.developer_agent,
        &ctx.reviewer_agent,
        &*ctx.workspace,
    );

    let resume_result = match resume_result {
        Some(result) => Some(result),
        None => handle_resume_with_validation(
            &ctx.args,
            &ctx.config,
            &ctx.registry,
            &ctx.logger,
            &ctx.developer_display,
            &ctx.reviewer_display,
            &*ctx.workspace,
        )?,
    };

    let resume_checkpoint = resume_result.map(|r| r.checkpoint);
    let run_context = resume_checkpoint
        .as_ref()
        .map_or_else(RunContext::new, RunContext::from_checkpoint);

    let mut config = resume_checkpoint
        .as_ref()
        .map_or_else(|| ctx.config.clone(), |checkpoint| {
            use crate::checkpoint::apply_checkpoint_to_config;
            let mut restored_config = ctx.config.clone();
            apply_checkpoint_to_config(&mut restored_config, checkpoint);
            ctx.logger.info("Restored configuration from checkpoint:");
            if checkpoint.cli_args.developer_iters > 0 {
                ctx.logger.info(&format!(
                    "  Developer iterations: {} (from checkpoint)",
                    checkpoint.cli_args.developer_iters
                ));
            }
            if checkpoint.cli_args.reviewer_reviews > 0 {
                ctx.logger.info(&format!(
                    "  Reviewer passes: {} (from checkpoint)",
                    checkpoint.cli_args.reviewer_reviews
                ));
            }
            restored_config
        });

    if let Some(ref checkpoint) = resume_checkpoint {
        use crate::checkpoint::restore::restore_environment_from_checkpoint;
        let restored_count = restore_environment_from_checkpoint(checkpoint);
        if restored_count > 0 {
            ctx.logger.info(&format!(
                "  Restored {restored_count} environment variable(s) from checkpoint"
            ));
        }
    }

    if config.cloud.enabled {
        resolve_cloud_git_defaults(&mut config, ctx)?;
        config
            .cloud
            .validate()
            .map_err(|e| anyhow::anyhow!("Cloud config validation failed: {e}"))?;
    }

    Ok(ResumeAndConfigState {
        config,
        run_context,
        resume_checkpoint,
    })
}

fn resolve_cloud_git_defaults(
    config: &mut crate::config::Config,
    ctx: &PipelineContext,
) -> anyhow::Result<()> {
    // Default push branch to the current branch name (safe, non-secret).
    if config.cloud.git_remote.push_branch.is_none() {
        let output = ctx.executor.execute(
            "git",
            &["rev-parse", "--abbrev-ref", "HEAD"],
            &[],
            Some(&ctx.repo_root),
        )?;
        if !output.status.success() {
            let stderr = crate::cloud::redaction::redact_secrets(&output.stderr);
            return Err(anyhow::anyhow!(
                "Failed to detect current branch for cloud push (git rev-parse). stderr: {stderr}"
            ));
        }

        let branch = output.stdout.trim();
        if branch.is_empty() || branch == "HEAD" {
            return Err(anyhow::anyhow!(
                "Cloud mode requires a branch name for pushing/PRs. Current ref is detached (HEAD). Set RALPH_GIT_PUSH_BRANCH explicitly."
            ));
        }
        config.cloud.git_remote.push_branch = Some(branch.to_string());
    }

    // Defensive: reject literal HEAD even if explicitly set.
    if config
        .cloud
        .git_remote
        .push_branch
        .as_deref()
        .is_some_and(|b| b.trim() == "HEAD")
    {
        return Err(anyhow::anyhow!(
            "RALPH_GIT_PUSH_BRANCH must be a branch name (not literal 'HEAD')"
        ));
    }

    Ok(())
}
