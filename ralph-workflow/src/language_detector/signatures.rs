//! Signature file detection for frameworks and package managers.
//!
//! Analyzes configuration files like Cargo.toml, package.json, etc.
//! to detect frameworks, test frameworks, and package managers.

use super::scanner::{should_skip_dir_name, MAX_SIGNATURE_SEARCH_DEPTH};
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

use crate::workspace::Workspace;

#[path = "signatures/detectors.rs"]
mod detectors;

/// Maximum number of files to scan
const MAX_FILES_TO_SCAN: usize = 2000;

/// Maximum number of signature files to collect (across all types).
const MAX_SIGNATURE_FILES: usize = 50;

/// Container for signature files found during scanning.
#[derive(Default)]
struct SignatureFiles {
    by_name_lower: HashMap<String, Vec<PathBuf>>,
}

/// Collect signature files using workspace.
fn collect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> SignatureFiles {
    let targets: HashSet<&str> = [
        "cargo.toml",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "pipfile",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "bun.lock",
        "gemfile",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "mix.exs",
        "pubspec.yaml",
    ]
    .into_iter()
    .collect();

    let mut result = SignatureFiles::default();
    let mut queue: VecDeque<(PathBuf, usize)> = VecDeque::new();
    queue.push_back((root.to_path_buf(), 0));

    let mut scanned_entries: usize = 0;
    let mut collected: usize = 0;

    while let Some((dir, depth)) = queue.pop_front() {
        if scanned_entries >= MAX_FILES_TO_SCAN || collected >= MAX_SIGNATURE_FILES {
            break;
        }

        let Ok(entries) = workspace.read_dir(&dir) else {
            continue;
        };

        for entry in entries {
            if scanned_entries >= MAX_FILES_TO_SCAN || collected >= MAX_SIGNATURE_FILES {
                break;
            }
            scanned_entries += 1;

            let path = entry.path().to_path_buf();
            let Some(name_os) = entry.file_name() else {
                continue;
            };
            let name = name_os.to_string_lossy().to_string();
            let name_lower = name.to_lowercase();

            if entry.is_dir() {
                if should_skip_dir_name(&name_lower) {
                    continue;
                }
                if depth < MAX_SIGNATURE_SEARCH_DEPTH {
                    queue.push_back((path, depth + 1));
                }
                continue;
            }

            if !entry.is_file() {
                continue;
            }

            // Check if this is a target signature file
            if targets.contains(name_lower.as_str()) {
                result
                    .by_name_lower
                    .entry(name_lower)
                    .or_default()
                    .push(path);
                collected += 1;
            }
        }
    }

    result
}

/// Detect signature files and return frameworks, test framework, package manager.
pub(super) fn detect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> (Vec<String>, Option<String>, Option<String>) {
    let signatures = collect_signature_files_with_workspace(workspace, root);
    let mut results = detectors::DetectionResults::new();

    detectors::detect_rust(workspace, &signatures, &mut results);
    detectors::detect_python(workspace, &signatures, &mut results);
    detectors::detect_javascript(workspace, &signatures, &mut results);
    detectors::detect_go(workspace, &signatures, &mut results);
    detectors::detect_ruby(workspace, &signatures, &mut results);
    detectors::detect_java(workspace, &signatures, &mut results);
    detectors::detect_php(workspace, &signatures, &mut results);
    detectors::detect_dotnet(&signatures, &mut results);
    detectors::detect_elixir(workspace, &signatures, &mut results);
    detectors::detect_dart(workspace, &signatures, &mut results);

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
