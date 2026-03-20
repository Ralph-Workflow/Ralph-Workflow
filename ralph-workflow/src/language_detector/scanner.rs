//! File system scanning utilities.
//!
//! Functions for scanning directories and counting file extensions.

/// Maximum directory depth to search for signature files
pub(super) const MAX_SIGNATURE_SEARCH_DEPTH: usize = 6;

/// Check if a directory name should be skipped during scanning.
pub(super) fn should_skip_dir_name(name: &str) -> bool {
    if name.starts_with('.') {
        return true;
    }
    matches!(
        name,
        "node_modules"
            | "target"
            | "dist"
            | "build"
            | "vendor"
            | "__pycache__"
            | "venv"
            | ".venv"
            | "env"
    )
}

pub(super) fn is_test_file_name(
    file_name: &str,
    primary_lang: &str,
    path_components: &[String],
) -> bool {
    match primary_lang {
        "Rust" => {
            file_name == "tests.rs"
                || file_name.ends_with("_test.rs")
                || (std::path::Path::new(file_name)
                    .extension()
                    .is_some_and(|ext| ext.eq_ignore_ascii_case("rs"))
                    && path_components.windows(1).any(|w| w[0] == "tests"))
        }
        "Python" => {
            let has_py_ext = std::path::Path::new(file_name)
                .extension()
                .is_some_and(|ext| ext.eq_ignore_ascii_case("py"));
            (file_name.starts_with("test_") && has_py_ext) || file_name.ends_with("_test.py")
        }
        "JavaScript" | "TypeScript" => {
            file_name.ends_with(".test.js")
                || file_name.ends_with(".spec.js")
                || file_name.ends_with(".test.ts")
                || file_name.ends_with(".spec.ts")
                || file_name.ends_with(".test.tsx")
                || file_name.ends_with(".spec.tsx")
        }
        "Go" => file_name.ends_with("_test.go"),
        "Java" => {
            path_components
                .windows(2)
                .any(|w| w[0] == "src" && w[1] == "test")
                || file_name.ends_with("test.java")
        }
        "Ruby" => file_name.ends_with("_spec.rb") || file_name.ends_with("_test.rb"),
        _ => file_name.contains("test") || file_name.contains("spec"),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum SearchResult {
    Found,
    Continue { new_queue: Vec<(PathBuf, usize)> },
    Done,
}

use std::path::PathBuf;

pub(super) fn advance_search(
    queue: &[(PathBuf, usize)],
    file_names: &[(PathBuf, String)],
    _scanned_files: usize,
    primary_lang: &str,
) -> SearchResult {
    let found = file_names.iter().any(|(path, name_lower)| {
        let path_components: Vec<String> = path
            .components()
            .map(|c| c.as_os_str().to_string_lossy().to_lowercase())
            .collect();

        if should_skip_dir_name(name_lower) {
            return false;
        }

        if path.is_dir() {
            matches!(name_lower.as_str(), "tests" | "test" | "spec" | "__tests__")
        } else {
            is_test_file_name(name_lower, primary_lang, &path_components)
        }
    });

    let new_queue: Vec<_> = file_names
        .iter()
        .filter(|(path, name_lower)| {
            path.is_dir()
                && !should_skip_dir_name(name_lower)
                && !matches!(name_lower.as_str(), "tests" | "test" | "spec" | "__tests__")
                && queue
                    .first()
                    .map_or(false, |(_, depth)| *depth < MAX_SIGNATURE_SEARCH_DEPTH)
        })
        .map(|(path, _)| {
            let depth = queue.first().map_or(0, |(_, d)| *d);
            (path.clone(), depth + 1)
        })
        .collect();

    if found {
        SearchResult::Found
    } else if new_queue.is_empty() {
        SearchResult::Done
    } else {
        SearchResult::Continue { new_queue }
    }
}

pub(super) const SIGNATURE_FILE_NAMES: &[&str] = &[
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
];

pub(super) fn is_signature_file(name: &str) -> bool {
    SIGNATURE_FILE_NAMES.contains(&name)
}

#[derive(Debug, Clone)]
pub(super) struct ScanItem {
    pub path: PathBuf,
    pub name: String,
}

#[derive(Debug, Clone)]
pub(super) enum ScanResult {
    Done {
        matched: Vec<(PathBuf, String)>,
    },
    Continue {
        matched: Vec<(PathBuf, String)>,
        next_items: Vec<ScanItem>,
    },
}

pub(super) fn advance_scan(items: &[ScanItem], depth: usize) -> ScanResult {
    if items.is_empty() {
        return ScanResult::Done {
            matched: Vec::new(),
        };
    }

    let matched: Vec<_> = items
        .iter()
        .filter(|item| !item.path.is_dir() && is_signature_file(&item.name))
        .map(|item| (item.path.clone(), item.name.clone()))
        .collect();

    let dirs: Vec<_> = items
        .iter()
        .filter(|item| {
            item.path.is_dir()
                && !should_skip_dir_name(&item.name)
                && depth < MAX_SIGNATURE_SEARCH_DEPTH
        })
        .cloned()
        .collect();

    if dirs.is_empty() {
        ScanResult::Done { matched }
    } else {
        ScanResult::Continue {
            matched,
            next_items: dirs,
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::language_detector::{count_extensions_with_workspace, detect_tests_with_workspace};
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_count_extensions_with_workspace() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/lib.rs", "pub mod foo;")
            .with_file("src/foo.rs", "pub fn foo() {}")
            .with_file("Cargo.toml", "[package]");

        let counts = count_extensions_with_workspace(&workspace, std::path::Path::new("")).unwrap();

        assert_eq!(counts.get("rs"), Some(&3));
        assert_eq!(counts.get("toml"), Some(&1));
    }

    #[test]
    fn test_count_extensions_with_workspace_skips_hidden() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file(".git/config", "hidden")
            .with_file(".hidden/file.rs", "hidden");

        let counts = count_extensions_with_workspace(&workspace, std::path::Path::new("")).unwrap();

        // Should only count src/main.rs, not hidden files
        assert_eq!(counts.get("rs"), Some(&1));
    }

    #[test]
    fn test_count_extensions_with_workspace_skips_node_modules() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/index.js", "export default {}")
            .with_file("node_modules/lodash/index.js", "module.exports = {}")
            .with_file("node_modules/react/index.js", "module.exports = {}");

        let counts = count_extensions_with_workspace(&workspace, std::path::Path::new("")).unwrap();

        // Should only count src/index.js
        assert_eq!(counts.get("js"), Some(&1));
    }

    #[test]
    fn test_detect_tests_with_workspace_finds_test_dir() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("tests/integration.rs", "#[test] fn test() {}");

        let has_tests = detect_tests_with_workspace(&workspace, std::path::Path::new(""), "Rust");

        assert!(has_tests);
    }

    #[test]
    fn test_detect_tests_with_workspace_finds_test_files() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/foo_test.rs", "#[test] fn test() {}");

        let has_tests = detect_tests_with_workspace(&workspace, std::path::Path::new(""), "Rust");

        assert!(has_tests);
    }

    #[test]
    fn test_detect_tests_with_workspace_no_tests() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/lib.rs", "pub mod foo;");

        let has_tests = detect_tests_with_workspace(&workspace, std::path::Path::new(""), "Rust");

        assert!(!has_tests);
    }
}
