//! Pure dylint-related logic with no I/O or side effects.
//!
//! Message builders used by the boundary layer to produce human-readable
//! error messages without embedding format strings inline.

pub(crate) fn rustup_not_installed_cargo_error(cargo_home: &str) -> String {
    format!(
        "error: rustup is not installed and CARGO_HOME is not writable: {cargo_home}\n             Set CARGO_HOME to a writable location or preinstall rustup."
    )
}

pub(crate) fn rustup_not_installed_rustup_error(rustup_home: &str) -> String {
    format!(
        "error: rustup is not installed and RUSTUP_HOME is not writable: {rustup_home}\n             Set RUSTUP_HOME to a writable location or preinstall rustup."
    )
}

pub(crate) fn nightly_missing_not_writable_error(rustup_home: &str) -> String {
    format!(
        "error: nightly toolchain is missing and RUSTUP_HOME is not writable: {rustup_home}\n             Set RUSTUP_HOME to a writable location or preinstall nightly."
    )
}

pub(crate) fn nightly_install_failed_help(nightly_toolchain: &str) -> String {
    format!(
        "error: failed to install nightly toolchain.\n             If you are offline, pre-provision nightly:\n             rustup toolchain install {nightly_toolchain} --profile minimal"
    )
}

pub(crate) fn cargo_dylint_not_writable_error(cargo_home: &str) -> String {
    format!(
        "error: cargo-dylint is not installed and CARGO_HOME is not writable: {cargo_home}\n             Set CARGO_HOME to a writable location or preinstall cargo-dylint."
    )
}

pub(crate) fn cargo_dylint_install_failed_help(cargo_home: &str) -> String {
    format!(
        "error: failed to install cargo-dylint.\n             If you are offline, preinstall it into {cargo_home}/bin.\n             cargo install cargo-dylint dylint-link"
    )
}
