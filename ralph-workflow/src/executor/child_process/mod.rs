//! Child process detection module.

mod macos;
mod ps;

pub use macos::{child_info_from_libproc, child_pid_entry_count};
pub use ps::{parse_pgrep_output, parse_ps_output};
