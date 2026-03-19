//! I/O boundary for language detection.
//!
//! This module handles all filesystem operations for language detection.
//! The pure detection logic lives in the parent module.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::workspace::Workspace;

use super::scanner::{self, ScanItem, ScanResult, SearchResult};
use super::signatures::SignatureFiles;

pub(super) fn collect_signature_files_with_workspace(
    workspace: &dyn Workspace,
    root: &Path,
) -> SignatureFiles {
    fn scan(
        workspace: &dyn Workspace,
        items: Vec<ScanItem>,
        depth: usize,
    ) -> Vec<(PathBuf, String)> {
        match scanner::advance_scan(&items, depth) {
            ScanResult::Done { matched } => matched,
            ScanResult::Continue {
                matched,
                next_items,
            } => {
                let all_entries: Vec<ScanItem> = next_items
                    .into_iter()
                    .filter_map(|item| {
                        workspace.read_dir(&item.path).ok().map(|entries| {
                            entries
                                .into_iter()
                                .filter_map(|entry| {
                                    let path = entry.path();
                                    let name = entry.file_name()?.to_string_lossy().to_string();
                                    Some(ScanItem {
                                        path: path.to_path_buf(),
                                        name: name.to_lowercase(),
                                    })
                                })
                                .collect::<Vec<_>>()
                        })
                    })
                    .flatten()
                    .collect();
                let next_files = scan(workspace, all_entries, depth + 1);
                matched.into_iter().chain(next_files.into_iter()).collect()
            }
        }
    }

    let initial_items: Vec<ScanItem> = workspace
        .read_dir(root)
        .ok()
        .map(|entries| {
            entries
                .into_iter()
                .filter_map(|entry| {
                    let path = entry.path();
                    let name = entry.file_name()?.to_string_lossy().to_string();
                    Some(ScanItem {
                        path: path.to_path_buf(),
                        name: name.to_lowercase(),
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let matched_files = scan(workspace, initial_items, 0);

    let by_name_lower: HashMap<String, Vec<PathBuf>> =
        matched_files
            .into_iter()
            .fold(std::collections::HashMap::new(), |map, (path, name)| {
                let existing = map.get(&name).cloned().unwrap_or_default();
                let updated: Vec<PathBuf> =
                    existing.into_iter().chain(std::iter::once(path)).collect();
                map.into_iter()
                    .chain(std::iter::once((name, updated)))
                    .collect()
            });

    SignatureFiles { by_name_lower }
}

pub fn count_extensions_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
) -> std::io::Result<HashMap<String, usize>> {
    use super::scanner::should_skip_dir_name;

    const MAX_FILES_TO_SCAN: usize = 2000;

    fn should_process_entry(name_lower: &str) -> bool {
        !should_skip_dir_name(name_lower)
    }

    fn scan_dirs_loop(
        workspace: &dyn Workspace,
        queue: Vec<PathBuf>,
        counts: HashMap<String, usize>,
        files_scanned: usize,
    ) -> std::io::Result<HashMap<String, usize>> {
        if files_scanned >= MAX_FILES_TO_SCAN {
            return Ok(counts);
        }

        let Some((dir, rest)) = queue.split_first() else {
            return Ok(counts);
        };

        let entries = match workspace.read_dir(dir) {
            Ok(e) => e,
            Err(_) => return scan_dirs_loop(workspace, rest.to_vec(), counts, files_scanned),
        };

        let (new_counts, subdirs): (_, Vec<PathBuf>) =
            entries
                .into_iter()
                .fold((counts, Vec::new()), |(mut c, mut dirs), entry| {
                    let Some(file_name) = entry.file_name() else {
                        return (c, dirs);
                    };
                    let name_lower = file_name.to_string_lossy().to_lowercase();
                    if !should_process_entry(&name_lower) {
                        return (c, dirs);
                    }
                    let path = entry.path();
                    if path.is_dir() {
                        dirs.push(path);
                    } else if let Some(ext) = path.extension() {
                        let ext_str = ext.to_string_lossy().to_lowercase();
                        *c.entry(ext_str).or_insert(0) += 1;
                    }
                    (c, dirs)
                });

        let scanned = files_scanned + new_counts.values().sum::<usize>();
        let next_queue: Vec<_> = rest.iter().chain(subdirs.iter()).cloned().collect();
        scan_dirs_loop(workspace, next_queue, new_counts, scanned)
    }

    let initial_queue = vec![relative_root.to_path_buf()];
    scan_dirs_loop(workspace, initial_queue, HashMap::new(), 0)
}

pub fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
    primary_lang: &str,
) -> bool {
    use super::scanner::advance_search;

    const MAX_FILES_TO_SCAN: usize = 2000;

    fn search_dirs_loop(
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

        let (dir, _depth) = item;

        let entries = match workspace.read_dir(dir) {
            Ok(e) => e,
            Err(_) => {
                return search_dirs_loop(workspace, rest.to_vec(), scanned_files, primary_lang)
            }
        };

        let file_names: Vec<(PathBuf, String)> = entries
            .into_iter()
            .filter_map(|entry| {
                let name_os = entry.file_name()?;
                let name = name_os.to_string_lossy().to_string();
                Some((entry.path().to_path_buf(), name.to_lowercase()))
            })
            .collect();

        let result = advance_search(
            &queue,
            &file_names,
            scanned_files.saturating_add(file_names.len()),
            primary_lang,
        );

        match result {
            SearchResult::Found => true,
            SearchResult::Done => {
                if rest.is_empty() {
                    false
                } else {
                    search_dirs_loop(workspace, rest.to_vec(), scanned_files, primary_lang)
                }
            }
            SearchResult::Continue { new_queue } => {
                let combined: Vec<_> = if new_queue.is_empty() {
                    rest.to_vec()
                } else {
                    rest.iter().cloned().chain(new_queue).collect()
                };
                search_dirs_loop(workspace, combined, scanned_files, primary_lang)
            }
        }
    }

    let initial_queue = vec![(relative_root.to_path_buf(), 0)];
    search_dirs_loop(workspace, initial_queue, 0, primary_lang)
}
