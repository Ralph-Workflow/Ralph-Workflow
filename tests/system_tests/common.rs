//! Common utilities for system tests
//!
//! This module provides shared utilities for system tests.

use std::sync::Arc;

/// Create a `MockProcessExecutor` configured for git command success.
///
/// This helper provides mock responses for common git commands used in
/// rebase and other git operations, preventing real git subprocess spawning.
///
/// # Note
///
/// System tests use real git operations via `git2` library, but still need
/// mock executors for Ralph's process execution layer which is separate
/// from direct `git2` calls.
pub(crate) fn mock_executor_for_git_success() -> Arc<dyn ralph_workflow::executor::ProcessExecutor>
{
    Arc::new(
        ralph_workflow::executor::MockProcessExecutor::new()
            // git status --porcelain (clean working tree)
            .with_output("git", "")
            // whoami - fallback for git identity
            .with_output("whoami", "testuser")
            // hostname - fallback for git identity email
            .with_output("hostname", "localhost")
            // cargo - build/test commands in rebase validation
            .with_output("cargo", "")
            // Agent commands also return success
            .with_agent_result(
                "claude",
                Ok(ralph_workflow::executor::AgentCommandResult::success()),
            )
            .with_agent_result(
                "codex",
                Ok(ralph_workflow::executor::AgentCommandResult::success()),
            )
            .with_agent_result(
                "opencode",
                Ok(ralph_workflow::executor::AgentCommandResult::success()),
            )
            .with_agent_result(
                "glm",
                Ok(ralph_workflow::executor::AgentCommandResult::success()),
            )
            .with_agent_result(
                "aider",
                Ok(ralph_workflow::executor::AgentCommandResult::success()),
            ),
    )
}
