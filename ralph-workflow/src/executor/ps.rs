//! PS output parsing for child process detection.

use super::ChildProcessInfo;

#[derive(Clone, Copy)]
struct ProcessEntry {
    pid: u32,
    parent_pid: u32,
    cpu_time_ms: u64,
    in_scope: bool,
    currently_active: bool,
}

fn parse_seconds_frac(seconds_str: &str) -> Option<(u64, u64)> {
    if let Some((s, f)) = seconds_str.split_once('.') {
        let secs: u64 = s.parse().ok()?;
        let frac: u64 = f.get(..2).unwrap_or(f).parse().ok()?;
        Some((secs, frac * 10))
    } else {
        Some((seconds_str.parse().ok()?, 0))
    }
}

fn parse_hours_field(field: &str) -> Option<u64> {
    if let Some((days, hours)) = field.split_once('-') {
        let days: u64 = days.parse().ok()?;
        let hours: u64 = hours.parse().ok()?;
        days.checked_mul(24)?.checked_add(hours)
    } else {
        field.parse().ok()
    }
}

pub fn parse_cputime_ms(s: &str) -> Option<u64> {
    let parts: Vec<&str> = s.split(':').collect();
    match parts.len() {
        3 => {
            let hours = parse_hours_field(parts[0])?;
            let minutes: u64 = parts[1].parse().ok()?;
            let (secs, frac_ms) = parse_seconds_frac(parts[2])?;
            Some((hours * 3600 + minutes * 60 + secs) * 1000 + frac_ms)
        }
        2 => {
            let minutes: u64 = parts[0].parse().ok()?;
            let (secs, frac_ms) = parse_seconds_frac(parts[1])?;
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

fn build_children_lookup(
    entries: &[ProcessEntry],
) -> std::collections::HashMap<u32, Vec<ProcessEntry>> {
    entries
        .iter()
        .fold(std::collections::HashMap::new(), |mut lookup, entry| {
            lookup.entry(entry.parent_pid).or_default().push(*entry);
            lookup
        })
}

struct ChildTally {
    child_count: u32,
    active_child_count: u32,
    total_cpu_ms: u64,
    descendant_pids: Vec<u32>,
}

fn tally_one_child(
    child: &ProcessEntry,
    tally: &mut ChildTally,
    visited: &mut std::collections::HashSet<u32>,
    queue: &mut std::collections::VecDeque<u32>,
) {
    if !child.in_scope || !visited.insert(child.pid) {
        return;
    }
    tally.child_count = tally.child_count.saturating_add(1);
    if child.currently_active {
        tally.active_child_count = tally.active_child_count.saturating_add(1);
    }
    tally.total_cpu_ms += child.cpu_time_ms;
    tally.descendant_pids.push(child.pid);
    queue.push_back(child.pid);
}

fn tally_children_of(
    current: u32,
    children_of: &std::collections::HashMap<u32, Vec<ProcessEntry>>,
    tally: &mut ChildTally,
    visited: &mut std::collections::HashSet<u32>,
    queue: &mut std::collections::VecDeque<u32>,
) {
    if let Some(kids) = children_of.get(&current) {
        kids.iter().for_each(|child| {
            debug_assert_eq!(child.parent_pid, current);
            tally_one_child(child, tally, visited, queue);
        });
    }
}

fn new_child_tally() -> ChildTally {
    ChildTally {
        child_count: 0,
        active_child_count: 0,
        total_cpu_ms: 0,
        descendant_pids: Vec::new(),
    }
}

fn drain_tally_queue(
    queue: &mut std::collections::VecDeque<u32>,
    children_of: &std::collections::HashMap<u32, Vec<ProcessEntry>>,
    tally: &mut ChildTally,
    visited: &mut std::collections::HashSet<u32>,
) {
    while let Some(current) = queue.pop_front() {
        tally_children_of(current, children_of, tally, visited, queue);
    }
}

fn accumulate_children(
    children_of: &std::collections::HashMap<u32, Vec<ProcessEntry>>,
    parent_pid: u32,
) -> ChildTally {
    let mut tally = new_child_tally();
    let mut visited = std::collections::HashSet::new();
    let mut queue = std::collections::VecDeque::from([parent_pid]);
    drain_tally_queue(&mut queue, children_of, &mut tally, &mut visited);
    tally
}

fn compute_child_process_info(
    entries: Vec<ProcessEntry>,
    parent_pid: u32,
) -> Option<ChildProcessInfo> {
    let children_of = build_children_lookup(&entries);
    let mut tally = accumulate_children(&children_of, parent_pid);
    tally.descendant_pids.sort_unstable();

    if tally.child_count == 0 {
        return Some(ChildProcessInfo::NONE);
    }

    Some(ChildProcessInfo {
        child_count: tally.child_count,
        active_child_count: tally.active_child_count,
        cpu_time_ms: tally.total_cpu_ms,
        descendant_pid_signature: module_level_descendant_pid_signature(&tally.descendant_pids),
    })
}

pub fn parse_ps_output(stdout: &str, parent_pid: u32) -> Option<ChildProcessInfo> {
    let entries = parse_ps_entries(stdout, parent_pid);
    compute_child_process_info(entries, parent_pid)
}

fn parse_ps_entries(stdout: &str, parent_pid: u32) -> Vec<ProcessEntry> {
    stdout
        .lines()
        .filter_map(|line| parse_ps_line(line, parent_pid))
        .collect()
}

fn parse_ps_line_extended<'a>(parts: &[&'a str], parent_pid: u32) -> (bool, bool, &'a str) {
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
}

fn parse_pid_pair(parts: &[&str]) -> Option<(u32, u32)> {
    let entry_pid = parts[0].parse::<u32>().ok()?;
    let parent_of_entry = parts[1].parse::<u32>().ok()?;
    Some((entry_pid, parent_of_entry))
}

fn parse_ps_line(line: &str, parent_pid: u32) -> Option<ProcessEntry> {
    let parts: Vec<&str> = line.split_whitespace().collect();
    if parts.len() < 3 {
        return None;
    }

    let (entry_pid, parent_of_entry) = parse_pid_pair(&parts)?;

    let (in_scope, currently_active, cputime_text) = if parts.len() >= 5 {
        parse_ps_line_extended(&parts, parent_pid)
    } else {
        (true, false, parts[2])
    };

    let cpu_ms = parse_cputime_ms(cputime_text).unwrap_or(0);
    Some(ProcessEntry {
        pid: entry_pid,
        parent_pid: parent_of_entry,
        cpu_time_ms: cpu_ms,
        in_scope,
        currently_active,
    })
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

fn canonical_descendant_signature(descendants: &[u32]) -> u64 {
    let mut sorted = descendants.to_vec();
    sorted.sort_unstable();
    module_level_descendant_pid_signature(&sorted)
}

pub fn child_info_from_descendant_pids(descendants: &[u32]) -> ChildProcessInfo {
    if descendants.is_empty() {
        return ChildProcessInfo::NONE;
    }

    let child_count = u32::try_from(descendants.len()).unwrap_or(u32::MAX);
    ChildProcessInfo {
        child_count,
        active_child_count: 0,
        cpu_time_ms: 0,
        descendant_pid_signature: canonical_descendant_signature(descendants),
    }
}

pub fn warn_child_process_detection_conservative() {}

pub fn warn_child_process_detection_degraded() {}
