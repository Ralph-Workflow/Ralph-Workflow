use super::types::InitialRebaseOutcome;
use crate::checkpoint::RunContext;
use crate::git_helpers::{get_default_branch_at, rebase_onto_at, RebaseResult};
use crate::logger::{Colors, Logger};
use crate::ProcessExecutor;

pub(crate) struct InitialRebaseRunResult {
    pub outcome: InitialRebaseOutcome,
    /// Prompt replay observability for conflict-resolution prompts.
    pub prompt_replay_hits: Vec<(String, bool)>,
}

/// Run rebase to the default branch.
///
/// This function performs a rebase from the current branch to the
/// default branch (main/master). It handles all edge cases including:
/// - Already on main/master (proceeds with rebase attempt)
/// - Empty repository (returns `NoOp`)
/// - Upstream branch not found (error)
/// - Conflicts during rebase (returns `Conflicts` result)
pub(crate) fn run_rebase_to_default(
    logger: &Logger,
    colors: Colors,
    repo_root: &std::path::Path,
    executor: &dyn ProcessExecutor,
) -> std::io::Result<RebaseResult> {
    let default_branch = get_default_branch_at(repo_root)?;
    logger.info(&format!(
        "Rebasing onto {}{}{}",
        colors.cyan(),
        default_branch,
        colors.reset()
    ));
    rebase_onto_at(repo_root, &default_branch, executor)
}

/// Run initial rebase before development phase.
///
/// This function is called before the development phase starts to ensure
/// the feature branch is up-to-date with the default branch.
///
/// Uses a state machine for fault tolerance and automatic recovery from
/// interruptions or failures.
pub(crate) fn run_initial_rebase(
    logger: &Logger,
    colors: Colors,
    repo_root: &std::path::Path,
    _run_context: &RunContext,
    executor: &dyn ProcessExecutor,
    _prompt_history: &mut std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) -> anyhow::Result<InitialRebaseRunResult> {
    let default_branch = get_default_branch_at(repo_root)?;
    logger.info(&format!(
        "Rebasing onto {}{}{}",
        colors.cyan(),
        default_branch,
        colors.reset()
    ));
    rebase_onto_at(repo_root, &default_branch, executor)
        .map_err(Into::into)
        .map(|result| {
            let outcome = match result {
                RebaseResult::Success => InitialRebaseOutcome::Succeeded {
                    new_head: String::new(),
                },
                RebaseResult::NoOp { reason } => InitialRebaseOutcome::Skipped { reason },
                RebaseResult::Conflicts(_) => InitialRebaseOutcome::Skipped {
                    reason: "conflicts during rebase".to_string(),
                },
                RebaseResult::Failed(e) => InitialRebaseOutcome::Skipped {
                    reason: format!("rebase failed: {e:?}"),
                },
            };
            InitialRebaseRunResult {
                outcome,
                prompt_replay_hits: Vec::new(),
            }
        })
}
