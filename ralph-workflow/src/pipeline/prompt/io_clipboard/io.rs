// pipeline/prompt/io_clipboard/io.rs — boundary module for clipboard operations.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Clipboard operations for prompt saving.
//
// This is a boundary module - process operations are allowed here.

use crate::pipeline::clipboard::ClipboardCommand;

pub fn copy_to_clipboard(
    executor: &dyn crate::executor::ProcessExecutor,
    prompt: &str,
    clipboard_cmd: ClipboardCommand,
) -> std::io::Result<()> {
    let mut spawned = executor.spawn(clipboard_cmd.binary, clipboard_cmd.args, &[], None)?;
    if let Some(ref mut stdin) = spawned.stdin {
        std::io::Write::write_all(stdin, prompt.as_bytes())?;
    }
    let _ = spawned.wait();
    Ok(())
}
