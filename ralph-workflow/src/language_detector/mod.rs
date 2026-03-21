//! Language and Stack Detection Module
//!
//! Detects the primary technology stack of a repository by analyzing file extensions,
//! configuration files, and common patterns. This enables language-specific review
//! guidance without requiring an LLM.
//!
//! The detection is fast (< 100ms typically) and uses heuristics based on:
//! - File extension counts
//! - Signature files (Cargo.toml, package.json, etc.)
//! - Framework indicators in config files
//!
//! # Module Structure
//!
//! - `extensions` - Extension to language mapping
//! - `signatures` - Signature file detection for frameworks and package managers
//! - `scanner` - File system scanning utilities
//! - `io` - Boundary module for filesystem operations

#![deny(unsafe_code)]

mod extensions;
mod io;
mod scanner;
mod signatures;

use std::collections::BTreeMap;
use std::path::Path;

use itertools::Itertools;

use crate::workspace::Workspace;

pub use extensions::extension_to_language;
use extensions::is_non_primary_language;

/// Maximum number of secondary languages to include in the stack summary.
///
/// Polyglot repos commonly have more than 3 relevant languages (e.g. PHP + TS + JS + SQL),
/// but we still cap this to keep prompts/banners readable.
const MAX_SECONDARY_LANGUAGES: usize = 6;

/// Minimum file count to consider a language as present
const MIN_FILES_FOR_DETECTION: usize = 1;

/// Represents the detected technology stack of a project
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectStack {
    /// Primary programming language (most prevalent)
    pub(crate) primary_language: String,
    /// Secondary languages used in the project
    pub(crate) secondary_languages: Vec<String>,
    /// Detected frameworks (React, Django, Rails, etc.)
    pub(crate) frameworks: Vec<String>,
    /// Whether the project appears to have tests
    pub(crate) has_tests: bool,
    /// Detected test framework (if any)
    pub(crate) test_framework: Option<String>,
    /// Package manager detected
    pub(crate) package_manager: Option<String>,
}

impl Default for ProjectStack {
    fn default() -> Self {
        Self {
            primary_language: "Unknown".to_string(),
            secondary_languages: Vec::new(),
            frameworks: Vec::new(),
            has_tests: false,
            test_framework: None,
            package_manager: None,
        }
    }
}

impl ProjectStack {
    /// Returns true if the project uses Rust
    pub(crate) fn is_rust(&self) -> bool {
        self.primary_language == "Rust" || self.secondary_languages.iter().any(|l| l == "Rust")
    }

    /// Returns true if the project uses Python
    pub(crate) fn is_python(&self) -> bool {
        self.primary_language == "Python" || self.secondary_languages.iter().any(|l| l == "Python")
    }

    /// Returns true if the project uses JavaScript or TypeScript
    pub(crate) fn is_javascript_or_typescript(&self) -> bool {
        matches!(self.primary_language.as_str(), "JavaScript" | "TypeScript")
            || self
                .secondary_languages
                .iter()
                .any(|l| l == "JavaScript" || l == "TypeScript")
    }

    /// Returns true if the project uses Go
    pub(crate) fn is_go(&self) -> bool {
        self.primary_language == "Go" || self.secondary_languages.iter().any(|l| l == "Go")
    }

    /// Format as a summary string for display
    pub(crate) fn summary(&self) -> String {
        let secondary = (!self.secondary_languages.is_empty())
            .then_some(format!("(+{})", self.secondary_languages.join(", ")));
        let frameworks =
            (!self.frameworks.is_empty()).then_some(format!("[{}]", self.frameworks.join(", ")));
        let tests = self.has_tests.then_some(
            self.test_framework
                .as_ref()
                .map(|tf| format!("tests:{tf}"))
                .unwrap_or_else(|| "tests:yes".to_string()),
        );

        std::iter::once(self.primary_language.clone())
            .chain(secondary)
            .chain(frameworks)
            .chain(tests)
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Detect the project stack for a given repository root.
///
/// This is a convenience wrapper that creates a [`WorkspaceFs`] and calls
/// [`detect_stack_with_workspace`].
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn detect_stack(root: &Path) -> std::io::Result<ProjectStack> {
    use crate::workspace::WorkspaceFs;

    let workspace = WorkspaceFs::new(root.to_path_buf());
    detect_stack_with_workspace(&workspace, Path::new(""))
}

/// Detect stack and return a summary string (for display in banner)
#[must_use]
pub fn detect_stack_summary(root: &Path) -> String {
    detect_stack(root).map_or_else(|_| "Unknown".to_string(), |stack| stack.summary())
}

#[cfg(test)]
mod tests;

// =============================================================================
// Workspace-based variants
// =============================================================================

/// Detect project stack using workspace abstraction.
///
/// This is the testable version of [`detect_stack`] that uses workspace
/// for all filesystem operations.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn detect_stack_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> std::io::Result<ProjectStack> {
    let extension_counts = count_extensions_with_workspace(workspace, root)?;

    let mut language_counts = BTreeMap::new();
    for (lang, count) in extension_counts.iter().filter_map(|(ext, count)| {
        extension_to_language(ext).map(|lang| (lang.to_string(), *count))
    }) {
        *language_counts.entry(lang).or_insert(0) += count;
    }

    let language_vec: Vec<_> = language_counts
        .into_iter()
        .filter(|(_, count)| *count >= MIN_FILES_FOR_DETECTION)
        .map(|(lang, count)| (count, lang))
        .sorted_by(|a, b| b.0.cmp(&a.0))
        .map(|(count, lang)| (lang, count))
        .collect();

    let primary_language = language_vec
        .iter()
        .find(|(lang, _)| !is_non_primary_language(lang))
        .or_else(|| language_vec.first())
        .map_or_else(|| "Unknown".to_string(), |(lang, _)| (*lang).to_string());

    let secondary_languages: Vec<String> = language_vec
        .iter()
        .filter(|(lang, _)| *lang != primary_language.as_str())
        .take(MAX_SECONDARY_LANGUAGES)
        .map(|(lang, _)| (*lang).to_string())
        .collect();

    let (frameworks, test_framework, package_manager) =
        signatures::detect_signature_files_with_workspace(workspace, root);

    let has_tests =
        test_framework.is_some() || detect_tests_with_workspace(workspace, root, &primary_language);

    Ok(ProjectStack {
        primary_language,
        secondary_languages,
        frameworks,
        has_tests,
        test_framework,
        package_manager,
    })
}

pub fn count_extensions_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> std::io::Result<std::collections::HashMap<String, usize>> {
    io::count_extensions_with_workspace(workspace, root)
}

pub fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
    primary_lang: &str,
) -> bool {
    io::detect_tests_with_workspace(workspace, root, primary_lang)
}

fn collect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> signatures::SignatureFiles {
    io::collect_signature_files_with_workspace(workspace, root)
}

#[cfg(test)]
mod workspace_tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_detect_stack_with_workspace_rust_project() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(
                "Cargo.toml",
                r#"
[package]
name = "test"
[dependencies]
axum = "0.7"
[dev-dependencies]
"#,
            )
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/lib.rs", "pub mod foo;")
            .with_file("tests/integration.rs", "#[test] fn test() {}");

        let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();

        assert_eq!(stack.primary_language, "Rust");
        assert!(stack.frameworks.contains(&"Axum".to_string()));
        assert!(stack.has_tests);
        assert_eq!(stack.package_manager, Some("Cargo".to_string()));
    }

    #[test]
    fn test_detect_stack_with_workspace_js_project() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(
                "package.json",
                r#"
{
  "dependencies": { "react": "^18.0.0" },
  "devDependencies": { "jest": "^29.0.0" }
}

"#,
            )
            .with_file("src/index.js", "export default {}")
            .with_file("src/App.jsx", "export function App() {}")
            .with_file("src/utils.js", "export const foo = 1");

        let stack = detect_stack_with_workspace(&workspace, Path::new("")).unwrap();

        assert_eq!(stack.primary_language, "JavaScript");
        assert!(stack.frameworks.contains(&"React".to_string()));
        assert_eq!(stack.test_framework, Some("Jest".to_string()));
    }
}
