//! Clipboard operations for prompt saving.
//!
//! This is a boundary module - process operations are allowed here.

use crate::pipeline::clipboard::ClipboardCommand;

pub fn copy_to_clipboard(
    executor: &dyn crate::executor::ProcessExecutor,
    prompt: &str,
    clipboard_cmd: ClipboardCommand,
) -> std::io::Result<()> {
    let mut child = executor
        .spawn(clipboard_cmd.binary, clipboard_cmd.args, &[], None)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    if let Some(ref mut stdin) = child.stdin {
        std::io::Write::write_all(stdin, prompt.as_bytes())?;
    }
    let _ = child.wait();
    Ok(())
}
