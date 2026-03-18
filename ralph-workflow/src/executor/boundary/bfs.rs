//! BFS traversal for child process discovery.
//!
//! Uses imperative loops for efficient child process detection.

use crate::executor::child_proc::parse_pgrep_output;
use crate::executor::{ChildProcessInfo, ProcessExecutor};
use std::collections::{HashSet, VecDeque};

/// Collect all descendant PIDs of a parent process using BFS.
pub fn collect_descendants<E: ProcessExecutor>(executor: &E, parent_pid: u32) -> Vec<u32> {
    let mut descendants = Vec::new();
    let mut visited = HashSet::new();
    let mut queue = VecDeque::from([parent_pid]);

    while let Some(current_pid) = queue.pop_front() {
        let Ok(output) = executor.execute("pgrep", &["-P", &current_pid.to_string()], &[], None)
        else {
            return descendants;
        };

        let child_pids = if output.status.success() {
            match parse_pgrep_output(&output.stdout) {
                Some(pids) => pids,
                None => return descendants,
            }
        } else if output.status.code() == Some(1) {
            Vec::new()
        } else {
            return descendants;
        };

        for child_pid in child_pids {
            if visited.insert(child_pid) {
                descendants.push(child_pid);
                queue.push_back(child_pid);
            }
        }
    }

    descendants.sort_unstable();
    descendants
}

/// Compute child process info from descendants.
pub fn compute_from_descendants(parent_pid: u32, descendants: &[u32]) -> ChildProcessInfo {
    use super::super::child_proc::child_info_from_descendant_pids;

    if descendants.is_empty() {
        return ChildProcessInfo::NONE;
    }

    child_info_from_descendant_pids(descendants)
}
