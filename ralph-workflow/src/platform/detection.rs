//! Platform detection
//!
//! Provides OS-specific detection capabilities.

use std::env::consts::OS;

use super::Platform;
use crate::executor::ProcessExecutor;
#[cfg(test)]
use crate::executor::RealProcessExecutor;
use crate::platform::io;

/// Detect Linux distribution based on available package managers
fn detect_linux_distro(executor: &dyn ProcessExecutor) -> Platform {
    // Check for package managers in order of specificity
    if io::command_exists(executor, "apt-get") || io::command_exists(executor, "apt") {
        Platform::DebianLinux
    } else if io::command_exists(executor, "dnf") || io::command_exists(executor, "yum") {
        Platform::RhelLinux
    } else if io::command_exists(executor, "pacman") {
        Platform::ArchLinux
    } else {
        Platform::GenericLinux
    }
}

impl Platform {
    /// Detect the current platform with a provided process executor
    pub(crate) fn detect_with_executor(executor: &dyn ProcessExecutor) -> Self {
        match OS {
            "macos" => {
                if io::command_exists(executor, "brew") {
                    Self::MacWithBrew
                } else {
                    Self::MacWithoutBrew
                }
            }
            "linux" => detect_linux_distro(executor),
            "windows" => Self::Windows,
            _ => Self::Unknown,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
