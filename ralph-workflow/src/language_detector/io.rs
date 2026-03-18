//! I/O boundary for language detection.
//!
//! This module handles all filesystem operations for language detection.
//! The pure detection logic lives in the parent module.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::workspace::Workspace;

use super::signatures::SignatureFiles;

pub(super) struct DetectedFrameworks {
    pub(super) frameworks: Vec<String>,
    pub(super) test_frameworks: Vec<String>,
    pub(super) package_managers: Vec<String>,
}

impl DetectedFrameworks {
    pub(super) const fn new() -> Self {
        Self {
            frameworks: Vec::new(),
            test_frameworks: Vec::new(),
            package_managers: Vec::new(),
        }
    }

    pub(super) fn with_framework(mut self, framework: impl Into<String>) -> Self {
        let framework = framework.into();
        if !self.frameworks.iter().any(|v| v == &framework) {
            self.frameworks.push(framework);
        }
        self
    }

    pub(super) fn with_test_framework(mut self, framework: impl Into<String>) -> Self {
        let framework = framework.into();
        if !self.test_frameworks.iter().any(|v| v == &framework) {
            self.test_frameworks.push(framework);
        }
        self
    }

    pub(super) fn with_package_manager(mut self, manager: impl Into<String>) -> Self {
        let manager = manager.into();
        if !self.package_managers.iter().any(|v| v == &manager) {
            self.package_managers.push(manager);
        }
        self
    }

    pub(super) fn finish(self) -> (Vec<String>, Option<String>, Option<String>) {
        let combine = |items: &[String]| -> Option<String> {
            match items.len() {
                0 => None,
                1 => Some(items[0].clone()),
                _ => Some(items.iter().cloned().collect::<Vec<_>>().join(" + ")),
            }
        };

        (
            self.frameworks,
            combine(&self.test_frameworks),
            combine(&self.package_managers),
        )
    }
}

pub(super) fn read_signature_file_contents(
    workspace: &dyn Workspace,
    files: &[PathBuf],
) -> HashMap<PathBuf, String> {
    files
        .iter()
        .filter_map(|path| {
            workspace
                .read(path)
                .ok()
                .map(|content| (path.clone(), content))
        })
        .collect()
}

pub(super) fn collect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> SignatureFiles {
    use super::scanner::{should_skip_dir_name, MAX_SIGNATURE_SEARCH_DEPTH};

    let targets: std::collections::HashSet<&str> = [
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
        targets: &std::collections::HashSet<&str>,
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

    let by_name_lower: std::collections::HashMap<String, Vec<PathBuf>> = matched_files
        .into_iter()
        .fold(std::collections::HashMap::new(), |mut map, (path, name)| {
            map.entry(name).or_default().push(path);
            map
        });

    SignatureFiles { by_name_lower }
}

pub(super) fn count_extensions_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
) -> std::io::Result<std::collections::HashMap<String, usize>> {
    use super::scanner::should_skip_dir_name;

    const MAX_FILES_TO_SCAN: usize = 2000;

    fn scan_dir(
        workspace: &dyn Workspace,
        dir: &Path,
        files_scanned: usize,
    ) -> std::io::Result<std::collections::HashMap<String, usize>> {
        if files_scanned >= MAX_FILES_TO_SCAN {
            return Ok(std::collections::HashMap::new());
        }

        let entries = match workspace.read_dir(dir) {
            Ok(e) => e,
            Err(_) => return Ok(std::collections::HashMap::new()),
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
                std::collections::HashMap::new(),
                |counts, (entry, _file_name, _name_lower)| {
                    let path = entry.path();
                    if entry.is_dir() {
                        let inner = scan_dir(workspace, path, files_scanned)?;
                        let merged: std::collections::HashMap<String, usize> =
                            inner.into_iter().fold(counts, |acc, (k, v)| {
                                let existing = acc.get(&k).copied().unwrap_or(0);
                                acc.into_iter()
                                    .chain(std::iter::once((k, existing + v)))
                                    .collect()
                            });
                        return Ok(merged);
                    }

                    if entry.is_file() {
                        if let Some(ext) = path.extension() {
                            let ext_str = ext.to_string_lossy().to_lowercase();
                            let existing = counts.get(&ext_str).copied().unwrap_or(0);
                            let updated: std::collections::HashMap<String, usize> = counts
                                .into_iter()
                                .chain(std::iter::once((ext_str, existing + 1)))
                                .collect();
                            return Ok(updated);
                        }
                    }
                    Ok(counts)
                },
            )
    }

    scan_dir(workspace, relative_root, 0)
}

pub(super) fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
    primary_lang: &str,
) -> bool {
    use super::scanner::{should_skip_dir_name, MAX_SIGNATURE_SEARCH_DEPTH};

    const MAX_FILES_TO_SCAN: usize = 2000;

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

    fn search(
        workspace: &dyn Workspace,
        queue: Vec<(PathBuf, usize)>,
        scanned_files: usize,
        primary_lang: &str,
    ) -> bool {
        if scanned_files >= MAX_FILES_TO_SCAN {
            return false;
        }

        let Some((item, rest)) = queue.split_first() else {
            return false;
        };
        let (dir, depth) = item;

        let entries = match workspace.read_dir(dir) {
            Ok(e) => e,
            Err(_) => return search(workspace, rest.to_vec(), scanned_files, primary_lang),
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
                if *depth < MAX_SIGNATURE_SEARCH_DEPTH {
                    let path = entry.path().to_path_buf();
                    let new_queue = rest
                        .iter()
                        .cloned()
                        .chain(std::iter::once((path, depth + 1)))
                        .collect();
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

        search(workspace, rest.to_vec(), scanned_files, primary_lang)
    }

    let initial_queue = vec![(relative_root.to_path_buf(), 0)];
    search(workspace, initial_queue, 0, primary_lang)
}
