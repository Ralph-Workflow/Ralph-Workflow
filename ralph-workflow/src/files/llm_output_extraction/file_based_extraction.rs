//! File-based XML extraction utilities.
//!
//! This module provides functions for extracting XML from designated file locations.
//! Agents write XML to `.agent/tmp/` files for faster iteration cycles.
//!
//! # Benefits
//!
//! - **Faster retries**: Agents can edit existing files instead of regenerating entire XML
//! - **Self-validation**: Agents can use `xmllint` to catch errors before submitting
//! - **Cleaner separation**: XML lives in files, not embedded in text responses
//! - **Better debugging**: XML files persist for inspection

use crate::workspace::Workspace;
use std::path::Path;

// Environment access in boundary module (io.rs stem → exempt from forbid_io_effects).
include!("file_based_extraction/io.rs");

/// XML file paths by phase.
pub mod paths {
    /// Path for planning phase XML output
    pub const PLAN_XML: &str = ".agent/tmp/plan.xml";
    /// Path for development result XML output
    pub const DEVELOPMENT_RESULT_XML: &str = ".agent/tmp/development_result.xml";
    /// Path for review issues XML output
    pub const ISSUES_XML: &str = ".agent/tmp/issues.xml";
    /// Path for fix result XML output
    pub const FIX_RESULT_XML: &str = ".agent/tmp/fix_result.xml";
    /// Path for commit message XML output
    pub const COMMIT_MESSAGE_XML: &str = ".agent/tmp/commit_message.xml";
}

/// Resolve a relative `.agent/tmp/` path to an absolute path.
///
/// This function is critical for AI agents with security sandboxes that reject
/// relative paths as "outside the working directory". By providing absolute paths,
/// we ensure agents can write to `.agent/tmp/` without security violations.
///
/// # Arguments
///
/// * `relative_path` - A relative path like `.agent/tmp/issues.xml`
///
/// # Returns
///
/// An absolute path if working directory can be determined, otherwise the original
/// relative path as a fallback.
///
/// # Example
///
/// ```ignore
/// // Working dir: /Users/user/project
/// let path = resolve_absolute_path(".agent/tmp/issues.xml");
/// // Returns: "/Users/user/project/.agent/tmp/issues.xml"
/// ```
///
/// **Note:** This function uses the current working directory for paths.
/// For explicit path control, use [`resolve_absolute_path_at`] instead.
#[must_use]
pub fn resolve_absolute_path(relative_path: &str) -> String {
    resolve_with_current_dir(relative_path)
}

/// Resolve a relative path to an absolute path at a specific repository root.
///
/// This is used to provide agents with absolute paths in prompts, ensuring
/// they write files to the correct locations.
///
/// # Arguments
///
/// * `repo_root` - Path to the repository root
/// * `relative_path` - The relative path to resolve (e.g., ".agent/tmp/issues.xml")
///
/// # Returns
///
/// The absolute path as a string.
///
/// # Example
///
/// ```ignore
/// // repo_root: /Users/user/project
/// let path = resolve_absolute_path_at(Path::new("/Users/user/project"), ".agent/tmp/issues.xml");
/// // Returns: "/Users/user/project/.agent/tmp/issues.xml"
/// ```
#[must_use]
pub fn resolve_absolute_path_at(repo_root: &std::path::Path, relative_path: &str) -> String {
    repo_root.join(relative_path).display().to_string()
}

/// Try to read XML from a designated file location using workspace abstraction.
///
/// This function uses the workspace abstraction for filesystem access,
/// enabling unit tests with `MemoryWorkspace` instead of real filesystem operations.
pub fn try_extract_from_file_with_workspace(
    workspace: &dyn Workspace,
    xml_path: &Path,
) -> Option<String> {
    if !workspace.exists(xml_path) {
        return None;
    }

    let content = workspace.read(xml_path).ok()?;
    let trimmed = content.trim();

    // Must be non-empty and look like XML
    if trimmed.is_empty() || !trimmed.starts_with('<') {
        return None;
    }

    Some(trimmed.to_string())
}

/// Check if a file contains valid XML output.
///
/// Returns `true` if the file exists, is non-empty, and starts with '<'.
/// This is a quick check without full XSD validation - full validation
/// happens later in the extraction flow.
///
/// # Arguments
///
/// * `workspace` - Workspace abstraction for filesystem access
/// * `xml_path` - Path to the XML file to check
///
/// # Returns
///
/// `true` if the file exists and appears to contain XML content,
/// `false` otherwise (missing, empty, or not XML).
///
/// # Example
///
/// ```ignore
/// let workspace = MemoryWorkspace::new_test()
///     .with_file(".agent/tmp/issues.xml", "<ralph-issues>...</ralph-issues>");
/// assert!(has_valid_xml_output(&workspace, Path::new(".agent/tmp/issues.xml")));
/// ```
pub fn has_valid_xml_output(workspace: &dyn Workspace, xml_path: &Path) -> bool {
    if !workspace.exists(xml_path) {
        return false;
    }

    workspace.read(xml_path).is_ok_and(|content| {
        let trimmed = content.trim();
        !trimmed.is_empty() && trimmed.starts_with('<')
    })
}

/// Archive an XML output file after successful processing.
///
/// Moves the XML file to a `.processed` suffix so it's preserved for debugging
/// but clearly marked as already processed. If a `.processed` file already exists,
/// it is overwritten.
///
/// `.processed` files are archives only and are never used as fallback inputs.
///
/// # Arguments
///
/// * `workspace` - Workspace abstraction for filesystem access
/// * `xml_path` - Path to the XML file to archive
pub fn archive_xml_file_with_workspace(workspace: &dyn Workspace, xml_path: &Path) {
    if workspace.exists(xml_path) {
        let processed_path = xml_path.with_extension("xml.processed");
        let _ = workspace.rename(xml_path, &processed_path);
    }
}

/// Archive JSON artifact files by renaming them to `.json.processed`.
///
/// Archives both the main artifact file (`{type}.json`) and the partial
/// artifact file (`{type}.partial.json`) if they exist.
pub fn archive_json_artifact_with_workspace(workspace: &dyn Workspace, artifact_type: &str) {
    let json_path = Path::new(".agent/tmp").join(format!("{artifact_type}.json"));
    if workspace.exists(&json_path) {
        let processed = json_path.with_extension("json.processed");
        let _ = workspace.rename(&json_path, &processed);
    }
    let partial_path = Path::new(".agent/tmp").join(format!("{artifact_type}.partial.json"));
    if workspace.exists(&partial_path) {
        let processed = partial_path.with_extension("partial.json.processed");
        let _ = workspace.rename(&partial_path, &processed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_try_extract_from_file_success() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "test.xml",
            "<ralph-plan><ralph-summary>Test</ralph-summary></ralph-plan>",
        );

        let result = try_extract_from_file_with_workspace(&workspace, Path::new("test.xml"));
        assert!(result.is_some());
        assert!(result.unwrap().contains("<ralph-plan>"));
    }

    #[test]
    fn test_try_extract_from_file_not_exists() {
        let workspace = MemoryWorkspace::new_test();

        let result =
            try_extract_from_file_with_workspace(&workspace, Path::new("nonexistent/file.xml"));
        assert!(result.is_none());
    }

    #[test]
    fn test_try_extract_from_file_empty() {
        let workspace = MemoryWorkspace::new_test().with_file("empty.xml", "");

        let result = try_extract_from_file_with_workspace(&workspace, Path::new("empty.xml"));
        assert!(result.is_none());
    }

    #[test]
    fn test_try_extract_from_file_whitespace_only() {
        let workspace = MemoryWorkspace::new_test().with_file("whitespace.xml", "   \n  \n  ");

        let result = try_extract_from_file_with_workspace(&workspace, Path::new("whitespace.xml"));
        assert!(result.is_none());
    }

    #[test]
    fn test_try_extract_from_file_not_xml() {
        let workspace =
            MemoryWorkspace::new_test().with_file("not_xml.txt", "This is plain text, not XML");

        let result = try_extract_from_file_with_workspace(&workspace, Path::new("not_xml.txt"));
        assert!(result.is_none());
    }

    #[test]
    fn test_try_extract_from_file_trims_whitespace() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("padded.xml", "  \n<ralph-plan>Test</ralph-plan>\n  ");

        let result = try_extract_from_file_with_workspace(&workspace, Path::new("padded.xml"));
        assert!(result.is_some());
        let xml = result.unwrap();
        assert!(xml.starts_with('<'));
        assert!(xml.ends_with('>'));
    }

    #[test]
    fn test_archive_xml_file_moves_file() {
        let workspace =
            MemoryWorkspace::new_test().with_file("to_archive.xml", "<test>content</test>");

        archive_xml_file_with_workspace(&workspace, Path::new("to_archive.xml"));

        assert!(!workspace.exists(Path::new("to_archive.xml")));
        assert!(workspace.exists(Path::new("to_archive.xml.processed")));
        assert_eq!(
            workspace
                .read(Path::new("to_archive.xml.processed"))
                .unwrap(),
            "<test>content</test>"
        );
    }

    #[test]
    fn test_archive_xml_file_handles_missing() {
        let workspace = MemoryWorkspace::new_test();

        // Should not panic
        archive_xml_file_with_workspace(&workspace, Path::new("nonexistent.xml"));
    }

    #[test]
    fn test_archive_xml_file_overwrites_existing_processed() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("test.xml.processed", "<old>data</old>")
            .with_file("test.xml", "<new>data</new>");

        archive_xml_file_with_workspace(&workspace, Path::new("test.xml"));

        assert!(!workspace.exists(Path::new("test.xml")));
        assert!(workspace.exists(Path::new("test.xml.processed")));
        assert_eq!(
            workspace.read(Path::new("test.xml.processed")).unwrap(),
            "<new>data</new>"
        );
    }

    #[test]
    fn test_paths_constants() {
        assert_eq!(paths::PLAN_XML, ".agent/tmp/plan.xml");
        assert_eq!(paths::ISSUES_XML, ".agent/tmp/issues.xml");
        assert_eq!(
            paths::DEVELOPMENT_RESULT_XML,
            ".agent/tmp/development_result.xml"
        );
        assert_eq!(paths::FIX_RESULT_XML, ".agent/tmp/fix_result.xml");
        assert_eq!(paths::COMMIT_MESSAGE_XML, ".agent/tmp/commit_message.xml");
    }

    #[test]
    fn test_resolve_absolute_path_with_cwd() {
        // When current_dir() succeeds, should return absolute path
        let result = resolve_absolute_path(".agent/tmp/issues.xml");

        // Should contain .agent/tmp/issues.xml at the end
        assert!(result.contains(".agent/tmp/issues.xml"));

        // Should be longer than the relative path (has directory prefix)
        assert!(result.len() > ".agent/tmp/issues.xml".len());

        // Should not start with a dot if we got the absolute path
        if std::env::current_dir().is_ok() {
            #[cfg(unix)]
            assert!(result.starts_with('/'));

            #[cfg(windows)]
            assert!(result.contains(":\\") || result.starts_with("\\\\"));
        }
    }

    #[test]
    fn test_resolve_absolute_path_fallback() {
        // Even if cwd fails, should return the input path as fallback
        let input = ".agent/tmp/test.xml";
        let result = resolve_absolute_path(input);

        // At minimum, should contain the input path
        assert!(result.contains("test.xml"));
    }

    #[test]
    fn test_resolve_absolute_path_xsd() {
        // Test with XSD file paths
        let result = resolve_absolute_path(".agent/tmp/issues.xsd");
        assert!(result.contains(".agent/tmp/issues.xsd"));
    }
}
