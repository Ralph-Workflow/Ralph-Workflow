/// Controls whether Ralph pauses for input before process exit.
#[derive(clap::ValueEnum, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum PauseOnExitMode {
    /// Pause on failure only when launch appears standalone (double-click Explorer launch).
    #[default]
    Auto,
    /// Always pause before exit, regardless of outcome.
    Always,
    /// Never pause before exit.
    Never,
}
