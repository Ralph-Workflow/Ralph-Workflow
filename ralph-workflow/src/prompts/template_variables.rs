//! Template variable generation from session capabilities.
//!
//! This module provides functions to generate template variables from
//! CapabilitySet and PolicyFlagSet for use in prompt template rendering.

use std::collections::HashMap;

use crate::agents::session::{Capability, CapabilitySet, PolicyFlag, PolicyFlagSet};

/// Bundled session capability parameters for prompt generation.
///
/// This newtype struct bundles `&CapabilitySet` and `&PolicyFlagSet` together
/// to reduce function argument counts and improve code organization.
///
/// # Example
///
/// ```
/// use ralph_workflow::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
/// use ralph_workflow::prompts::template_variables::SessionCapabilities;
///
/// let caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
/// let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Development);
/// let session_caps = SessionCapabilities::new(&caps, &flags);
/// ```
#[derive(Debug, Clone, Copy)]
pub struct SessionCapabilities<'a> {
    pub capabilities: &'a CapabilitySet,
    pub policy_flags: &'a PolicyFlagSet,
}

impl<'a> SessionCapabilities<'a> {
    /// Create a new SessionCapabilities from references to CapabilitySet and PolicyFlagSet.
    #[inline]
    #[must_use]
    pub fn new(capabilities: &'a CapabilitySet, policy_flags: &'a PolicyFlagSet) -> Self {
        Self {
            capabilities,
            policy_flags,
        }
    }

    /// Create SessionCapabilities from an AgentSession.
    #[inline]
    #[must_use]
    pub fn from_session(session: &'a crate::agents::session::AgentSession) -> Self {
        Self {
            capabilities: session.capabilities(),
            policy_flags: session.policy_flags(),
        }
    }

    /// Create default capabilities and policy flags for a drain.
    ///
    /// This returns owned values, not a SessionCapabilities, because
    /// SessionCapabilities holds references. Use this to get the defaults,
    /// then wrap in SessionCapabilities::new().
    ///
    /// # Example
    ///
    /// ```ignore
    /// let (caps, flags) = SessionCapabilities::from_drain(SessionDrain::Development);
    /// let session_caps = SessionCapabilities::new(&caps, &flags);
    /// ```
    #[inline]
    #[must_use]
    pub fn from_drain(
        drain: crate::agents::session::SessionDrain,
    ) -> (CapabilitySet, PolicyFlagSet) {
        (
            CapabilitySet::defaults_for_drain(drain),
            PolicyFlagSet::defaults_for_drain(drain),
        )
    }

    /// Destructure SessionCapabilities into its component parts.
    ///
    /// This is useful when you need to pass the inner references to functions
    /// that accept bare `&CapabilitySet` and `&PolicyFlagSet` parameters.
    #[inline]
    #[must_use]
    pub fn as_parts(&self) -> (&CapabilitySet, &PolicyFlagSet) {
        (self.capabilities, self.policy_flags)
    }
}

/// Helper to get default capabilities and policy flags for a drain as a tuple.
/// This is useful for creating SessionCapabilities in a single expression.
///
/// # Example
///
/// ```ignore
/// let session_caps = {
///     let (caps, flags) = default_caps_and_flags_for_drain(SessionDrain::Development);
///     SessionCapabilities::new(caps, flags)
/// };
/// ```
#[inline]
#[must_use]
pub fn default_caps_and_flags_for_drain(
    drain: crate::agents::session::SessionDrain,
) -> (CapabilitySet, PolicyFlagSet) {
    (
        CapabilitySet::defaults_for_drain(drain),
        PolicyFlagSet::defaults_for_drain(drain),
    )
}

/// Generate template variables from capabilities and policy flags.
///
/// These variables are used in templates to conditionally include
/// or exclude partials based on the session's granted capabilities.
///
/// # Template Variables Generated
///
/// | Variable | Source | Purpose |
/// | --- | --- | --- |
/// | `HAS_WORKSPACE_WRITE` | `capabilities.contains(WorkspaceWriteTracked)` | Controls write instructions |
/// | `HAS_PROCESS_EXEC` | `capabilities.contains(ProcessExecBounded)` | Controls shell instructions |
/// | `HAS_GIT_WRITE` | `capabilities.contains(GitWrite)` | Controls git write instructions |
/// | `POLICY_NO_EDIT` | `policy_flags.contains(NoEdit)` | Controls `_safety_no_execute` inclusion |
/// | `POLICY_ALLOW_SHELL` | `policy_flags.contains(AllowShell)` | Controls shell execution instructions |
/// | `POLICY_ALLOW_GIT_WRITE` | `policy_flags.contains(AllowGitWrite)` | Controls git write permission text |
/// | `CAPABILITY_SUMMARY` | Formatted list | Human-readable capability block |
/// | `MCP_TOOLS_LIST` | Comma-separated MCP tool names | Available MCP tools for RFC-009 |
/// | `HAS_MCP_WRITE` | `capabilities.contains(WorkspaceWriteTracked|WorkspaceWriteEphemeral)` | MCP write tools available |
/// | `HAS_MCP_EXEC` | `capabilities.contains(ProcessExecBounded)` | MCP exec tool available |
/// | `HAS_MCP_GIT` | `capabilities.contains(GitStatusRead|GitDiffRead|GitWrite)` | MCP git tools available |
#[must_use]
pub fn capability_template_variables(
    capabilities: &CapabilitySet,
    policy_flags: &PolicyFlagSet,
) -> HashMap<String, String> {
    // Capability-based variables
    let capability_vars = [
        (
            "HAS_WORKSPACE_WRITE".to_string(),
            bool_to_string(capabilities.contains(Capability::WorkspaceWriteTracked)),
        ),
        (
            "HAS_PROCESS_EXEC".to_string(),
            bool_to_string(capabilities.contains(Capability::ProcessExecBounded)),
        ),
        (
            "HAS_GIT_WRITE".to_string(),
            bool_to_string(capabilities.contains(Capability::GitWrite)),
        ),
    ];

    // Policy flag-based variables
    let policy_vars = [
        (
            "POLICY_NO_EDIT".to_string(),
            bool_to_string(policy_flags.contains(PolicyFlag::NoEdit)),
        ),
        (
            "POLICY_ALLOW_SHELL".to_string(),
            bool_to_string(policy_flags.contains(PolicyFlag::AllowShell)),
        ),
        (
            "POLICY_ALLOW_GIT_WRITE".to_string(),
            bool_to_string(policy_flags.contains(PolicyFlag::AllowGitWrite)),
        ),
    ];

    // MCP tool variables for RFC-009
    // HAS_MCP_WRITE is true only when WorkspaceWriteTracked is present.
    // WorkspaceWriteEphemeral allows writing to .agent/ but not tracked source files,
    // so it doesn't enable write_file in the MCP tools list.
    let has_mcp_write = capabilities.contains(Capability::WorkspaceWriteTracked);
    let has_mcp_exec = capabilities.contains(Capability::ProcessExecBounded);
    let has_mcp_git = capabilities.contains(Capability::GitStatusRead)
        || capabilities.contains(Capability::GitDiffRead)
        || capabilities.contains(Capability::GitWrite);

    let mcp_tools_list = format_mcp_tools_list(capabilities);
    let mcp_vars = [
        ("MCP_TOOLS_LIST".to_string(), mcp_tools_list),
        ("HAS_MCP_WRITE".to_string(), bool_to_string(has_mcp_write)),
        ("HAS_MCP_EXEC".to_string(), bool_to_string(has_mcp_exec)),
        ("HAS_MCP_GIT".to_string(), bool_to_string(has_mcp_git)),
    ];

    // Capability summary for human-readable display
    let summary_var = (
        "CAPABILITY_SUMMARY".to_string(),
        format_capability_summary(capabilities, policy_flags),
    );

    // Build final map using from_iter with chained iterator (functional style, no mutation)
    HashMap::from_iter(
        capability_vars
            .into_iter()
            .chain(policy_vars)
            .chain(mcp_vars)
            .chain(std::iter::once(summary_var)),
    )
}

/// Generate template variables from an AgentSession.
///
/// This is a convenience wrapper around `capability_template_variables`
/// that extracts the capabilities and policy flags from the session.
/// It uses the actual session capabilities rather than drain defaults,
/// ensuring behavioral equivalence between prompt rendering and session invocation.
///
/// # Example
///
/// ```ignore
/// let vars = capability_template_variables_from_session(&session);
/// ```
#[must_use]
pub fn capability_template_variables_from_session(
    session: &crate::agents::session::AgentSession,
) -> HashMap<String, String> {
    capability_template_variables(session.capabilities(), session.policy_flags())
}

/// Convert a boolean to a template-friendly string value.
///
/// Template conditionals evaluate a variable as truthy if it exists
/// and is non-empty. Using "true"/"false" strings allows templates
/// to use `{% if HAS_FEATURE %}...{% endif %}` patterns.
fn bool_to_string(value: bool) -> String {
    if value {
        "true".to_string()
    } else {
        String::new()
    }
}

/// Format a human-readable capability summary for display in prompts.
fn format_capability_summary(capabilities: &CapabilitySet, policy_flags: &PolicyFlagSet) -> String {
    let cap_list: Vec<String> = capabilities
        .iter()
        .map(|cap| format!("  - {}", cap.identifier()))
        .collect();

    let flag_list: Vec<String> = policy_flags
        .iter()
        .map(|flag| format!("  - {}", flag.identifier()))
        .collect();

    let caps_section = if cap_list.is_empty() {
        "  (none)".to_string()
    } else {
        cap_list.join("\n")
    };

    let flags_section = if flag_list.is_empty() {
        "  (none)".to_string()
    } else {
        flag_list.join("\n")
    };

    format!(
        "Capabilities:\n{}\n\nPolicy Flags:\n{}",
        caps_section, flags_section
    )
}

/// Format the list of available MCP tools based on session capabilities.
///
/// This generates the comma-separated list of MCP tool names that the
/// agent is allowed to use in this session, for display in the
/// `_mcp_tools.txt` partial.
fn format_mcp_tools_list(capabilities: &CapabilitySet) -> String {
    // Base tools always available
    let base_tools = vec![
        "read_file",
        "list_directory",
        "list_directory_recursive",
        "search_files",
        "ralph_submit_artifact",
        "report_progress",
        "read_env",
        "declare_complete",
        "coordinate",
    ];

    // Conditionally available tools built via functional chain
    let git_read_tools: Vec<&str> = if capabilities.contains(Capability::GitStatusRead) {
        vec!["git_status", "git_log", "git_show"]
    } else {
        vec![]
    };
    let git_diff_tool: Vec<&str> = if capabilities.contains(Capability::GitDiffRead) {
        vec!["git_diff"]
    } else {
        vec![]
    };
    let write_tool: Vec<&str> = if capabilities.contains(Capability::WorkspaceWriteTracked) {
        vec!["write_file"]
    } else {
        vec![]
    };
    let git_write_tool: Vec<&str> = if capabilities.contains(Capability::GitWrite) {
        vec!["ralph_git_commit"]
    } else {
        vec![]
    };
    let exec_tool: Vec<&str> = if capabilities.contains(Capability::ProcessExecBounded) {
        vec!["exec"]
    } else {
        vec![]
    };

    let all_tools: Vec<&str> = base_tools
        .into_iter()
        .chain(git_read_tools)
        .chain(git_diff_tool)
        .chain(write_tool)
        .chain(git_write_tool)
        .chain(exec_tool)
        .collect();

    all_tools.join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::{AgentSession, Capability, SessionDrain};

    #[test]
    fn test_capability_variables_for_planning_session() {
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Planning);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Planning);

        let vars = capability_template_variables(&caps, &flags);

        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "true");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "");
        assert!(vars
            .get("CAPABILITY_SUMMARY")
            .unwrap()
            .contains("workspace.read"));
        assert!(vars.get("CAPABILITY_SUMMARY").unwrap().contains("no_edit"));
    }

    #[test]
    fn test_capability_variables_for_development_session() {
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Development);

        let vars = capability_template_variables(&caps, &flags);

        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "true");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "true");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "true");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "");
    }

    #[test]
    fn test_capability_variables_for_commit_session() {
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Commit);

        let vars = capability_template_variables(&caps, &flags);

        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "true");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "true");
    }

    #[test]
    fn test_capability_summary_contains_all_granted_capabilities() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::WorkspaceRead);
        caps.insert(Capability::ProcessExecBounded);

        let flags = PolicyFlagSet::new();
        let vars = capability_template_variables(&caps, &flags);

        let summary = vars.get("CAPABILITY_SUMMARY").unwrap();
        assert!(summary.contains("workspace.read"));
        assert!(summary.contains("process.exec_bounded"));
    }

    #[test]
    fn test_empty_capabilities_shows_none() {
        let caps = CapabilitySet::new();
        let flags = PolicyFlagSet::new();

        let vars = capability_template_variables(&caps, &flags);

        let summary = vars.get("CAPABILITY_SUMMARY").unwrap();
        assert!(summary.contains("(none)"));
    }

    #[test]
    fn test_bool_to_string_true() {
        assert_eq!(bool_to_string(true), "true");
    }

    #[test]
    fn test_bool_to_string_false() {
        assert_eq!(bool_to_string(false), "");
    }

    #[test]
    fn test_capability_template_variables_from_session_development() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 0);
        let vars = capability_template_variables_from_session(&session);

        // Should match what defaults_for_drain produces for Development
        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "true");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "true");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "true");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "");
    }

    #[test]
    fn test_capability_template_variables_from_session_planning() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 0);
        let vars = capability_template_variables_from_session(&session);

        // Should match what defaults_for_drain produces for Planning
        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "true");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "");
    }

    #[test]
    fn test_capability_template_variables_from_session_commit() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Commit, 0);
        let vars = capability_template_variables_from_session(&session);

        // Should match what defaults_for_drain produces for Commit
        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "true");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "true");
    }

    #[test]
    fn test_capability_template_variables_from_session_review() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Review, 0);
        let vars = capability_template_variables_from_session(&session);

        // Should match what defaults_for_drain produces for Review
        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "true");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "");
    }

    #[test]
    fn test_capability_template_variables_from_session_fix() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Fix, 0);
        let vars = capability_template_variables_from_session(&session);

        // Should match what defaults_for_drain produces for Fix
        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "true");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "true");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "true");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "");
    }

    #[test]
    fn test_capability_template_variables_from_session_analysis() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Analysis, 0);
        let vars = capability_template_variables_from_session(&session);

        // Should match what defaults_for_drain produces for Analysis
        assert_eq!(vars.get("HAS_WORKSPACE_WRITE").unwrap(), "");
        assert_eq!(vars.get("HAS_PROCESS_EXEC").unwrap(), "");
        assert_eq!(vars.get("HAS_GIT_WRITE").unwrap(), "");
        assert_eq!(vars.get("POLICY_NO_EDIT").unwrap(), "true");
        assert_eq!(vars.get("POLICY_ALLOW_SHELL").unwrap(), "");
        assert_eq!(vars.get("POLICY_ALLOW_GIT_WRITE").unwrap(), "");
    }

    #[test]
    fn test_capability_template_variables_from_session_matches_direct_for_all_drains() {
        // Verifies that the session wrapper produces identical output to direct call
        // for ALL 6 drain types
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Development,
            SessionDrain::Analysis,
            SessionDrain::Review,
            SessionDrain::Fix,
            SessionDrain::Commit,
        ] {
            let session = AgentSession::for_drain("test-run".to_string(), drain, 0);

            let from_session = capability_template_variables_from_session(&session);
            let direct =
                capability_template_variables(session.capabilities(), session.policy_flags());

            assert_eq!(
                from_session, direct,
                "Session wrapper should produce identical output to direct call for {:?}",
                drain
            );
        }
    }

    #[test]
    fn test_mcp_tools_list_for_development_session() {
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Development);
        let vars = capability_template_variables(&caps, &flags);

        let mcp_list = vars.get("MCP_TOOLS_LIST").unwrap();
        // Development has write, exec, git read
        assert!(mcp_list.contains("read_file"));
        assert!(mcp_list.contains("write_file"));
        assert!(mcp_list.contains("exec"));
        assert!(mcp_list.contains("git_status"));
        assert!(mcp_list.contains("git_diff"));
        // Commit-specific tools should not be present
        assert!(!mcp_list.contains("ralph_git_commit"));
    }

    #[test]
    fn test_mcp_tools_list_for_planning_session() {
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Planning);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Planning);
        let vars = capability_template_variables(&caps, &flags);

        let mcp_list = vars.get("MCP_TOOLS_LIST").unwrap();
        // Planning has read-only, git read, no write, no exec
        assert!(mcp_list.contains("read_file"));
        assert!(mcp_list.contains("git_status"));
        assert!(mcp_list.contains("git_diff"));
        assert!(mcp_list.contains("git_log"));
        assert!(mcp_list.contains("git_show"));
        // No write_file (only WorkspaceWriteEphemeral, not WorkspaceWriteTracked)
        assert!(!mcp_list.contains("write_file"));
        // No exec
        assert!(!mcp_list.contains("exec"));
        // No git commit (no GitWrite)
        assert!(!mcp_list.contains("ralph_git_commit"));
    }

    #[test]
    fn test_mcp_tools_list_for_commit_session() {
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Commit);
        let vars = capability_template_variables(&caps, &flags);

        let mcp_list = vars.get("MCP_TOOLS_LIST").unwrap();
        // Commit has git write, git read, no write_file, no exec
        assert!(mcp_list.contains("ralph_git_commit"));
        assert!(mcp_list.contains("git_status"));
        assert!(mcp_list.contains("git_diff"));
        assert!(mcp_list.contains("git_log"));
        assert!(mcp_list.contains("git_show"));
        // No write_file (only WorkspaceWriteEphemeral, not WorkspaceWriteTracked)
        assert!(!mcp_list.contains("write_file"));
        // No exec
        assert!(!mcp_list.contains("exec"));
    }

    #[test]
    fn test_has_mcp_write_for_different_drains() {
        // Development and Fix have WorkspaceWriteTracked
        for drain in [SessionDrain::Development, SessionDrain::Fix] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("HAS_MCP_WRITE").unwrap(),
                "true",
                " {:?} should have HAS_MCP_WRITE",
                drain
            );
        }

        // Planning, Review, Analysis, Commit only have WorkspaceWriteEphemeral (not WorkspaceWriteTracked)
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Review,
            SessionDrain::Analysis,
            SessionDrain::Commit,
        ] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("HAS_MCP_WRITE").unwrap(),
                "",
                " {:?} should NOT have HAS_MCP_WRITE",
                drain
            );
        }
    }

    #[test]
    fn test_has_mcp_exec_for_different_drains() {
        // Development and Fix have ProcessExecBounded
        for drain in [SessionDrain::Development, SessionDrain::Fix] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("HAS_MCP_EXEC").unwrap(),
                "true",
                " {:?} should have HAS_MCP_EXEC",
                drain
            );
        }

        // Planning, Review, Analysis, Commit don't have ProcessExecBounded
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Review,
            SessionDrain::Analysis,
            SessionDrain::Commit,
        ] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("HAS_MCP_EXEC").unwrap(),
                "",
                " {:?} should NOT have HAS_MCP_EXEC",
                drain
            );
        }
    }

    #[test]
    fn test_has_mcp_git_for_different_drains() {
        // All 6 drain types have git capabilities:
        // - Planning/Analysis/Review: GitStatusRead, GitDiffRead
        // - Development/Fix: GitStatusRead, GitDiffRead
        // - Commit: GitStatusRead, GitDiffRead, GitWrite
        for drain in [
            SessionDrain::Planning,
            SessionDrain::Analysis,
            SessionDrain::Review,
            SessionDrain::Development,
            SessionDrain::Fix,
            SessionDrain::Commit,
        ] {
            let caps = CapabilitySet::defaults_for_drain(drain);
            let flags = PolicyFlagSet::defaults_for_drain(drain);
            let vars = capability_template_variables(&caps, &flags);
            assert_eq!(
                vars.get("HAS_MCP_GIT").unwrap(),
                "true",
                " {:?} should have HAS_MCP_GIT",
                drain
            );
        }
    }

    #[test]
    fn test_format_mcp_tools_list_contains_all_required_tools() {
        let caps = CapabilitySet::defaults_for_drain(SessionDrain::Development);
        let flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Development);
        let vars = capability_template_variables(&caps, &flags);

        let mcp_list = vars.get("MCP_TOOLS_LIST").unwrap();
        // Base tools always present
        assert!(mcp_list.contains("ralph_submit_artifact"));
        assert!(mcp_list.contains("report_progress"));
        assert!(mcp_list.contains("declare_complete"));
        assert!(mcp_list.contains("read_env"));
        assert!(mcp_list.contains("coordinate"));
        // Development has tracked write and exec
        assert!(mcp_list.contains("write_file"));
        assert!(mcp_list.contains("exec"));
        // Development has git read but not git write
        assert!(mcp_list.contains("git_status"));
        assert!(mcp_list.contains("git_diff"));
        assert!(!mcp_list.contains("ralph_git_commit"));
    }
}
