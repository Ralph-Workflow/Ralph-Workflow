//! Clipboard operations for prompt saving.
//!
//! This is a boundary module - process operations are allowed here.

use crate::executor::ProcessExecutor;
use crate::pipeline::clipboard::ClipboardCommand;
use std::io::{self, Write};

pub fn copy_to_clipboard(
    executor: &dyn ProcessExecutor,
    prompt: &str,
    clipboard_cmd: ClipboardCommand,
) -> io::Result<()> {
    let mut child = executor
        .spawn(clipboard_cmd.binary, clipboard_cmd.args, &[], None)
        .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;
    if let Some(ref mut stdin) = child.stdin {
        stdin.write_all(prompt.as_bytes())?;
    }
    let _ = child.wait();
    Ok(())
}
