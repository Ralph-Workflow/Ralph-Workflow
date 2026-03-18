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
    fn scan_dir(
        workspace: &dyn Workspace,
        dir: &Path,
        files_scanned: usize,
    ) -> io::Result<HashMap<String, usize>> {
        if files_scanned >= MAX_FILES_TO_SCAN {
            return Ok(HashMap::new());
        }

        let entries = match workspace.read_dir(dir) {
            Ok(e) => e,
            Err(_) => return Ok(HashMap::new()),
        };

        let entries_vec: Vec<_> = entries
            .into_iter()
            .take(MAX_FILES_TO_SCAN - files_scanned)
            .collect();

        entries_vec
            .into_iter()
            .filter_map(|entry| {
                let file_name = entry.file_name().map(|s| s.to_string_lossy().to_string())?;
                let name_lower = file_name.to_ascii_lowercase();
                if should_skip_dir_name(&name_lower) {
                    return None;
                }
                Some((entry, file_name, name_lower))
            })
            .try_fold(
                HashMap::new(),
                |counts, (entry, _file_name, _name_lower)| {
                    let path = entry.path();
                    if entry.is_dir() {
                        let inner = scan_dir(workspace, path, files_scanned)?;
                        let merged: HashMap<String, usize> = counts
                            .into_iter()
                            .chain(inner.into_iter())
                            .fold(HashMap::new(), |acc, (k, v)| {
                                acc.into_iter().chain(std::iter::once((k, v))).fold(
                                    HashMap::new(),
                                    |mut m, (key, val)| {
                                        *m.entry(key).or_insert(0) += val;
                                        m
                                    },
                                )
                            });
                        return Ok(merged);
                    }

                    if entry.is_file() {
                        if let Some(ext) = path.extension() {
                            let ext_str = ext.to_string_lossy().to_lowercase();
                            let updated: HashMap<String, usize> = counts
                                .into_iter()
                                .chain(std::iter::once((ext_str, 1)))
                                .fold(HashMap::new(), |mut m, (key, val)| {
                                    *m.entry(key).or_insert(0) += val;
                                    m
                                });
                            return Ok(updated);
                        }
                    }
                    Ok(counts)
                },
            )
    }

    scan_dir(workspace, relative_root, 0)
}

/// Detect if tests exist using workspace
pub(super) fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
    primary_lang: &str,
) -> bool {
    use std::collections::VecDeque;
    use std::path::PathBuf;

    fn search(
        workspace: &dyn Workspace,
        mut queue: VecDeque<(PathBuf, usize)>,
        scanned_files: usize,
        primary_lang: &str,
    ) -> bool {
        if scanned_files >= MAX_FILES_TO_SCAN {
            return false;
        }

        let Some((dir, depth)) = queue.pop_front() else {
            return false;
        };

        let entries = match workspace.read_dir(&dir) {
            Ok(e) => e,
            Err(_) => return search(workspace, queue, scanned_files, primary_lang),
        };

        let found = entries.into_iter().any(|entry| {
            let Some(name_os) = entry.file_name() else {
                return false;
            };
            let name = name_os.to_string_lossy().to_string();
            let name_lower = name.to_lowercase();

            if entry.is_dir() {
                if should_skip_dir_name(&name_lower) {
                    return false;
                }
                if matches!(name_lower.as_str(), "tests" | "test" | "spec" | "__tests__") {
                    return true;
                }
                if depth < MAX_SIGNATURE_SEARCH_DEPTH {
                    let path = entry.path().to_path_buf();
                    let mut new_queue = queue.clone();
                    new_queue.push_back((path, depth + 1));
                    return search(workspace, new_queue, scanned_files, primary_lang);
                }
                return false;
            }

            if !entry.is_file() {
                return false;
            }

            let new_scanned = scanned_files.saturating_add(1);
            if new_scanned >= MAX_FILES_TO_SCAN {
                return false;
            }

            let path_components: Vec<String> = entry
                .path()
                .components()
                .map(|c| c.as_os_str().to_string_lossy().to_lowercase())
                .collect();

            is_test_file(&name_lower, primary_lang, &path_components)
        });

        if found {
            return true;
        }

        search(workspace, queue, scanned_files, primary_lang)
    }

    let initial_queue = VecDeque::from([(relative_root.to_path_buf(), 0)]);
    search(workspace, initial_queue, 0, primary_lang)
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
