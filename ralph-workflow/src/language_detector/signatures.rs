//! Signature file detection for frameworks and package managers.
//!
//! Analyzes configuration files like Cargo.toml, package.json, etc.
//! to detect frameworks, test frameworks, and package managers.

use super::scanner::{should_skip_dir_name, MAX_SIGNATURE_SEARCH_DEPTH};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use crate::workspace::Workspace;

#[path = "signatures/detectors.rs"]
mod detectors;

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

    fn scan(
        workspace: &dyn Workspace,
        items: Vec<(PathBuf, String)>,
        depth: usize,
        targets: &HashSet<&str>,
    ) -> Vec<(PathBuf, String)> {
        if items.is_empty() {
            return Vec::new();
        }

        let (dirs, files): (Vec<_>, Vec<_>) = items.into_iter().partition(|(p, n)| {
            p.is_dir() && !should_skip_dir_name(n) && depth < MAX_SIGNATURE_SEARCH_DEPTH
        });

        let sig_files: Vec<_> = files
            .into_iter()
            .filter(|(_, n)| targets.contains(n.as_str()))
            .collect();

        if dirs.is_empty() {
            return sig_files;
        }

        let all_entries: Vec<(PathBuf, String)> = dirs
            .into_iter()
            .filter_map(|(p, _)| workspace.read_dir(&p).ok())
            .flat_map(|entries| {
                entries.into_iter().filter_map(|entry| {
                    let path = entry.path().to_path_buf();
                    let name = entry.file_name()?.to_string_lossy().to_string();
                    Some((path, name.to_lowercase()))
                })
            })
            .collect();

        let next_files = scan(workspace, all_entries, depth + 1, targets);
        sig_files
            .into_iter()
            .chain(next_files.into_iter())
            .collect()
    }

    let initial_items: Vec<(PathBuf, String)> = workspace
        .read_dir(root)
        .ok()
        .map(|entries| {
            entries
                .into_iter()
                .filter_map(|entry| {
                    let path = entry.path().to_path_buf();
                    let name = entry.file_name()?.to_string_lossy().to_string();
                    Some((path, name.to_lowercase()))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let matched_files = scan(workspace, initial_items, 0, &targets);

    fn build_map(files: Vec<(PathBuf, String)>) -> HashMap<String, Vec<PathBuf>> {
        files
            .into_iter()
            .fold(HashMap::new(), |mut map, (path, name)| {
                map.entry(name).or_insert_with(Vec::new).push(path);
                map
            })
    }

    let by_name_lower = build_map(matched_files);

    SignatureFiles { by_name_lower }
}

/// Detect signature files and return frameworks, test framework, package manager.
pub(super) fn detect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> (Vec<String>, Option<String>, Option<String>) {
    let signatures = collect_signature_files_with_workspace(workspace, root);
    let results = detectors::DetectionResults::new();

    let results = detectors::detect_rust(workspace, &signatures, results);
    let results = detectors::detect_python(workspace, &signatures, results);
    let results = detectors::detect_javascript(workspace, &signatures, results);
    let results = detectors::detect_go(workspace, &signatures, results);
    let results = detectors::detect_ruby(workspace, &signatures, results);
    let results = detectors::detect_java(workspace, &signatures, results);
    let results = detectors::detect_php(workspace, &signatures, results);
    let results = detectors::detect_dotnet(&signatures, results);
    let results = detectors::detect_elixir(workspace, &signatures, results);
    let results = detectors::detect_dart(workspace, &signatures, results);

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
