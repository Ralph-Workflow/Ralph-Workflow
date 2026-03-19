//! BFS traversal for child process discovery.
//!
//! Uses functional iteration for child process detection.

use crate::executor::ps::parse_pgrep_output;
use crate::executor::{ChildProcessInfo, ProcessExecutor};
use std::collections::{HashSet, VecDeque};

fn pgrep_children<E: ProcessExecutor + ?Sized>(executor: &E, current_pid: u32) -> Option<Vec<u32>> {
    let output = executor
        .execute("pgrep", &["-P", &current_pid.to_string()], &[], None)
        .ok()?;
    if output.status.success() {
        parse_pgrep_output(&output.stdout)
    } else if output.status.code() == Some(1) {
        Some(Vec::new())
    } else {
        None
    }
}

fn bfs_traverse<F>(start: u32, get_children: F) -> Vec<u32>
where
    F: Fn(u32) -> Option<Vec<u32>>,
{
    let mut queue = VecDeque::from([start]);
    let mut visited = HashSet::new();
    let mut descendants = Vec::new();

    while let Some(current) = queue.pop_front() {
        let Some(children) = get_children(current) else {
            break;
        };

        children
            .into_iter()
            .filter(|&pid| visited.insert(pid))
            .for_each(|pid| {
                descendants.push(pid);
                queue.push_back(pid);
            });
    }

    descendants.sort_unstable();
    descendants
}

/// Collect all descendant PIDs of a parent process using BFS.
pub fn collect_descendants<E: ProcessExecutor + ?Sized>(executor: &E, parent_pid: u32) -> Vec<u32> {
    bfs_traverse(parent_pid, |pid| pgrep_children(executor, pid))
}

/// Compute child process info from descendants.
pub fn compute_from_descendants(_parent_pid: u32, descendants: &[u32]) -> ChildProcessInfo {
    use crate::executor::child_info_from_descendant_pids;

    if descendants.is_empty() {
        return ChildProcessInfo::NONE;
    }

    child_info_from_descendant_pids(descendants)
}
