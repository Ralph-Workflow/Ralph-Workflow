//! Template variable generation from session capabilities.
//!
//! This module provides functions to generate template variables from
//! CapabilitySet and PolicyFlagSet for use in prompt template rendering.

use std::collections::HashMap;

use crate::agents::session::{Capability, CapabilitySet, PolicyFlag, PolicyFlagSet};

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
            .chain(std::iter::once(summary_var)),
    )
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::{Capability, SessionDrain};

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
}
