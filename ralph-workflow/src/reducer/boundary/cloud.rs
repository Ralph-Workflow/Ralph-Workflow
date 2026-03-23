//! Cloud mode effect handlers.
//!
//! This module implements effect handlers for cloud-specific operations:
//! - Git authentication configuration
//! - Remote push operations
//! - Pull request creation
//! - Progress reporting
//!
//! All handlers follow the reducer architecture contract:
//! - Execute a single operation
//! - Emit events describing outcomes
//! - No retry logic (reducer decides)

use super::MainEffectHandler;
use crate::common::domain_types::NonEmptyString;
use crate::phases::PhaseContext;
use crate::reducer::domain::branch::parse_head_push_refspec;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{CommitEvent, PipelineEvent};
use crate::reducer::ui_event::UIEvent;

impl MainEffectHandler {
    /// Configure git authentication for remote operations.
    ///
    /// This handler sets up git credentials based on the auth method:
    /// - SSH key: Configure `GIT_SSH_COMMAND` environment variable
    /// - Token: Set up git credential helper
    /// - Credential helper: Configure external helper
    pub(super) fn handle_configure_git_auth(
        ctx: &PhaseContext<'_>,
        auth_method: &str,
    ) -> EffectResult {
        ctx.logger
            .info(&format!("Configuring git authentication: {auth_method}"));

        configure_git_auth_method(ctx, auth_method);

        EffectResult::event(PipelineEvent::Commit(CommitEvent::GitAuthConfigured))
    }

    /// Push commits to remote repository.
    ///
    /// Executes git push command and emits domain-shaped execution outcome.
    /// The reducer/orchestrator interprets the outcome and applies policy-level success/failure.
    pub(super) fn handle_push_to_remote(
        ctx: &PhaseContext<'_>,
        remote: String,
        branch: String,
        force: bool,
        commit_sha: String,
    ) -> EffectResult {
        ctx.logger.info(&format!(
            "Pushing commit {} to {}/{}{}",
            &commit_sha[..7.min(commit_sha.len())],
            remote,
            branch,
            if force { " (force)" } else { "" }
        ));

        let Some(refspec) = build_head_push_refspec(&branch) else {
            return push_invalid_branch_result(ctx, remote, branch);
        };

        let argv = build_push_argv(ctx, remote.clone(), refspec, force);
        let git_args: Vec<&str> = argv.iter().map(std::string::String::as_str).collect();

        let result = ctx
            .executor
            .execute("git", &git_args, &[], Some(ctx.repo_root));

        interpret_push_result(ctx, result, remote, branch, commit_sha)
    }

    /// Create a pull request on the remote platform.
    ///
    /// Uses gh CLI for GitHub or glab CLI for GitLab.
    pub(super) fn handle_create_pull_request(
        ctx: &PhaseContext<'_>,
        base_branch: &str,
        head_branch: &str,
        title: &str,
        body: &str,
    ) -> EffectResult {
        ctx.logger
            .info(&format!("Creating PR: {head_branch} -> {base_branch}"));

        let validated_title = match NonEmptyString::try_from_str(title) {
            Ok(valid) => valid,
            Err(err) => {
                let error = crate::cloud::redaction::redact_secrets(&err.to_string());
                ctx.logger
                    .warn(&format!("Pull request title validation failed: {error}"));
                return pull_request_failed_result(error);
            }
        };

        try_gh_then_glab_create_pr(
            ctx,
            base_branch,
            head_branch,
            validated_title.as_str(),
            body,
        )
    }
}

fn pr_created_result(url: String) -> EffectResult {
    let number = url
        .rsplit('/')
        .next()
        .and_then(|s| s.parse::<u32>().ok())
        .unwrap_or(0);
    let ui = UIEvent::PullRequestCreated {
        url: url.clone(),
        number,
    };
    EffectResult::with_ui(
        PipelineEvent::Commit(CommitEvent::PullRequestCreated { url, number }),
        vec![ui],
    )
}

fn pull_request_failed_result(error: String) -> EffectResult {
    let ui = UIEvent::PullRequestFailed {
        error: error.clone(),
    };
    EffectResult::with_ui(
        PipelineEvent::Commit(CommitEvent::PullRequestFailed { error }),
        vec![ui],
    )
}

fn try_gh_then_glab_create_pr(
    ctx: &PhaseContext<'_>,
    base_branch: &str,
    head_branch: &str,
    title: &str,
    body: &str,
) -> EffectResult {
    let gh_result = ctx.executor.execute(
        "gh",
        &[
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            head_branch,
            "--title",
            title,
            "--body",
            body,
        ],
        &[],
        Some(ctx.repo_root),
    );
    match gh_result {
        Ok(output) if output.succeeded() => {
            let url = output.stdout.trim().to_string();
            ctx.logger.info(&format!("Pull request created: {url}"));
            pr_created_result(url)
        }
        Ok(output) => {
            let error = crate::cloud::redaction::redact_secrets(&output.stderr);
            ctx.logger.warn(&format!("PR creation failed: {error}"));
            pull_request_failed_result(error)
        }
        Err(gh_err) => {
            ctx.logger
                .info("gh CLI not available, trying glab for GitLab");
            try_glab_create_pr(ctx, base_branch, head_branch, title, body, gh_err)
        }
    }
}

fn try_glab_create_pr(
    ctx: &PhaseContext<'_>,
    base_branch: &str,
    head_branch: &str,
    title: &str,
    body: &str,
    gh_err: std::io::Error,
) -> EffectResult {
    let glab_result = ctx.executor.execute(
        "glab",
        &[
            "mr",
            "create",
            "--target-branch",
            base_branch,
            "--source-branch",
            head_branch,
            "--title",
            title,
            "--description",
            body,
        ],
        &[],
        Some(ctx.repo_root),
    );
    match glab_result {
        Ok(output) if output.succeeded() => {
            let url = output.stdout.trim().to_string();
            ctx.logger.info(&format!("Merge request created: {url}"));
            pr_created_result(url)
        }
        Ok(output) => {
            let error = crate::cloud::redaction::redact_secrets(&output.stderr);
            ctx.logger.warn(&format!("MR creation failed: {error}"));
            pull_request_failed_result(error)
        }
        Err(e2) => {
            let e = crate::cloud::redaction::redact_secrets(&gh_err.to_string());
            let e2 = crate::cloud::redaction::redact_secrets(&e2.to_string());
            ctx.logger.warn(&format!(
                "Neither gh nor glab CLI available: gh error: {e}, glab error: {e2}",
            ));
            let error = format!("Neither gh nor glab CLI available (gh: {e}, glab: {e2})");
            pull_request_failed_result(error)
        }
    }
}

fn push_invalid_branch_result(
    ctx: &PhaseContext<'_>,
    remote: String,
    branch: String,
) -> EffectResult {
    let error =
        crate::cloud::redaction::redact_secrets(&format!("Invalid push branch name: '{branch}'"));
    ctx.logger.warn(&format!("Git push skipped: {error}"));
    let ui = UIEvent::PushFailed {
        remote: remote.clone(),
        branch: branch.clone(),
        error: error.clone(),
    };
    EffectResult::with_ui(
        PipelineEvent::Commit(CommitEvent::PushFailed {
            remote,
            branch,
            error,
        }),
        vec![ui],
    )
}

fn build_push_argv(
    ctx: &PhaseContext<'_>,
    remote: String,
    refspec: String,
    force: bool,
) -> Vec<String> {
    // Build git push command.
    // Auth is configured in a checkpoint-safe way:
    // - ssh-key: via GIT_SSH_COMMAND (set in ConfigureGitAuth)
    // - token: via ephemeral credential helper that reads token from env
    // - credential-helper: via per-command credential.helper override
    let auth_args: Vec<String> = match &ctx.cloud.git_remote.auth_method {
        crate::config::types::GitAuthMethod::SshKey { .. } => vec![],
        crate::config::types::GitAuthMethod::Token { .. } => vec![
            "-c".to_string(),
            "credential.helper=!f() { echo username=$RALPH_GIT_TOKEN_USERNAME; echo password=$RALPH_GIT_TOKEN; }; f"
                .to_string(),
            "-c".to_string(),
            "credential.useHttpPath=true".to_string(),
        ],
        crate::config::types::GitAuthMethod::CredentialHelper { helper } => vec![
            "-c".to_string(),
            format!("credential.helper={helper}"),
            "-c".to_string(),
            "credential.useHttpPath=true".to_string(),
        ],
    };
    auth_args
        .into_iter()
        .chain(std::iter::once("push".to_string()))
        .chain(std::iter::once(remote))
        .chain(std::iter::once(refspec))
        .chain(force.then(|| "--force".to_string()))
        .collect()
}

fn interpret_push_result(
    ctx: &PhaseContext<'_>,
    result: std::io::Result<crate::executor::ProcessOutput>,
    remote: String,
    branch: String,
    commit_sha: String,
) -> EffectResult {
    match result {
        Ok(output) => {
            let exit_code = output.status.code().unwrap_or(-1);
            let success = output.status.success();
            let stderr = output.stderr.clone();
            ctx.logger.info(&format!(
                "Git push executed for {remote}/{branch} (exit: {exit_code})"
            ));
            let exec_result: crate::reducer::event::ProcessExecutionResult = output.into();
            let primary_event = PipelineEvent::Commit(CommitEvent::PushExecuted {
                remote: remote.clone(),
                branch: branch.clone(),
                commit_sha: commit_sha.clone(),
                result: exec_result,
            });
            push_outcome_result(
                ctx,
                primary_event,
                remote,
                branch,
                commit_sha,
                success,
                &stderr,
            )
        }
        Err(e) => {
            let error = crate::cloud::redaction::redact_secrets(&e.to_string());
            ctx.logger
                .warn(&format!("Git push execution failed: {error}"));
            let ui = UIEvent::PushFailed {
                remote: remote.clone(),
                branch: branch.clone(),
                error: error.clone(),
            };
            EffectResult::with_ui(
                PipelineEvent::Commit(CommitEvent::PushFailed {
                    remote,
                    branch,
                    error,
                }),
                vec![ui],
            )
        }
    }
}

fn push_outcome_result(
    ctx: &PhaseContext<'_>,
    primary_event: PipelineEvent,
    remote: String,
    branch: String,
    commit_sha: String,
    success: bool,
    stderr: &str,
) -> EffectResult {
    if success {
        ctx.logger
            .info(&format!("Successfully pushed to {remote}/{branch}"));
        let ui = UIEvent::PushCompleted {
            remote,
            branch,
            commit_sha,
        };
        EffectResult::event(primary_event).with_ui_event(ui)
    } else {
        let error = crate::cloud::redaction::redact_secrets(stderr);
        ctx.logger.warn(&format!("Git push failed: {error}"));
        let ui = UIEvent::PushFailed {
            remote,
            branch,
            error,
        };
        EffectResult::event(primary_event).with_ui_event(ui)
    }
}

fn configure_git_auth_method(ctx: &PhaseContext<'_>, auth_method: &str) {
    // Parse auth method string (format: "method:param")
    let parts: Vec<&str> = auth_method.splitn(2, ':').collect();
    let method = parts.first().unwrap_or(&"ssh-key");
    let param = parts.get(1).unwrap_or(&"default");

    match *method {
        "ssh-key" => configure_ssh_key_auth(ctx, param),
        "token" => {
            ctx.logger.info(&format!(
                "Configuring token authentication for user: {param}"
            ));
            let _ = ctx.env.disable_git_terminal_prompt();
        }
        "credential-helper" => {
            ctx.logger
                .info(&format!("Using credential helper: {param}"));
            let _ = ctx.env.disable_git_terminal_prompt();
        }
        _ => {
            ctx.logger.warn(&format!(
                "Unknown auth method: {method}, falling back to default SSH"
            ));
        }
    }
}

fn configure_ssh_key_auth(ctx: &PhaseContext<'_>, param: &str) {
    if param == "default" {
        ctx.logger
            .info("Using default SSH authentication (SSH_AUTH_SOCK or ~/.ssh/id_rsa)");
    } else if ctx.env.configure_git_ssh_command(param).is_ok() {
        ctx.logger
            .info("Set GIT_SSH_COMMAND to use provided SSH key");
    } else {
        ctx.logger
            .warn("Invalid SSH key path for cloud git auth; falling back to default SSH");
    }
}

fn build_head_push_refspec(branch: &str) -> Option<String> {
    parse_head_push_refspec(branch)
        .map(|refspec| refspec.into_string())
        .ok()
}

#[cfg(test)]
mod tests {
    use super::build_head_push_refspec;

    #[test]
    fn build_head_push_refspec_accepts_simple_branch() {
        assert_eq!(
            build_head_push_refspec("main"),
            Some("HEAD:refs/heads/main".to_string())
        );
    }

    #[test]
    fn build_head_push_refspec_rejects_invalid_branch() {
        assert!(build_head_push_refspec(":evil").is_none());
    }

    #[test]
    fn build_head_push_refspec_keeps_explicit_ref_heads() {
        assert_eq!(
            build_head_push_refspec("refs/heads/feature"),
            Some("HEAD:refs/heads/feature".to_string())
        );
    }
}
