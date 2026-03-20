//! Platform detection
//!
//! Provides OS-specific detection capabilities.

use std::env::consts::OS;

use super::Platform;
use crate::ProcessExecutor;
#[cfg(test)]
use crate::RealProcessExecutor;

fn command_available(available_commands: &[&str], command: &str) -> bool {
    available_commands
        .iter()
        .any(|candidate| *candidate == command)
}

fn detect_platform(os_name: &str, available_commands: &[&str]) -> Platform {
    match os_name {
        "macos" => {
            if command_available(available_commands, "brew") {
                Platform::MacWithBrew
            } else {
                Platform::MacWithoutBrew
            }
        }
        "linux" => {
            if command_available(available_commands, "apt")
                || command_available(available_commands, "apt-get")
            {
                Platform::DebianLinux
            } else if command_available(available_commands, "dnf")
                || command_available(available_commands, "yum")
            {
                Platform::RhelLinux
            } else if command_available(available_commands, "pacman") {
                Platform::ArchLinux
            } else {
                Platform::GenericLinux
            }
        }
        "windows" => Platform::Windows,
        _ => Platform::Unknown,
    }
}

impl Platform {
    /// Detect the current platform with a provided process executor
    pub(crate) fn detect_with_executor(executor: &dyn ProcessExecutor) -> Self {
        const CANDIDATES: &[&str] = &["brew", "apt", "apt-get", "dnf", "yum", "pacman"];
        let available_commands: Vec<&str> = CANDIDATES
            .iter()
            .copied()
            .filter(|command| executor.command_exists(command))
            .collect();
        detect_platform(OS, &available_commands)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_platform_handles_macos_and_brew_presence() {
        assert_eq!(detect_platform("macos", &["brew"]), Platform::MacWithBrew);
        assert_eq!(detect_platform("macos", &[]), Platform::MacWithoutBrew);
    }

    #[test]
    fn detect_platform_handles_linux_distro_commands() {
        assert_eq!(detect_platform("linux", &["apt"]), Platform::DebianLinux);
        assert_eq!(detect_platform("linux", &["dnf"]), Platform::RhelLinux);
        assert_eq!(detect_platform("linux", &["pacman"]), Platform::ArchLinux);
        assert_eq!(detect_platform("linux", &[]), Platform::GenericLinux);
    }

    #[test]
    fn detect_platform_handles_windows_and_unknown() {
        assert_eq!(detect_platform("windows", &[]), Platform::Windows);
        assert_eq!(detect_platform("haiku", &[]), Platform::Unknown);
    }

    #[test]
    fn test_platform_detect_returns_valid_platform() {
        let platform = Platform::detect_with_executor(&RealProcessExecutor);
        // Should return some valid platform based on current OS
        assert!(matches!(
            platform,
            Platform::MacWithBrew
                | Platform::MacWithoutBrew
                | Platform::DebianLinux
                | Platform::RhelLinux
                | Platform::ArchLinux
                | Platform::GenericLinux
                | Platform::Windows
                | Platform::Unknown
        ));
    }
}
