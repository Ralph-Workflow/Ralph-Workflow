//! File system scanning utilities.
//!
//! Functions for scanning directories and counting file extensions.

use std::collections::HashMap;
use std::io;
use std::path::Path;

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

/// Check if a file name matches test file patterns for a given language
fn is_test_file(file_name: &str, primary_lang: &str, path_components: &[String]) -> bool {
    match primary_lang {
        "Rust" => {
            if file_name == "tests.rs" || file_name.ends_with("_test.rs") {
                return true;
            }
            std::path::Path::new(file_name)
                .extension()
                .is_some_and(|ext| ext.eq_ignore_ascii_case("rs"))
                && path_components.windows(1).any(|w| w[0] == "tests")
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

/// Scan directory recursively using workspace and count file extensions
pub(super) fn count_extensions_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
) -> io::Result<HashMap<String, usize>> {
    fn scan_dir_workspace(
        workspace: &dyn Workspace,
        dir: &Path,
        counts: &mut HashMap<String, usize>,
        files_scanned: &mut usize,
    ) -> io::Result<()> {
        if *files_scanned >= MAX_FILES_TO_SCAN {
            return Ok(());
        }

        let Ok(entries) = workspace.read_dir(dir) else {
            return Ok(());
        };

        for entry in entries {
            if *files_scanned >= MAX_FILES_TO_SCAN {
                return Ok(());
            }

            let file_name = entry.file_name().map(|s| s.to_string_lossy().to_string());
            let Some(file_name_str) = file_name else {
                continue;
            };

            // Skip hidden directories and common non-source directories
            let name_lower = file_name_str.to_ascii_lowercase();
            if should_skip_dir_name(&name_lower) {
                continue;
            }

            let path = entry.path();
            if entry.is_dir() {
                scan_dir_workspace(workspace, path, counts, files_scanned)?;
            } else if entry.is_file() {
                *files_scanned = files_scanned.saturating_add(1);
                if let Some(ext) = path.extension() {
                    let ext_str = ext.to_string_lossy().to_lowercase();
                    let entry = counts.entry(ext_str).or_insert(0);
                    *entry = entry.saturating_add(1);
                }
            }
        }

        Ok(())
    }

    let mut counts: HashMap<String, usize> = HashMap::new();
    let mut files_scanned = 0;
    scan_dir_workspace(workspace, relative_root, &mut counts, &mut files_scanned)?;
    Ok(counts)
}

/// Detect if tests exist using workspace
pub(super) fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
    primary_lang: &str,
) -> bool {
    use std::collections::VecDeque;
    use std::path::PathBuf;

    let mut queue: VecDeque<(PathBuf, usize)> = VecDeque::new();
    queue.push_back((relative_root.to_path_buf(), 0));

    let mut scanned_files = 0usize;

    while let Some((dir, depth)) = queue.pop_front() {
        if scanned_files >= MAX_FILES_TO_SCAN {
            break;
        }
        let Ok(entries) = workspace.read_dir(&dir) else {
            continue;
        };

        for entry in entries {
            if scanned_files >= MAX_FILES_TO_SCAN {
                break;
            }
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
                // Common test directories (any language)
                if matches!(name_lower.as_str(), "tests" | "test" | "spec" | "__tests__") {
                    return true;
                }
                if depth < MAX_SIGNATURE_SEARCH_DEPTH {
                    queue.push_back((path, depth + 1));
                }
                continue;
            }

            if !entry.is_file() {
                continue;
            }
            scanned_files = scanned_files.saturating_add(1);

            let path_components: Vec<String> = path
                .components()
                .map(|c| c.as_os_str().to_string_lossy().to_lowercase())
                .collect();

            if is_test_file(&name_lower, primary_lang, &path_components) {
                return true;
            }
        }
    }

    false
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

        let counts = count_extensions_with_workspace(&workspace, Path::new("")).unwrap();

        assert_eq!(counts.get("rs"), Some(&3));
        assert_eq!(counts.get("toml"), Some(&1));
    }

    #[test]
    fn test_count_extensions_with_workspace_skips_hidden() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file(".git/config", "hidden")
            .with_file(".hidden/file.rs", "hidden");

        let counts = count_extensions_with_workspace(&workspace, Path::new("")).unwrap();

        // Should only count src/main.rs, not hidden files
        assert_eq!(counts.get("rs"), Some(&1));
    }

    #[test]
    fn test_count_extensions_with_workspace_skips_node_modules() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/index.js", "export default {}")
            .with_file("node_modules/lodash/index.js", "module.exports = {}")
            .with_file("node_modules/react/index.js", "module.exports = {}");

        let counts = count_extensions_with_workspace(&workspace, Path::new("")).unwrap();

        // Should only count src/index.js
        assert_eq!(counts.get("js"), Some(&1));
    }

    #[test]
    fn test_detect_tests_with_workspace_finds_test_dir() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("tests/integration.rs", "#[test] fn test() {}");

        let has_tests = detect_tests_with_workspace(&workspace, Path::new(""), "Rust");

        assert!(has_tests);
    }

    #[test]
    fn test_detect_tests_with_workspace_finds_test_files() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/foo_test.rs", "#[test] fn test() {}");

        let has_tests = detect_tests_with_workspace(&workspace, Path::new(""), "Rust");

        assert!(has_tests);
    }

    #[test]
    fn test_detect_tests_with_workspace_no_tests() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}")
            .with_file("src/lib.rs", "pub mod foo;");

        let has_tests = detect_tests_with_workspace(&workspace, Path::new(""), "Rust");

        assert!(!has_tests);
    }
}
