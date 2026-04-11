use crate::agents::session::{Capability, CapabilitySet};
use itertools::Itertools;
use regex::Regex;

const CLAUDE_MCP_TOOL_PREFIX: &str = "mcp__ralph__";
const WORKSPACE_READ_TOOLS: &[&str] = &[
    "read_file",
    "list_directory",
    "list_directory_recursive",
    "search_files",
];
const GIT_STATUS_READ_TOOLS: &[&str] = &["git_status", "git_log", "git_show"];
const GIT_DIFF_READ_TOOLS: &[&str] = &["git_diff"];
const TRACKED_WRITE_TOOLS: &[&str] = &["write_file"];
const PROCESS_EXEC_TOOLS: &[&str] = &["exec"];
const ARTIFACT_TOOLS: &[&str] = &["ralph_submit_artifact", "declare_complete", "coordinate"];
const PROGRESS_TOOLS: &[&str] = &["report_progress"];
const ENV_READ_TOOLS: &[&str] = &["read_env"];

pub(crate) fn visible_mcp_tool_names(capabilities: &CapabilitySet) -> Vec<&'static str> {
    [
        (
            capabilities.contains(Capability::WorkspaceRead),
            WORKSPACE_READ_TOOLS,
        ),
        (
            capabilities.contains(Capability::GitStatusRead),
            GIT_STATUS_READ_TOOLS,
        ),
        (
            capabilities.contains(Capability::GitDiffRead),
            GIT_DIFF_READ_TOOLS,
        ),
        (
            capabilities.contains(Capability::WorkspaceWriteTracked),
            TRACKED_WRITE_TOOLS,
        ),
        (
            capabilities.contains(Capability::ProcessExecBounded),
            PROCESS_EXEC_TOOLS,
        ),
        (
            capabilities.contains(Capability::ArtifactSubmit),
            ARTIFACT_TOOLS,
        ),
        (
            capabilities.contains(Capability::RunReportProgress),
            PROGRESS_TOOLS,
        ),
        (capabilities.contains(Capability::EnvRead), ENV_READ_TOOLS),
    ]
    .into_iter()
    .filter(|(enabled, _)| *enabled)
    .flat_map(|(_, tools)| tools.iter().copied())
    .collect()
}

pub(crate) fn visible_mcp_tool_names_owned(capabilities: &CapabilitySet) -> Vec<String> {
    visible_mcp_tool_names(capabilities)
        .into_iter()
        .map(str::to_string)
        .collect()
}

pub(crate) fn rewrite_prompt_mcp_tool_names(
    prompt: &str,
    capabilities: &CapabilitySet,
    use_claude_prefixed_names: bool,
) -> String {
    if !use_claude_prefixed_names {
        return prompt.to_string();
    }

    visible_mcp_tool_names(capabilities)
        .into_iter()
        .sorted_by_key(|tool_name| std::cmp::Reverse(tool_name.len()))
        .fold(prompt.to_string(), |acc, tool_name| {
            let pattern = Regex::new(&format!(r"\b{}\b", regex::escape(tool_name)))
                .expect("tool name regex should compile");
            pattern
                .replace_all(&acc, format!("{CLAUDE_MCP_TOOL_PREFIX}{tool_name}"))
                .into_owned()
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::SessionDrain;

    #[test]
    fn planning_manifest_excludes_progress_and_env_tools() {
        let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Planning);
        let manifest = visible_mcp_tool_names(&capabilities);

        assert!(manifest.contains(&"ralph_submit_artifact"));
        assert!(!manifest.contains(&"report_progress"));
        assert!(!manifest.contains(&"read_env"));
        assert!(!manifest.contains(&"write_file"));
        assert!(!manifest.contains(&"exec"));
    }

    #[test]
    fn commit_manifest_excludes_nonexistent_git_commit_and_env_tools() {
        let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let manifest = visible_mcp_tool_names(&capabilities);

        assert!(!manifest.contains(&"ralph_git_commit"));
        assert!(!manifest.contains(&"read_env"));
        assert!(!manifest.contains(&"write_file"));
        assert!(!manifest.contains(&"exec"));
        assert!(manifest.contains(&"ralph_submit_artifact"));
    }

    #[test]
    fn claude_prompt_rewrite_prefixes_visible_mcp_tools() {
        let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let prompt = "AVAILABLE TOOLS:\nread_file, ralph_submit_artifact, declare_complete\n\nUse `ralph_submit_artifact` to submit structured results. Use `declare_complete` when finished.";

        let rewritten = super::rewrite_prompt_mcp_tool_names(prompt, &capabilities, true);

        assert!(rewritten.contains("mcp__ralph__read_file"));
        assert!(rewritten.contains("mcp__ralph__ralph_submit_artifact"));
        assert!(rewritten.contains("mcp__ralph__declare_complete"));
        assert!(!rewritten.contains("`ralph_submit_artifact`"));
    }

    #[test]
    fn non_claude_prompt_rewrite_is_noop() {
        let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let prompt = "Use `ralph_submit_artifact` to submit structured results.";

        let rewritten = super::rewrite_prompt_mcp_tool_names(prompt, &capabilities, false);

        assert_eq!(rewritten, prompt);
    }

    #[test]
    fn claude_prompt_rewrite_still_rewrites_when_diff_mentions_prefixed_tools() {
        let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Commit);
        let prompt = "AVAILABLE TOOLS:\nread_file, ralph_submit_artifact\n\nUse `ralph_submit_artifact` to submit structured results.\n\nDIFF:\n+ assert!(allow.contains(\"mcp__ralph__ralph_submit_artifact\"));";

        let rewritten = super::rewrite_prompt_mcp_tool_names(prompt, &capabilities, true);

        assert!(rewritten
            .contains("Use `mcp__ralph__ralph_submit_artifact` to submit structured results."));
        assert!(
            rewritten.contains("assert!(allow.contains(\"mcp__ralph__ralph_submit_artifact\"));")
        );
    }
}
