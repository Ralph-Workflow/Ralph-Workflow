//! Commit message generation handler.
//!
//! This module handles the commit phase of the pipeline, which generates
//! commit messages from git diffs using LLM agents.
//!
//! ## Architecture
//!
//! The commit handler follows the reducer architecture pattern:
//! - **Pure orchestration** - Reducers decide when to commit based on state
//! - **Single-attempt effects** - Handlers execute one commit generation attempt
//! - **Fact-shaped events** - Events report outcomes (`CommitGenerated`, `ValidationFailed`)
//! - **Workspace abstraction** - All filesystem I/O goes through `ctx.workspace`
//!
//! ## Process Flow
//!
//! 1. **Input materialization** - Load and materialize git diff
//! 2. **Prompt preparation** - Generate commit message prompt from diff
//! 3. **Agent invocation** - Invoke LLM agent with commit prompt
//! 4. **XML extraction** - Extract commit message from agent output
//! 5. **Validation** - Validate XML structure and content
//! 6. **Execution** - Create git commit with generated message
//!
//! ## Modules
//!
//! - [`inputs`] - Diff materialization and input preparation
//! - [`prompts`] - Commit prompt generation and template handling
//! - [`agent`] - Agent invocation for commit message generation
//! - [`xml`] - XML cleanup, extraction, and archiving
//! - [`validation`] - XML validation and outcome application
//! - [`execution`] - Git commit creation and skipping
//!
//! ## See Also
//!
//! - [`crate::phases::commit`] - Commit phase configuration
//! - [`crate::prompts`] - Prompt template system
//! - [`crate::files::llm_output_extraction`] - XML extraction utilities

mod agent;
mod execution;
mod inputs;
mod prompts;
mod validation;
mod xml;

const COMMIT_XSD_ERROR_PATH: &str = ".agent/tmp/commit_xsd_error.txt";

/// Get the current commit attempt number from commit state.
pub(in crate::reducer::handler) const fn current_commit_attempt(
    commit: &crate::reducer::state::CommitState,
) -> u32 {
    use crate::reducer::state::CommitState;
    match commit {
        CommitState::Generating { attempt, .. } => *attempt,
        _ => 1,
    }
}
