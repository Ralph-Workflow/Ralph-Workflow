//! Platform detection and installation guidance
//!
//! Provides OS-specific detection for platform-dependent behavior.
mod detection;

/// Detected platform type
///
/// This enum is `pub(crate)` because it is only used internally by
/// platform-specific helpers (e.g. clipboard handling). External code
/// should rely on higher-level helpers rather than matching on platform.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    /// macOS with Homebrew available
    MacWithBrew,
    /// macOS without Homebrew
    MacWithoutBrew,
    /// Debian/Ubuntu Linux (apt-based)
    DebianLinux,
    /// RHEL/Fedora Linux (dnf-based)
    RhelLinux,
    /// Arch Linux (pacman-based)
    ArchLinux,
    /// Generic Linux (unknown package manager)
    GenericLinux,
    /// Windows
    Windows,
    /// Unknown platform
    Unknown,
}
