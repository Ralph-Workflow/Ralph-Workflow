//! Signature file detection for frameworks and package managers.
//!
//! Analyzes configuration files like Cargo.toml, package.json, etc.
//! to detect frameworks, test frameworks, and package managers.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::workspace::Workspace;

#[path = "signatures/detectors.rs"]
mod detectors;

/// Container for signature files found during scanning.
#[derive(Default)]
pub(super) struct SignatureFiles {
    pub(super) by_name_lower: HashMap<String, Vec<PathBuf>>,
}

/// Collect signature files using workspace.
pub(super) fn collect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> SignatureFiles {
    super::collect_signature_files_with_workspace(workspace, root)
}

/// Detect signature files and return frameworks, test framework, package manager.
pub(super) fn detect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> (Vec<String>, Option<String>, Option<String>) {
    let signatures = collect_signature_files_with_workspace(workspace, root);

    let file_contents: std::collections::HashMap<PathBuf, String> = signatures
        .by_name_lower
        .values()
        .flatten()
        .filter_map(|path| {
            workspace
                .read(path)
                .ok()
                .map(|content| (path.clone(), content))
        })
        .collect();

    let results = detectors::DetectionResults::new();

    let results = detectors::detect_rust(&file_contents, &signatures, results);
    let results = detectors::detect_python(&file_contents, &signatures, results);
    let results = detectors::detect_javascript(&file_contents, &signatures, results);
    let results = detectors::detect_go(&file_contents, &signatures, results);
    let results = detectors::detect_ruby(&file_contents, &signatures, results);
    let results = detectors::detect_java(&file_contents, &signatures, results);
    let results = detectors::detect_php(&file_contents, &signatures, results);
    let results = detectors::detect_dotnet(&signatures, results);
    let results = detectors::detect_elixir(&file_contents, &signatures, results);
    let results = detectors::detect_dart(&file_contents, &signatures, results);

    results.finish()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_detect_rust() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "Cargo.toml",
            r#"
[package]
name = "test"
[dependencies]
axum = "0.7"
tokio = { version = "1", features = ["full"] }
[dev-dependencies]
"#,
        );

        let (frameworks, test_fw, pkg_mgr) =
            detect_signature_files_with_workspace(&workspace, Path::new(""));

        assert!(frameworks.contains(&"Axum".to_string()));
        assert!(frameworks.contains(&"Tokio".to_string()));
        assert_eq!(test_fw, Some("cargo test".to_string()));
        assert_eq!(pkg_mgr, Some("Cargo".to_string()));
    }

    #[test]
    fn test_detect_javascript() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "package.json",
            r#"
{
  "dependencies": { "react": "^18.0.0", "next": "^14.0.0" },
  "devDependencies": { "jest": "^29.0.0" }
}
"#,
        );

        let (frameworks, test_fw, pkg_mgr) =
            detect_signature_files_with_workspace(&workspace, Path::new(""));

        assert!(frameworks.contains(&"React".to_string()));
        assert!(frameworks.contains(&"Next.js".to_string()));
        assert_eq!(test_fw, Some("Jest".to_string()));
        assert_eq!(pkg_mgr, Some("npm".to_string()));
    }

    #[test]
    fn test_detect_python() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "pyproject.toml",
            r#"
[project]
name = "test"
dependencies = ["django", "pytest"]
"#,
        );

        let (frameworks, test_fw, pkg_mgr) =
            detect_signature_files_with_workspace(&workspace, Path::new(""));

        assert!(frameworks.contains(&"Django".to_string()));
        assert_eq!(test_fw, Some("pytest".to_string()));
        assert_eq!(pkg_mgr, Some("Poetry/pip".to_string()));
    }

    #[test]
    fn test_detect_go() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "go.mod",
            "module example.com/test\n\ngo 1.21\n\nrequire github.com/gin-gonic/gin v1.9.0\n",
        );

        let (frameworks, test_fw, pkg_mgr) =
            detect_signature_files_with_workspace(&workspace, Path::new(""));

        assert!(frameworks.contains(&"Gin".to_string()));
        assert_eq!(test_fw, Some("go test".to_string()));
        assert_eq!(pkg_mgr, Some("Go Modules".to_string()));
    }

    #[test]
    fn test_detect_ruby() {
        let workspace = MemoryWorkspace::new_test().with_file(
            "Gemfile",
            r"
source 'https://rubygems.org'
gem 'rails', '~> 7.0'
gem 'rspec-rails', group: :test
",
        );

        let (frameworks, test_fw, pkg_mgr) =
            detect_signature_files_with_workspace(&workspace, Path::new(""));

        assert!(frameworks.contains(&"Rails".to_string()));
        assert_eq!(test_fw, Some("RSpec".to_string()));
        assert_eq!(pkg_mgr, Some("Bundler".to_string()));
    }
}
