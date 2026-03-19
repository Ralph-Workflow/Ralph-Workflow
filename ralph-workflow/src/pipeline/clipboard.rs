//! Platform-specific clipboard command configuration.

use crate::executor::{ProcessExecutor, RealProcessExecutor};
use crate::platform::Platform;

/// Platform-specific clipboard command configuration.
#[derive(Clone)]
pub struct ClipboardCommand {
    pub binary: &'static str,
    pub args: &'static [&'static str],
    pub paste_hint: &'static str,
}

/// Get platform-specific clipboard command using default process executor.
///
/// Returns None if no clipboard command is available for current platform.
pub fn get_platform_clipboard_command() -> Option<ClipboardCommand> {
    get_platform_clipboard_command_with_executor(&RealProcessExecutor)
}

/// Get platform-specific clipboard command with a provided process executor.
///
/// Returns None if no clipboard command is available for current platform.
pub fn get_platform_clipboard_command_with_executor(
    executor: &dyn ProcessExecutor,
) -> Option<ClipboardCommand> {
    let platform = Platform::detect_with_executor(executor);

    match platform {
        Platform::MacWithBrew | Platform::MacWithoutBrew => Some(ClipboardCommand {
            binary: "pbcopy",
            args: &[],
            paste_hint: "pbpaste to view",
        }),
        Platform::DebianLinux
        | Platform::RhelLinux
        | Platform::ArchLinux
        | Platform::GenericLinux => {
            // Try wl-copy (Wayland) first, then xclip (X11)
            if executor.command_exists("wl-copy") {
                Some(ClipboardCommand {
                    binary: "wl-copy",
                    args: &[],
                    paste_hint: "wl-paste to view",
                })
            } else if executor.command_exists("xclip") {
                Some(ClipboardCommand {
                    binary: "xclip",
                    args: &["-selection", "clipboard"],
                    paste_hint: "xclip -o -selection clipboard to view",
                })
            } else {
                None
            }
        }
        Platform::Windows => Some(ClipboardCommand {
            binary: "clip",
            args: &[],
            paste_hint: "paste to view",
        }),
        Platform::Unknown => None,
    }
}
