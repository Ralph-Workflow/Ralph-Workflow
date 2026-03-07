use crate::cli::PauseOnExitMode;
use std::io::Write;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitOutcome {
    Success,
    Failure,
    Interrupted,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchContext {
    pub is_windows: bool,
    pub has_terminal_session_marker: bool,
    pub parent_process_name: Option<String>,
}

#[must_use]
pub fn should_pause_before_exit(
    mode: PauseOnExitMode,
    outcome: ExitOutcome,
    launch_context: &LaunchContext,
) -> bool {
    match mode {
        PauseOnExitMode::Never => false,
        PauseOnExitMode::Always => true,
        PauseOnExitMode::Auto => {
            matches!(outcome, ExitOutcome::Failure)
                && is_probably_standalone_windows_launch(launch_context)
        }
    }
}

#[must_use]
pub fn detect_launch_context() -> LaunchContext {
    LaunchContext {
        is_windows: cfg!(windows),
        has_terminal_session_marker: has_terminal_session_marker(),
        parent_process_name: detect_parent_process_name(),
    }
}

/// Wait for user confirmation before closing the process.
///
/// # Errors
///
/// Returns an error when writing the prompt to stderr fails or when stdin cannot be read.
pub fn pause_for_enter() -> std::io::Result<()> {
    eprint!("\nPress Enter to close... ");
    std::io::stderr().flush()?;

    let mut line = String::new();
    std::io::stdin().read_line(&mut line)?;

    Ok(())
}

fn is_probably_standalone_windows_launch(launch_context: &LaunchContext) -> bool {
    if !launch_context.is_windows || launch_context.has_terminal_session_marker {
        return false;
    }

    launch_context
        .parent_process_name
        .as_deref()
        .is_some_and(|name| normalize_process_name(name) == "explorer.exe")
}

fn has_terminal_session_marker() -> bool {
    const TERMINAL_MARKERS: [&str; 7] = [
        "WT_SESSION",
        "TERM",
        "MSYSTEM",
        "ConEmuPID",
        "ALACRITTY_LOG",
        "TERM_PROGRAM",
        "VSCODE_GIT_IPC_HANDLE",
    ];

    TERMINAL_MARKERS.iter().any(|key| {
        std::env::var_os(key).is_some_and(|value| !value.to_string_lossy().trim().is_empty())
    })
}

fn normalize_process_name(name: &str) -> String {
    let normalized = name.trim().to_ascii_lowercase();
    if std::path::Path::new(&normalized)
        .extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("exe"))
    {
        normalized
    } else {
        format!("{normalized}.exe")
    }
}

#[cfg(windows)]
fn detect_parent_process_name() -> Option<String> {
    use std::process::Command;

    let script = format!(
        "$p=(Get-CimInstance Win32_Process -Filter \"ProcessId = {}\").ParentProcessId; if ($p) {{ (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName }}",
        std::process::id()
    );

    let output = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let name = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!name.is_empty()).then_some(name)
}

#[cfg(not(windows))]
const fn detect_parent_process_name() -> Option<String> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn windows_context(parent: Option<&str>, has_marker: bool) -> LaunchContext {
        LaunchContext {
            is_windows: true,
            has_terminal_session_marker: has_marker,
            parent_process_name: parent.map(ToString::to_string),
        }
    }

    #[test]
    fn test_auto_pauses_on_failure_when_launched_from_explorer() {
        let context = windows_context(Some("explorer.exe"), false);
        assert!(should_pause_before_exit(
            PauseOnExitMode::Auto,
            ExitOutcome::Failure,
            &context,
        ));
    }

    #[test]
    fn test_auto_does_not_pause_on_success() {
        let context = windows_context(Some("explorer.exe"), false);
        assert!(!should_pause_before_exit(
            PauseOnExitMode::Auto,
            ExitOutcome::Success,
            &context,
        ));
    }

    #[test]
    fn test_auto_does_not_pause_when_terminal_session_marker_exists() {
        let context = windows_context(Some("explorer.exe"), true);
        assert!(!should_pause_before_exit(
            PauseOnExitMode::Auto,
            ExitOutcome::Failure,
            &context,
        ));
    }

    #[test]
    fn test_auto_does_not_pause_on_non_windows() {
        let context = LaunchContext {
            is_windows: false,
            has_terminal_session_marker: false,
            parent_process_name: Some("explorer.exe".to_string()),
        };
        assert!(!should_pause_before_exit(
            PauseOnExitMode::Auto,
            ExitOutcome::Failure,
            &context,
        ));
    }

    #[test]
    fn test_always_pauses_even_on_success() {
        let context = windows_context(Some("cmd.exe"), true);
        assert!(should_pause_before_exit(
            PauseOnExitMode::Always,
            ExitOutcome::Success,
            &context,
        ));
    }

    #[test]
    fn test_never_never_pauses() {
        let context = windows_context(Some("explorer.exe"), false);
        assert!(!should_pause_before_exit(
            PauseOnExitMode::Never,
            ExitOutcome::Failure,
            &context,
        ));
    }

    #[test]
    fn test_auto_does_not_pause_on_interrupted() {
        let context = windows_context(Some("explorer.exe"), false);
        assert!(!should_pause_before_exit(
            PauseOnExitMode::Auto,
            ExitOutcome::Interrupted,
            &context,
        ));
    }
}
