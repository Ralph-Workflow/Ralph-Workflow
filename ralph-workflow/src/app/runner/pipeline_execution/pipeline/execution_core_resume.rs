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

fn load_resume_and_config_state(
    ctx: &crate::app::context::PipelineContext,
) -> anyhow::Result<ResumeAndConfigState> {
    let resume_result = crate::app::resume::offer_resume_if_checkpoint_exists(
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
        None => crate::app::resume::handle_resume_with_validation(
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
    let run_context = resume_checkpoint.as_ref().map_or_else(
        crate::checkpoint::RunContext::new,
        crate::checkpoint::RunContext::from_checkpoint,
    );

    let config = resume_checkpoint.as_ref().map_or_else(
        || ctx.config.clone(),
        |checkpoint| {
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
            crate::app::initialization::restore_config_from_checkpoint(
                ctx.config.clone(),
                checkpoint,
            )
        },
    );

    let config = if config.cloud.enabled {
        apply_cloud_git_defaults(&config, ctx)?
    } else {
        config
    };

    if let Some(ref checkpoint) = resume_checkpoint {
        let restored_count =
            crate::checkpoint::restore::restore_environment_from_checkpoint(checkpoint);
        if restored_count > 0 {
            ctx.logger.info(&format!(
                "  Restored {restored_count} environment variable(s) from checkpoint"
            ));
        }
    }

    Ok(ResumeAndConfigState {
        config,
        run_context,
        resume_checkpoint,
    })
}

fn apply_cloud_git_defaults(
    config: &crate::config::Config,
    _ctx: &crate::app::context::PipelineContext,
) -> anyhow::Result<crate::config::Config> {
    Ok(config.clone())
}
