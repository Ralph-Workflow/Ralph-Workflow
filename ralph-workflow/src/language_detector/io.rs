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

fn classify_entries(
    entries: impl Iterator<Item = (PathBuf, bool, Option<String>)>,
    counts: &mut HashMap<String, usize>,
) -> Vec<PathBuf> {
    let mut subdirs = Vec::new();
    entries.for_each(|(path, is_dir, ext_opt)| {
        if is_dir {
            subdirs.push(path);
        } else if let Some(ext) = ext_opt {
            *counts.entry(ext).or_insert(0) += 1;
        }
    });
    subdirs
}

fn build_next_scan_queue(queue_tail: &[PathBuf], subdirs: Vec<PathBuf>) -> Vec<PathBuf> {
    let mut next_queue = queue_tail.to_vec();
    next_queue.extend(subdirs);
    next_queue
}

fn classify_to_scan_next(
    queue: Vec<PathBuf>,
    counts: HashMap<String, usize>,
    scanned: usize,
) -> ScanDirsNext {
    if queue.is_empty() {
        ScanDirsNext::Done(counts)
    } else {
        ScanDirsNext::Continue {
            queue,
            counts,
            files_scanned: scanned,
        }
    }
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
    let subdirs = classify_entries(entries, &mut new_counts);
    let scanned = files_scanned + new_counts.values().sum::<usize>();
    classify_to_scan_next(
        build_next_scan_queue(queue_tail, subdirs),
        new_counts,
        scanned,
    )
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

fn queue_rest(queue: &[(PathBuf, usize)]) -> Vec<(PathBuf, usize)> {
    let (_, rest) = queue
        .split_first()
        .unwrap_or((&(PathBuf::new(), 0usize), &[]));
    rest.to_vec()
}

fn continue_or_done(queue: Vec<(PathBuf, usize)>, scanned_files: usize) -> SearchDirsNext {
    if queue.is_empty() {
        SearchDirsNext::Done
    } else {
        SearchDirsNext::Continue {
            queue,
            scanned_files,
        }
    }
}

fn merge_with_new_queue(
    rest: Vec<(PathBuf, usize)>,
    new_queue: Vec<(PathBuf, usize)>,
) -> Vec<(PathBuf, usize)> {
    rest.into_iter().chain(new_queue).collect()
}

fn map_search_result(
    result: SearchResult,
    queue: &[(PathBuf, usize)],
    scanned_files: usize,
) -> SearchDirsNext {
    match result {
        SearchResult::Found => SearchDirsNext::Found,
        SearchResult::Done => continue_or_done(queue_rest(queue), scanned_files),
        SearchResult::Continue { new_queue } => continue_or_done(
            merge_with_new_queue(queue_rest(queue), new_queue),
            scanned_files,
        ),
    }
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
    map_search_result(result, queue, scanned_files)
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
                    .filter_map(|item| workspace.read_dir(&item.path).ok())
                    .flat_map(|entries| {
                        entries.into_iter().filter_map(|entry| {
                            let path = entry.path();
                            let name = entry.file_name()?.to_string_lossy().to_string();
                            Some(ScanItem {
                                path: path.to_path_buf(),
                                name: name.to_lowercase(),
                            })
                        })
                    })
                    .collect();
                let mut matched = matched;
                matched.extend(scan(workspace, all_entries, depth + 1));
                matched
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

    let mut by_name_lower: HashMap<String, Vec<PathBuf>> = HashMap::new();
    for (path, name) in matched_files {
        by_name_lower.entry(name).or_default().push(path);
    }

    SignatureFiles { by_name_lower }
}

fn should_process_entry(name_lower: &str) -> bool {
    use super::scanner::should_skip_dir_name;
    !should_skip_dir_name(name_lower)
}

fn map_dir_entry(entry: &crate::workspace::DirEntry) -> Option<(PathBuf, bool, Option<String>)> {
    let name_os = entry.file_name()?;
    let name = name_os.to_string_lossy().to_lowercase();
    if !should_process_entry(&name) {
        return None;
    }
    let path = entry.path();
    let is_dir = entry.is_dir();
    let ext = path.extension().map(|e| e.to_string_lossy().to_lowercase());
    Some((path.to_path_buf(), is_dir, ext))
}

fn apply_scan_dirs_next(
    workspace: &dyn Workspace,
    next: ScanDirsNext,
) -> std::io::Result<HashMap<String, usize>> {
    match next {
        ScanDirsNext::Done(result) => Ok(result),
        ScanDirsNext::Continue {
            queue: next_queue,
            counts: next_counts,
            files_scanned: next_scanned,
        } => process_dir_queue(workspace, next_queue, next_counts, next_scanned),
    }
}

fn process_dir_head(
    workspace: &dyn Workspace,
    head: PathBuf,
    rest: Vec<PathBuf>,
    counts: HashMap<String, usize>,
    files_scanned: usize,
) -> std::io::Result<HashMap<String, usize>> {
    let entries = match workspace.read_dir(&head) {
        Ok(e) => e,
        Err(_) => return process_dir_queue(workspace, rest, counts, files_scanned),
    };
    let next = scan_dirs_step(
        entries.iter().filter_map(map_dir_entry),
        &rest,
        counts,
        files_scanned,
    );
    apply_scan_dirs_next(workspace, next)
}

fn process_dir_queue(
    workspace: &dyn Workspace,
    queue: Vec<PathBuf>,
    counts: HashMap<String, usize>,
    files_scanned: usize,
) -> std::io::Result<HashMap<String, usize>> {
    match queue.split_first() {
        Some((current, rest)) => process_dir_head(
            workspace,
            current.clone(),
            rest.to_vec(),
            counts,
            files_scanned,
        ),
        None => Ok(counts),
    }
}

pub fn count_extensions_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
) -> std::io::Result<HashMap<String, usize>> {
    process_dir_queue(
        workspace,
        vec![relative_root.to_path_buf()],
        HashMap::new(),
        0,
    )
}

fn merge_search_queue(
    rest: &[(PathBuf, usize)],
    new_queue: Vec<(PathBuf, usize)>,
) -> Vec<(PathBuf, usize)> {
    match (rest.is_empty(), new_queue.is_empty()) {
        (true, true) => Vec::new(),
        (false, true) => rest.to_vec(),
        (true, false) => new_queue,
        (false, false) => {
            let mut combined = rest.to_vec();
            combined.extend(new_queue);
            combined
        }
    }
}

fn collect_file_names(entries: Vec<crate::workspace::DirEntry>) -> Vec<(PathBuf, String)> {
    entries
        .into_iter()
        .filter_map(|entry| {
            let name_os = entry.file_name()?;
            let name = name_os.to_string_lossy().to_lowercase();
            Some((entry.path().to_path_buf(), name))
        })
        .collect()
}

fn eval_search_dirs_next(
    workspace: &dyn Workspace,
    rest: &[(PathBuf, usize)],
    primary_lang: &str,
    next: SearchDirsNext,
) -> bool {
    match next {
        SearchDirsNext::Found => true,
        SearchDirsNext::Done => false,
        SearchDirsNext::Continue {
            queue: new_queue,
            scanned_files: next_scanned,
        } => detect_tests_recurse(
            workspace,
            merge_search_queue(rest, new_queue),
            next_scanned,
            primary_lang,
        ),
    }
}

fn detect_tests_step(
    workspace: &dyn Workspace,
    current: (PathBuf, usize),
    rest: &[(PathBuf, usize)],
    queue: &[(PathBuf, usize)],
    scanned_files: usize,
    primary_lang: &str,
) -> bool {
    let entries = match workspace.read_dir(&current.0) {
        Ok(e) => e,
        Err(_) => {
            return detect_tests_recurse(workspace, rest.to_vec(), scanned_files, primary_lang)
        }
    };
    let file_names = collect_file_names(entries);
    let scanned = scanned_files.saturating_add(file_names.len());
    let next = search_dirs_step(queue, &file_names, scanned, primary_lang);
    eval_search_dirs_next(workspace, rest, primary_lang, next)
}

fn detect_tests_recurse(
    workspace: &dyn Workspace,
    queue: Vec<(PathBuf, usize)>,
    scanned_files: usize,
    primary_lang: &str,
) -> bool {
    match queue.split_first() {
        Some((current, rest)) => detect_tests_step(
            workspace,
            current.clone(),
            rest,
            &queue,
            scanned_files,
            primary_lang,
        ),
        None => false,
    }
}

pub fn detect_tests_with_workspace(
    workspace: &dyn Workspace,
    relative_root: &Path,
    primary_lang: &str,
) -> bool {
    detect_tests_recurse(
        workspace,
        vec![(relative_root.to_path_buf(), 0)],
        0usize,
        primary_lang,
    )
}
