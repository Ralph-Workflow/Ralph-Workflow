//! macOS-specific child process detection using libproc.

use super::ChildProcessInfo;
use std::collections::{HashSet, VecDeque};
use std::ffi::c_void;

const PROC_PIDT_SHORTBSDINFO: libc::c_int = 13;
const PROC_PIDTASKINFO: libc::c_int = 4;
const MAXCOMLEN: usize = 16;
const SIDL: u32 = 1;
const SRUN: u32 = 2;
const SSTOP: u32 = 4;
const SZOMB: u32 = 5;

#[repr(C)]
struct ProcBsdShortInfo {
    pid: u32,
    parent_pid: u32,
    process_group_id: u32,
    status: u32,
    command: [libc::c_char; MAXCOMLEN],
    flags: u32,
    uid: libc::uid_t,
    gid: libc::gid_t,
    real_uid: libc::uid_t,
    real_gid: libc::gid_t,
    saved_uid: libc::uid_t,
    saved_gid: libc::gid_t,
    reserved: u32,
}

#[repr(C)]
struct ProcTaskInfo {
    virtual_size: u64,
    resident_size: u64,
    total_user_time: u64,
    total_system_time: u64,
    threads_user_time: u64,
    threads_system_time: u64,
    policy: i32,
    faults: i32,
    pageins: i32,
    cow_faults: i32,
    messages_sent: i32,
    messages_received: i32,
    mach_syscalls: i32,
    unix_syscalls: i32,
    context_switches: i32,
    thread_count: i32,
    running_thread_count: i32,
    priority: i32,
}

#[link(name = "proc")]
unsafe extern "C" {
    fn proc_listchildpids(pid: libc::pid_t, buffer: *mut c_void, buffersize: i32) -> i32;
    fn proc_pidinfo(
        pid: libc::pid_t,
        flavor: libc::c_int,
        arg: u64,
        buffer: *mut c_void,
        buffersize: libc::c_int,
    ) -> libc::c_int;
}

pub fn child_pid_entry_count(bytes_written: i32) -> Option<usize> {
    let bytes = usize::try_from(bytes_written).ok()?;
    let pid_width = std::mem::size_of::<libc::pid_t>();
    Some(bytes / pid_width)
}

fn descendant_pid_signature(descendants: &[u32]) -> u64 {
    const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

    descendants.iter().fold(FNV_OFFSET, |signature, &pid| {
        pid.to_le_bytes().iter().fold(signature, |sig, &byte| {
            (sig ^ u64::from(byte)).wrapping_mul(FNV_PRIME)
        })
    })
}

const fn qualifies_libproc_status(status: u32) -> bool {
    !matches!(status, SIDL | SSTOP | SZOMB)
}

const fn libproc_state_indicates_current_activity(
    status: u32,
    cpu_time_ms: u64,
    num_running_threads: i32,
) -> bool {
    status == SRUN && cpu_time_ms > 0 && num_running_threads > 0
}

fn call_proc_listchildpids(pid: libc::pid_t, capacity: usize) -> Option<(i32, Vec<libc::pid_t>)> {
    let byte_len = capacity.checked_mul(std::mem::size_of::<libc::pid_t>())?;
    let buffer_size = i32::try_from(byte_len).ok()?;
    let mut buffer = vec![libc::pid_t::default(); capacity];
    let bytes_written =
        unsafe { proc_listchildpids(pid, buffer.as_mut_ptr().cast::<c_void>(), buffer_size) };
    Some((bytes_written, buffer))
}

fn collect_child_pids_from_buffer(buffer: Vec<libc::pid_t>, count: usize) -> Vec<u32> {
    buffer
        .into_iter()
        .take(count)
        .filter_map(|p| u32::try_from(p).ok())
        .collect()
}

fn try_read_child_pids(pid: libc::pid_t, capacity: usize) -> Option<Result<Vec<u32>, usize>> {
    let (bytes_written, buffer) = call_proc_listchildpids(pid, capacity)?;
    if bytes_written < 0 {
        return None;
    }
    if bytes_written == 0 {
        return Some(Ok(Vec::new()));
    }
    let count = child_pid_entry_count(bytes_written)?;
    if count < capacity {
        return Some(Ok(collect_child_pids_from_buffer(buffer, count)));
    }
    Some(Err(capacity.checked_mul(2)?))
}

fn list_child_pids(parent_pid: u32) -> Option<Vec<u32>> {
    let pid = libc::pid_t::try_from(parent_pid).ok()?;
    let mut capacity: usize = 32;

    loop {
        match try_read_child_pids(pid, capacity)? {
            Ok(pids) => return Some(pids),
            Err(new_capacity) => capacity = new_capacity,
        }
    }
}

fn fetch_bsd_short_info(pid: u32) -> Option<ProcBsdShortInfo> {
    let mut info = ProcBsdShortInfo {
        pid: 0,
        parent_pid: 0,
        process_group_id: 0,
        status: 0,
        command: [0; MAXCOMLEN],
        flags: 0,
        uid: 0,
        gid: 0,
        real_uid: 0,
        real_gid: 0,
        saved_uid: 0,
        saved_gid: 0,
        reserved: 0,
    };
    let pid = libc::pid_t::try_from(pid).ok()?;
    let expected = i32::try_from(std::mem::size_of::<ProcBsdShortInfo>()).ok()?;
    let bytes = unsafe {
        proc_pidinfo(
            pid,
            PROC_PIDT_SHORTBSDINFO,
            0,
            (&raw mut info).cast::<c_void>(),
            expected,
        )
    };
    (bytes == expected).then_some(info)
}

fn fetch_task_info(pid: u32) -> Option<ProcTaskInfo> {
    let mut info = ProcTaskInfo {
        virtual_size: 0,
        resident_size: 0,
        total_user_time: 0,
        total_system_time: 0,
        threads_user_time: 0,
        threads_system_time: 0,
        policy: 0,
        faults: 0,
        pageins: 0,
        cow_faults: 0,
        messages_sent: 0,
        messages_received: 0,
        mach_syscalls: 0,
        unix_syscalls: 0,
        context_switches: 0,
        thread_count: 0,
        running_thread_count: 0,
        priority: 0,
    };
    let pid = libc::pid_t::try_from(pid).ok()?;
    let expected = i32::try_from(std::mem::size_of::<ProcTaskInfo>()).ok()?;
    let bytes = unsafe {
        proc_pidinfo(
            pid,
            PROC_PIDTASKINFO,
            0,
            (&raw mut info).cast::<c_void>(),
            expected,
        )
    };
    (bytes == expected).then_some(info)
}

fn process_child_pids(
    child_pids: Vec<u32>,
    descendants: &mut Vec<u32>,
    visited: &mut HashSet<u32>,
    queue: &mut VecDeque<u32>,
) {
    child_pids
        .into_iter()
        .filter(|&p| visited.insert(p))
        .for_each(|p| {
            descendants.push(p);
            queue.push_back(p);
        });
}

fn drain_pid_queue(
    queue: &mut VecDeque<u32>,
    descendants: &mut Vec<u32>,
    visited: &mut HashSet<u32>,
) -> Option<()> {
    while let Some(pid) = queue.pop_front() {
        let child_pids = list_child_pids(pid)?;
        process_child_pids(child_pids, descendants, visited, queue);
    }
    Some(())
}

fn collect_descendant_pids(current_pid: u32) -> Option<Vec<u32>> {
    let mut descendants = Vec::new();
    let mut visited = HashSet::new();
    let mut queue = VecDeque::from([current_pid]);
    drain_pid_queue(&mut queue, &mut descendants, &mut visited)?;
    descendants.sort_unstable();
    Some(descendants)
}

struct DescendantTally {
    child_count: u32,
    active_child_count: u32,
    total_cpu_ms: u64,
    qualifying_pids: Vec<u32>,
}

fn pid_qualifies_for_parent(bsd_info: &ProcBsdShortInfo, parent_pid: u32) -> bool {
    bsd_info.process_group_id == parent_pid && qualifies_libproc_status(bsd_info.status)
}

fn cpu_time_ms_from_task(task_info: &Option<ProcTaskInfo>) -> u64 {
    task_info.as_ref().map_or(0, |info| {
        (info.total_user_time + info.total_system_time) / 1_000_000
    })
}

fn running_threads_from_task(task_info: &Option<ProcTaskInfo>) -> i32 {
    task_info
        .as_ref()
        .map_or(0, |info| info.running_thread_count)
}

fn tally_one_descendant(pid: u32, parent_pid: u32, tally: &mut DescendantTally) {
    let Some(bsd_info) = fetch_bsd_short_info(pid) else {
        return;
    };
    if !pid_qualifies_for_parent(&bsd_info, parent_pid) {
        return;
    }
    let task_info = fetch_task_info(pid);
    let cpu_time_ms = cpu_time_ms_from_task(&task_info);
    let num_running_threads = running_threads_from_task(&task_info);
    tally.child_count = tally.child_count.saturating_add(1);
    tally.total_cpu_ms += cpu_time_ms;
    if libproc_state_indicates_current_activity(bsd_info.status, cpu_time_ms, num_running_threads) {
        tally.active_child_count = tally.active_child_count.saturating_add(1);
    }
    tally.qualifying_pids.push(pid);
}

fn build_descendant_tally(parent_pid: u32, descendants: &[u32]) -> DescendantTally {
    let mut tally = DescendantTally {
        child_count: 0,
        active_child_count: 0,
        total_cpu_ms: 0,
        qualifying_pids: Vec::new(),
    };
    descendants
        .iter()
        .for_each(|&pid| tally_one_descendant(pid, parent_pid, &mut tally));
    tally
}

fn tally_to_child_info(tally: DescendantTally) -> ChildProcessInfo {
    ChildProcessInfo {
        child_count: tally.child_count,
        active_child_count: tally.active_child_count,
        cpu_time_ms: tally.total_cpu_ms,
        descendant_pid_signature: descendant_pid_signature(&tally.qualifying_pids),
    }
}

fn compute_child_info_from_descendants(
    parent_pid: u32,
    descendants: &[u32],
) -> Option<ChildProcessInfo> {
    if descendants.is_empty() {
        return None;
    }
    let tally = build_descendant_tally(parent_pid, descendants);
    if tally.child_count == 0 {
        return Some(ChildProcessInfo::NONE);
    }
    Some(tally_to_child_info(tally))
}

pub fn child_info_from_libproc(parent_pid: u32) -> Option<ChildProcessInfo> {
    let descendants = collect_descendant_pids(parent_pid)?;
    compute_child_info_from_descendants(parent_pid, &descendants)
}
