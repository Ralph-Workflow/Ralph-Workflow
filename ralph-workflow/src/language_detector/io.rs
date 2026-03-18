//! I/O boundary for language detection.
//!
//! This module handles all filesystem operations for language detection.
//! The pure detection logic lives in the parent module.

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

    let by_name_lower: std::collections::HashMap<String, Vec<PathBuf>> = matched_files
        .into_iter()
        .fold(std::collections::HashMap::new(), |map, (path, name)| {
            let existing = map.get(&name).cloned().unwrap_or_default();
            let updated: Vec<PathBuf> = existing.into_iter().chain(std::iter::once(path)).collect();
            map.into_iter()
                .chain(std::iter::once((name, updated)))
                .collect()
        });

    SignatureFiles { by_name_lower }
}

pub fn count_extensions_with_workspace(
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
            .take(MAX_FILES_TO_SCAN.saturating_sub(files_scanned))
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

pub fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
    primary_lang: &str,
) -> bool {
    use super::scanner::advance_search;

    const MAX_FILES_TO_SCAN: usize = 2000;

    fn next_search(
        queue: &[(PathBuf, usize)],
        new_queue: Vec<(PathBuf, usize)>,
    ) -> Option<Vec<(PathBuf, usize)>> {
        if new_queue.is_empty() {
            None
        } else {
            Some(queue.iter().cloned().chain(new_queue).collect())
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
        let (dir, _depth) = item;

        let entries = match workspace.read_dir(dir) {
            Ok(e) => e,
            Err(_) => return search(workspace, rest.to_vec(), scanned_files, primary_lang),
        };

        let file_names: Vec<(PathBuf, String)> = entries
            .into_iter()
            .filter_map(|entry| {
                let name_os = entry.file_name()?;
                let name = name_os.to_string_lossy().to_string();
                Some((entry.path().to_path_buf(), name.to_lowercase()))
            })
            .collect();

        match advance_search(
            &queue,
            &file_names,
            scanned_files.saturating_add(file_names.len()),
            primary_lang,
        ) {
            SearchResult::Found => true,
            SearchResult::Done => {
                rest.is_empty() || search(workspace, rest.to_vec(), scanned_files, primary_lang)
            }
            SearchResult::Continue { new_queue } => next_search(&queue, new_queue)
                .map_or(false, |combined| {
                    search(workspace, combined, scanned_files, primary_lang)
                }),
        }
    }

    let initial_queue = vec![(relative_root.to_path_buf(), 0)];
    search(workspace, initial_queue, 0, primary_lang)
}
