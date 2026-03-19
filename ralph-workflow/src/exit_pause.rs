use crate::cli::PauseOnExitMode;
use crate::io::terminal::{TerminalInput, TerminalOutput};

pub trait EnvironmentReader: Send {
    fn var_os(&self, key: &str) -> Option<std::ffi::OsString>;
}

pub struct StdEnvironment;

impl EnvironmentReader for StdEnvironment {
    fn var_os(&self, key: &str) -> Option<std::ffi::OsString> {
        std::env::var_os(key)
    }
}

pub trait ProcessSpawner: Send {
    fn spawn(&self, program: &str, args: &[&str]) -> Option<std::process::Output>;
}

pub struct StdProcessSpawner;

impl ProcessSpawner for StdProcessSpawner {
    fn spawn(&self, program: &str, args: &[&str]) -> Option<std::process::Output> {
        std::process::Command::new(program).args(args).output().ok()
    }
}

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
pub fn detect_launch_context_with(
    env: impl EnvironmentReader,
    spawner: impl ProcessSpawner,
) -> LaunchContext {
    LaunchContext {
        is_windows: cfg!(windows),
        has_terminal_session_marker: has_terminal_session_marker_with(&env),
        parent_process_name: detect_parent_process_name_with(spawner),
    }
}

pub fn pause_for_enter() -> std::io::Result<()> {
    crate::io::terminal::pause_for_enter_with(std::io::stdin(), std::io::stderr())
}

pub fn pause_for_enter_with(
    input: impl TerminalInput,
    output: impl TerminalOutput,
) -> std::io::Result<()> {
    crate::io::terminal::pause_for_enter_with(input, output)
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

fn has_terminal_session_marker_with(env: &impl EnvironmentReader) -> bool {
    const TERMINAL_MARKERS: [&str; 7] = [
        "WT_SESSION",
        "TERM",
        "MSYSTEM",
        "ConEmuPID",
        "ALACRITTY_LOG",
        "TERM_PROGRAM",
        "VSCODE_GIT_IPC_HANDLE",
    ];

    TERMINAL_MARKERS.iter().copied().any(|key| {
        env.var_os(key)
            .is_some_and(|value| !value.to_string_lossy().trim().is_empty())
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
fn detect_parent_process_name_with(spawner: impl ProcessSpawner) -> Option<String> {
    let script = format!(
        "$p=(Get-CimInstance Win32_Process -Filter \"ProcessId = {}\").ParentProcessId; if ($p) {{ (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName }}",
        std::process::id()
    );

    let output = spawner.spawn(
        "powershell",
        &["-NoProfile", "-NonInteractive", "-Command", &script],
    )?;

    if !output.status.success() {
        return None;
    }

    let name = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!name.is_empty()).then_some(name)
}

#[cfg(not(windows))]
fn detect_parent_process_name_with(_spawner: impl ProcessSpawner) -> Option<String> {
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

    struct MockEnv {
        vars: std::collections::HashMap<String, std::ffi::OsString>,
    }

    impl MockEnv {
        fn new() -> Self {
            Self {
                vars: std::collections::HashMap::new(),
            }
        }

        fn with_var(self, key: &str, value: &str) -> Self {
            Self {
                vars: self
                    .vars
                    .into_iter()
                    .chain([(key.to_string(), value.into())])
                    .collect(),
            }
        }
    }

    impl EnvironmentReader for MockEnv {
        fn var_os(&self, key: &str) -> Option<std::ffi::OsString> {
            self.vars.get(key).cloned()
        }
    }

    struct MockSpawner {
        output: Option<std::process::Output>,
    }

    impl MockSpawner {
        fn no_output() -> Self {
            Self { output: None }
        }
    }

    impl ProcessSpawner for MockSpawner {
        fn spawn(&self, _program: &str, _args: &[&str]) -> Option<std::process::Output> {
            self.output.clone()
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

    #[test]
    fn test_terminal_marker_detection_with_mock_env() {
        let env = MockEnv::new().with_var("TERM", "xterm-256color");
        assert!(has_terminal_session_marker_with(&env));

        let env_no_marker = MockEnv::new();
        assert!(!has_terminal_session_marker_with(&env_no_marker));
    }

    #[test]
    fn test_launch_context_with_deps() {
        let env = MockEnv::new().with_var("TERM", "xterm");
        let spawner = MockSpawner::no_output();

        let context = detect_launch_context_with(env, spawner);
        assert!(context.has_terminal_session_marker);
    }
}
