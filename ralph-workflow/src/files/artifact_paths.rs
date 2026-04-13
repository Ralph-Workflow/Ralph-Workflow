//! Artifact file path constants and completion-detection utilities.
//!
//! This module centralises the `.agent/tmp/` path constants used across the
//! pipeline for MCP JSON artifact files. It also provides a lightweight
//! completion-detection helper used by the fault-tolerant executor.

use crate::workspace::Workspace;
use std::path::Path;

// ── JSON artifact paths (MCP submission path) ───────────────────────────────

/// Path for planning phase JSON output
pub const PLAN_JSON: &str = ".agent/tmp/plan.json";
/// Path for development result JSON output
pub const DEVELOPMENT_RESULT_JSON: &str = ".agent/tmp/development_result.json";
/// Path for review issues JSON output
pub const ISSUES_JSON: &str = ".agent/tmp/issues.json";
/// Path for fix result JSON output
pub const FIX_RESULT_JSON: &str = ".agent/tmp/fix_result.json";
/// Path for commit message JSON output
pub const COMMIT_MESSAGE_JSON: &str = ".agent/tmp/commit_message.json";

// ── XML artifact paths (legacy — kept for completion-detection heuristics) ──

/// Path for planning phase XML output (legacy)
pub const PLAN_XML: &str = ".agent/tmp/plan.xml";
/// Path for development result XML output (legacy)
pub const DEVELOPMENT_RESULT_XML: &str = ".agent/tmp/development_result.xml";
/// Path for review issues XML output (legacy)
pub const ISSUES_XML: &str = ".agent/tmp/issues.xml";
/// Path for fix result XML output (legacy)
pub const FIX_RESULT_XML: &str = ".agent/tmp/fix_result.xml";
/// Path for commit message XML output (legacy)
pub const COMMIT_MESSAGE_XML: &str = ".agent/tmp/commit_message.xml";

// ── Completion detection ─────────────────────────────────────────────────────

/// Check whether an agent has produced output at the given path.
///
/// Returns `true` if the file exists and is non-empty. Used by the
/// fault-tolerant executor as an error-recovery heuristic: if an agent
/// crashes after writing its artifact, the executor treats the run as
/// successful rather than triggering a retry.
///
/// Unlike the old `has_valid_xml_output`, this function is format-agnostic —
/// it accepts both JSON (MCP) and XML (legacy) artifacts.
pub fn has_valid_artifact_output(workspace: &dyn Workspace, path: &Path) -> bool {
    if !workspace.exists(path) {
        return false;
    }
    workspace
        .read(path)
        .is_ok_and(|content| !content.trim().is_empty())
}

/// Archive an XML output file after successful processing.
///
/// Renames the XML file to `.xml.processed` so it is preserved for debugging
/// but clearly marked as already processed. If a `.processed` file already
/// exists it is overwritten. A no-op when the file is absent.
pub fn archive_xml_file_with_workspace(workspace: &dyn Workspace, xml_path: &Path) {
    if workspace.exists(xml_path) {
        let processed_path = xml_path.with_extension("xml.processed");
        let _ = workspace.rename(xml_path, &processed_path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn has_valid_artifact_output_present_nonempty() {
        let ws = MemoryWorkspace::new_test()
            .with_file(".agent/tmp/plan.json", r#"{"type":"plan"}"#);
        assert!(has_valid_artifact_output(
            &ws,
            Path::new(PLAN_JSON)
        ));
    }

    #[test]
    fn has_valid_artifact_output_absent() {
        let ws = MemoryWorkspace::new_test();
        assert!(!has_valid_artifact_output(&ws, Path::new(PLAN_JSON)));
    }

    #[test]
    fn has_valid_artifact_output_empty_file() {
        let ws = MemoryWorkspace::new_test().with_file(PLAN_JSON, "");
        assert!(!has_valid_artifact_output(&ws, Path::new(PLAN_JSON)));
    }

    #[test]
    fn has_valid_artifact_output_whitespace_only() {
        let ws = MemoryWorkspace::new_test().with_file(PLAN_JSON, "   \n  ");
        assert!(!has_valid_artifact_output(&ws, Path::new(PLAN_JSON)));
    }

    #[test]
    fn archive_xml_file_moves_to_processed() {
        let ws =
            MemoryWorkspace::new_test().with_file(PLAN_XML, "<ralph-plan/>");
        archive_xml_file_with_workspace(&ws, Path::new(PLAN_XML));
        assert!(!ws.exists(Path::new(PLAN_XML)));
        assert!(ws.exists(Path::new(".agent/tmp/plan.xml.processed")));
    }

    #[test]
    fn archive_xml_file_noop_when_absent() {
        let ws = MemoryWorkspace::new_test();
        // Must not panic
        archive_xml_file_with_workspace(&ws, Path::new(PLAN_XML));
    }
}
