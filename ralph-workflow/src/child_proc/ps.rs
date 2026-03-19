//! PS output parsing for child process detection.

use crate::executor::ChildProcessInfo;
use std::collections::{HashMap, HashSet, VecDeque};

#[derive(Clone, Copy)]
struct ProcessSnapshotEntry {
    pid: u32,
    parent_pid: u32,
    cpu_time_ms: u64,
    in_scope: bool,
    currently_active: bool,
}

fn parse_cputime_parts(hours_or_minutes: &str, minutes: &str, seconds_str: &str) -> Option<u64> {
    let hours = if let Some((days, hours)) = hours_or_minutes.split_once('-') {
        let days: u64 = days.parse().ok()?;
        let hours: u64 = hours.parse().ok()?;
        days.checked_mul(24)?.checked_add(hours)?
    } else {
        hours_or_minutes.parse().ok()?
    };
    let minutes: u64 = minutes.parse().ok()?;
    let (secs, frac_ms) = parse_seconds_with_fraction(seconds_str)?;
    Some((hours * 3600 + minutes * 60 + secs) * 1000 + frac_ms)
}

fn parse_seconds_with_fraction(s: &str) -> Option<(u64, u64)> {
    if let Some((secs_str, frac)) = s.split_once('.') {
        let secs: u64 = secs_str.parse().ok()?;
        let frac: u64 = frac.get(..2).unwrap_or(frac).parse().ok()?;
        Some((secs, frac * 10))
    } else {
        Some((s.parse().ok()?, 0))
    }
}

pub fn parse_cputime_ms(s: &str) -> Option<u64> {
    let parts: Vec<&str> = s.split(':').collect();
    match parts.len() {
        3 => parse_cputime_parts(parts[0], parts[1], parts[2]),
        2 => {
            let minutes: u64 = parts[0].parse().ok()?;
            let (secs, frac_ms) = parse_seconds_with_fraction(parts[1])?;
            Some((minutes * 60 + secs) * 1000 + frac_ms)
        }
        _ => None,
    }
}

fn qualifies_process_state(state: &str) -> bool {
    match state.chars().next() {
        Some('Z' | 'X' | 'T' | 'I') | None => false,
        Some(_) => true,
    }
}

fn state_indicates_current_activity(state: &str, cpu_time_ms: u64) -> bool {
    match state.chars().next() {
        Some('D' | 'U') => true,
        Some('R') => cpu_time_ms > 0,
        _ => false,
    }
}

fn module_level_descendant_pid_signature(descendants: &[u32]) -> u64 {
    const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

    descendants.iter().fold(FNV_OFFSET, |signature, &pid| {
        pid.to_le_bytes().iter().fold(signature, |sig, &byte| {
            (sig ^ u64::from(byte)).wrapping_mul(FNV_PRIME)
        })
    })
}

pub fn parse_ps_output(stdout: &str, parent_pid: u32) -> Option<ChildProcessInfo> {
    let parse_results: Vec<_> = stdout
        .lines()
        .filter_map(|line| parse_ps_line(line, parent_pid))
        .collect();

    if parse_results.is_empty() {
        return None;
    }

    compute_child_info_from_snapshot(parent_pid, &parse_results)
}

fn parse_ps_line(line: &str, parent_pid: u32) -> Option<(u32, u32, u64, bool, bool)> {
    let parts: Vec<&str> = line.split_whitespace().collect();
    if parts.len() < 3 {
        return None;
    }

    let Ok(entry_pid) = parts[0].parse::<u32>() else {
        return None;
    };
    let Ok(parent_of_entry) = parts[1].parse::<u32>() else {
        return None;
    };

    let (in_scope, currently_active, cputime_text) = extract_ps_policy(parts, parent_pid);
    let cpu_ms = parse_cputime_ms(cputime_text).unwrap_or(0);
    Some((
        parent_of_entry,
        entry_pid,
        cpu_ms,
        in_scope,
        currently_active,
    ))
}

fn extract_ps_policy<'a>(parts: &'a [&'a str], parent_pid: u32) -> (bool, bool, &'a str) {
    if parts.len() >= 5 {
        let pgid_matches_parent = parts[2]
            .parse::<u32>()
            .ok()
            .is_some_and(|pgid| pgid == parent_pid);
        let state_qualifies = qualifies_process_state(parts[3]);
        let cpu_ms = parse_cputime_ms(parts[4]).unwrap_or(0);
        (
            pgid_matches_parent && state_qualifies,
            state_indicates_current_activity(parts[3], cpu_ms),
            parts[4],
        )
    } else {
        (true, false, parts[2])
    }
}

fn compute_child_info_from_snapshot(
    parent_pid: u32,
    entries: &[(u32, u32, u64, bool, bool)],
) -> Option<ChildProcessInfo> {
    let children_of = build_children_map(entries);
    let descendant_info = compute_descendant_traversal(parent_pid, &children_of);

    if descendant_info.child_count == 0 {
        return Some(ChildProcessInfo::NONE);
    }

    Some(ChildProcessInfo {
        child_count: descendant_info.child_count,
        active_child_count: descendant_info.active_child_count,
        cpu_time_ms: descendant_info.total_cpu_ms,
        descendant_pid_signature: module_level_descendant_pid_signature(
            &descendant_info.descendant_pids,
        ),
    })
}

fn build_children_map(
    entries: &[(u32, u32, u64, bool, bool)],
) -> HashMap<u32, Vec<ProcessSnapshotEntry>> {
    let mut children_of: HashMap<u32, Vec<ProcessSnapshotEntry>> = HashMap::new();
    for (parent_of_entry, entry_pid, cpu_ms, in_scope, currently_active) in entries {
        children_of
            .entry(*parent_of_entry)
            .or_default()
            .push(ProcessSnapshotEntry {
                pid: *entry_pid,
                parent_pid: *parent_of_entry,
                cpu_time_ms: *cpu_ms,
                in_scope: *in_scope,
                currently_active: *currently_active,
            });
    }
    children_of
}

struct DescendantTraversalResult {
    child_count: u32,
    active_child_count: u32,
    total_cpu_ms: u64,
    descendant_pids: Vec<u32>,
}

fn compute_descendant_traversal(
    parent_pid: u32,
    children_of: &HashMap<u32, Vec<ProcessSnapshotEntry>>,
) -> DescendantTraversalResult {
    let mut child_count: u32 = 0;
    let mut active_child_count: u32 = 0;
    let mut total_cpu_ms: u64 = 0;
    let mut descendant_pids = Vec::new();
    let mut visited = HashSet::new();
    let mut queue = VecDeque::from([parent_pid]);

    while let Some(current) = queue.pop_front() {
        if let Some(kids) = children_of.get(&current) {
            for child in kids {
                if !child.in_scope || !visited.insert(child.pid) {
                    continue;
                }

                debug_assert_eq!(child.parent_pid, current);
                child_count = child_count.saturating_add(1);
                if child.currently_active {
                    active_child_count = active_child_count.saturating_add(1);
                }
                total_cpu_ms += child.cpu_time_ms;
                descendant_pids.push(child.pid);
                queue.push_back(child.pid);
            }
        }
    }

    descendant_pids.sort_unstable();
    DescendantTraversalResult {
        child_count,
        active_child_count,
        total_cpu_ms,
        descendant_pids,
    }
}

pub fn parse_pgrep_output(stdout: &str) -> Option<Vec<u32>> {
    let child_pids: Vec<u32> = stdout
        .lines()
        .filter_map(|line| {
            let pid = line.trim();
            if pid.is_empty() {
                None
            } else {
                pid.parse::<u32>().ok()
            }
        })
        .collect();
    Some(child_pids)
}
