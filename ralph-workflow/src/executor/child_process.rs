//! Child process detection utilities.
//!
//! This module provides functions for detecting and inspecting child processes
//! of a parent process. Used by the idle-timeout monitor to determine whether
//! child processes are actively working vs merely existing.

use super::ChildProcessInfo;

#[cfg(target_os = "macos")]
mod macos;

#[cfg(target_os = "macos")]
pub use macos::*;

pub mod ps;

pub use ps::{parse_pgrep_output, parse_ps_output};

fn descendant_pid_signature(descendants: &[u32]) -> u64 {
    const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

    descendants.iter().fold(FNV_OFFSET, |signature, &pid| {
        pid.to_le_bytes().iter().fold(signature, |sig, &byte| {
            (sig ^ u64::from(byte)).wrapping_mul(FNV_PRIME)
        })
    })
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
        descendant_pid_signature: descendant_pid_signature(descendants),
    }
}

#[expect(
    clippy::print_stderr,
    reason = "diagnostic warning for system tool failure"
)]
pub fn warn_child_process_detection_degraded() {
    use std::sync::OnceLock;
    static WARNED: OnceLock<()> = OnceLock::new();
    if WARNED.set(()).is_ok() {
        eprintln!(
            "Warning: child-process detection degraded (ps unavailable or failing); \
             idle-timeout false-positive prevention may be reduced"
        );
    }
}

#[expect(
    clippy::print_stderr,
    reason = "diagnostic warning for system tool failure"
)]
pub fn warn_child_process_detection_conservative() {
    use std::sync::OnceLock;
    static WARNED: OnceLock<()> = OnceLock::new();
    if WARNED.set(()).is_ok() {
        eprintln!(
            "Warning: child-process detection is running in conservative fallback mode \
             (descendant PIDs found without state/CPU evidence); idle timeout will not \
             be suppressed by those descendants"
        );
    }
}
