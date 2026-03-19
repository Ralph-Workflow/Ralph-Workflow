//! I/O boundary for language detection.
//!
//! This module handles all filesystem operations for language detection.
//! The pure detection logic lives in the parent module.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::workspace::Workspace;

use super::scanner::{self, ScanItem, ScanResult, SearchResult};
use super::signatures::SignatureFiles;

#[derive(Debug)]
enum ScanDirsNext {
    Done(HashMap<String, usize>),
    Continue {
        queue: Vec<PathBuf>,
        counts: HashMap<String, usize>,
        files_scanned: usize,
    },
}

fn scan_dirs_step(
    entries: impl Iterator<Item = (PathBuf, bool, Option<String>)>,
    queue_tail: &[PathBuf],
    counts: HashMap<String, usize>,
    files_scanned: usize,
) -> ScanDirsNext {
    const MAX_FILES_TO_SCAN: usize = 2000;

    if files_scanned >= MAX_FILES_TO_SCAN {
        return ScanDirsNext::Done(counts);
    }

    let mut new_counts = counts;
    let mut subdirs = Vec::new();

    for (path, is_dir, ext_opt) in entries {
        if is_dir {
            subdirs.push(path);
        } else if let Some(ext) = ext_opt {
            *new_counts.entry(ext).or_insert(0) += 1;
        }
    }

    let scanned = files_scanned + new_counts.values().sum::<usize>();
    let next_queue: Vec<_> = queue_tail.iter().chain(subdirs.iter()).cloned().collect();

    if next_queue.is_empty() {
        ScanDirsNext::Done(new_counts)
    } else {
        ScanDirsNext::Continue {
            queue: next_queue,
            counts: new_counts,
            files_scanned: scanned,
        }
    }
}

#[derive(Debug)]
enum SearchDirsNext {
    Found,
    Done,
    Continue {
        queue: Vec<(PathBuf, usize)>,
        scanned_files: usize,
    },
}

fn search_dirs_step(
    queue: &[(PathBuf, usize)],
    file_names: &[(PathBuf, String)],
    scanned_files: usize,
    primary_lang: &str,
) -> SearchDirsNext {
    const MAX_FILES_TO_SCAN: usize = 2000;

    if scanned_files >= MAX_FILES_TO_SCAN {
        return SearchDirsNext::Done;
    }

    let result = scanner::advance_search(queue, file_names, scanned_files, primary_lang);

    match result {
        SearchResult::Found => SearchDirsNext::Found,
        SearchResult::Done => {
            let (_, rest) = queue
                .split_first()
                .unwrap_or((&(PathBuf::new(), 0usize), &[]));
            let rest = rest.to_vec();
            if rest.is_empty() {
                SearchDirsNext::Done
            } else {
                SearchDirsNext::Continue {
                    queue: rest,
                    scanned_files,
                }
            }
        }
        SearchResult::Continue { new_queue } => {
            let (_, rest) = queue
                .split_first()
                .unwrap_or((&(PathBuf::new(), 0usize), &[]));
            let combined: Vec<_> = if new_queue.is_empty() {
                rest.to_vec()
            } else {
                rest.iter().cloned().chain(new_queue).collect()
            };
            if combined.is_empty() {
                SearchDirsNext::Done
            } else {
                SearchDirsNext::Continue {
                    queue: combined,
                    scanned_files,
                }
            }
        }
    }
}

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

    fn should_process_entry(name_lower: &str) -> bool {
        !should_skip_dir_name(name_lower)
    }

    let mut queue = vec![relative_root.to_path_buf()];
    let mut counts = HashMap::new();
    let mut files_scanned = 0usize;

    loop {
        let (current, rest) = match queue.split_first() {
            Some((c, r)) => (c.clone(), r.to_vec()),
            None => return Ok(counts),
        };

        let entries = match workspace.read_dir(&current) {
            Ok(e) => e,
            Err(_) => {
                queue = rest;
                continue;
            }
        };

        let entry_data: Vec<_> = entries
            .into_iter()
            .filter_map(|entry| {
                let name_os = entry.file_name()?;
                let name = name_os.to_string_lossy().to_lowercase();
                if !should_process_entry(&name) {
                    return None;
                }
                let path = entry.path();
                let is_dir = path.is_dir();
                let ext = path.extension().map(|e| e.to_string_lossy().to_lowercase());
                Some((path.to_path_buf(), is_dir, ext))
            })
            .collect();

        match scan_dirs_step(entry_data.into_iter(), &rest, counts, files_scanned) {
            ScanDirsNext::Done(result) => return Ok(result),
            ScanDirsNext::Continue {
                queue: next_queue,
                counts: next_counts,
                files_scanned: next_scanned,
            } => {
                queue = next_queue;
                counts = next_counts;
                files_scanned = next_scanned;
            }
        }
    }
}

pub fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
    primary_lang: &str,
) -> bool {
    let mut queue = vec![(relative_root.to_path_buf(), 0)];
    let mut scanned_files = 0usize;

    loop {
        let (current, rest) = match queue.split_first() {
            Some((c, r)) => (c.clone(), r.to_vec()),
            None => return false,
        };

        let entries = match workspace.read_dir(&current.0) {
            Ok(e) => e,
            Err(_) => {
                queue = rest;
                continue;
            }
        };

        let file_names: Vec<_> = entries
            .into_iter()
            .filter_map(|entry| {
                let name_os = entry.file_name()?;
                let name = name_os.to_string_lossy().to_lowercase();
                Some((entry.path().to_path_buf(), name))
            })
            .collect();

        let scanned = scanned_files.saturating_add(file_names.len());

        match search_dirs_step(&queue, &file_names, scanned, primary_lang) {
            SearchDirsNext::Found => return true,
            SearchDirsNext::Done => return false,
            SearchDirsNext::Continue {
                queue: new_queue,
                scanned_files: next_scanned,
            } => {
                queue = if new_queue.is_empty() {
                    rest
                } else {
                    rest.iter().cloned().chain(new_queue).collect()
                };
                scanned_files = next_scanned;
            }
        }
    }
}
