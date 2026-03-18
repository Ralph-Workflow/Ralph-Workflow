//! File system scanning utilities.
//!
//! Functions for scanning directories and counting file extensions.

use crate::workspace::Workspace;

/// Maximum number of files to scan (for performance on large repos)
const MAX_FILES_TO_SCAN: usize = 2000;

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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_count_extensions_with_workspace() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/lib.rs", "pub mod foo;")
            .with_file("src/foo.rs", "pub fn foo() {}")
            .with_file("Cargo.toml", "[package]");

        let counts =
            super::count_extensions_with_workspace(&workspace, std::path::Path::new("")).unwrap();

        assert_eq!(counts.get("rs"), Some(&3));
        assert_eq!(counts.get("toml"), Some(&1));
    }

    #[test]
    fn test_count_extensions_with_workspace_skips_hidden() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file(".git/config", "hidden")
            .with_file(".hidden/file.rs", "hidden");

        let counts =
            super::count_extensions_with_workspace(&workspace, std::path::Path::new("")).unwrap();

        // Should only count src/main.rs, not hidden files
        assert_eq!(counts.get("rs"), Some(&1));
    }

    #[test]
    fn test_count_extensions_with_workspace_skips_node_modules() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/index.js", "export default {}")
            .with_file("node_modules/lodash/index.js", "module.exports = {}")
            .with_file("node_modules/react/index.js", "module.exports = {}");

        let counts =
            super::count_extensions_with_workspace(&workspace, std::path::Path::new("")).unwrap();

        // Should only count src/index.js
        assert_eq!(counts.get("js"), Some(&1));
    }

    #[test]
    fn test_detect_tests_with_workspace_finds_test_dir() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("tests/integration.rs", "#[test] fn test() {}");

        let has_tests =
            super::detect_tests_with_workspace(&workspace, std::path::Path::new(""), "Rust");

        assert!(has_tests);
    }

    #[test]
    fn test_detect_tests_with_workspace_finds_test_files() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/foo_test.rs", "#[test] fn test() {}");

        let has_tests =
            super::detect_tests_with_workspace(&workspace, std::path::Path::new(""), "Rust");

        assert!(has_tests);
    }

    #[test]
    fn test_detect_tests_with_workspace_no_tests() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/lib.rs", "pub mod foo;");

        let has_tests =
            super::detect_tests_with_workspace(&workspace, std::path::Path::new(""), "Rust");

        assert!(!has_tests);
    }
}
