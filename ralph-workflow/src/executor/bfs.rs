//! BFS traversal for child process discovery.
//!
//! Uses functional iteration for child process detection.

use crate::executor::ps::child_info_from_descendant_pids;
use crate::executor::ps::parse_pgrep_output;
use crate::executor::{ChildProcessInfo, ProcessExecutor};
use std::collections::{HashSet, VecDeque};

fn pgrep_children<E: ProcessExecutor + ?Sized>(executor: &E, current_pid: u32) -> Option<Vec<u32>> {
    let output = executor
        .execute("pgrep", &["-P", &current_pid.to_string()], &[], None)
        .ok()?;
    if output.succeeded() {
        parse_pgrep_output(&output.stdout)
    } else if output.exit_code() == 1 {
        Some(Vec::new())
    } else {
        None
    }
}

fn bfs_step(
    current: u32,
    visited: &mut HashSet<u32>,
    get_children: impl Fn(u32) -> Option<Vec<u32>>,
) -> Option<(Vec<u32>, Vec<u32>)> {
    let child_pids = get_children(current)?;
    let new_pids: Vec<u32> = child_pids
        .into_iter()
        .filter(|&pid| visited.insert(pid))
        .collect();
    Some((new_pids.clone(), new_pids))
}

fn apply_bfs_step(
    current: u32,
    queue: &mut VecDeque<u32>,
    visited: &mut HashSet<u32>,
    descendants: &mut Vec<u32>,
    get_children: &impl Fn(u32) -> Option<Vec<u32>>,
) {
    if let Some((new_queue_items, new_descendants)) = bfs_step(current, visited, get_children) {
        descendants.extend(new_descendants);
        queue.extend(new_queue_items);
    }
}

fn bfs_collect(
    queue: &mut VecDeque<u32>,
    visited: &mut HashSet<u32>,
    get_children: &impl Fn(u32) -> Option<Vec<u32>>,
) -> Vec<u32> {
    let mut descendants = Vec::new();
    while let Some(current) = queue.pop_front() {
        apply_bfs_step(current, queue, visited, &mut descendants, get_children);
    }
    descendants
}

fn bfs_traverse(start: u32, get_children: impl Fn(u32) -> Option<Vec<u32>>) -> Vec<u32> {
    let mut queue = VecDeque::from([start]);
    let mut visited = HashSet::new();
    let _ = visited.insert(start);

    let mut descendants = bfs_collect(&mut queue, &mut visited, &get_children);
    descendants.sort_unstable();
    descendants
}

/// Collect all descendant PIDs of a parent process using BFS.
pub fn collect_descendants<E: ProcessExecutor + ?Sized>(executor: &E, parent_pid: u32) -> Vec<u32> {
    bfs_traverse(parent_pid, |pid| pgrep_children(executor, pid))
}

/// Compute child process info from descendants.
pub fn compute_from_descendants(_parent_pid: u32, descendants: &[u32]) -> ChildProcessInfo {
    if descendants.is_empty() {
        return ChildProcessInfo::NONE;
    }

    child_info_from_descendant_pids(descendants)
}
