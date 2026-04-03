//! Boundary layer for dylint: wires pure decision logic to runtime capabilities.

use std::path::PathBuf;
use std::process::ExitCode;

use crate::runtime::dylint;

/// Run dylint with proper environment setup.
///
/// This function coordinates:
/// 1. Environment discovery
/// 2. Toolchain bootstrapping (installing rustup/nightly if needed)
/// 3. Wrapper script creation
/// 4. Delegation to runtime execution
///
/// # Arguments
///
/// * `verbose` - Enable verbose output
/// * `package` - The package to lint (e.g., "ralph-workflow", "mcp-server")
pub fn run_dylint(verbose: bool, package: &str) -> ExitCode {
    let dylint_env = dylint::discover_env();
    let dylint_env = resolve_env_paths(dylint_env, verbose);

    if !validate_env(&dylint_env) {
        return ExitCode::from(1);
    }

    if verbose {
        print_env_debug(&dylint_env);
    }

    if !validate_directories(&dylint_env) {
        return ExitCode::from(1);
    }

    let Some(toolchain) = bootstrap_toolchain(&dylint_env, verbose) else {
        return ExitCode::from(1);
    };

    let Some((wrapper_path, path_env)) = setup_wrapper(&dylint_env, &toolchain) else {
        return ExitCode::from(1);
    };

    if !ensure_cargo_dylint(&path_env, &toolchain, &dylint_env, verbose) {
        return ExitCode::from(1);
    }

    let result = dylint::execute_dylint(
        &wrapper_path,
        &dylint_env,
        &toolchain,
        &path_env,
        verbose,
        package,
    )
    .unwrap_or_else(|_| ExitCode::from(1));

    // Clean up wrapper directory
    if let Some(parent) = wrapper_path.parent() {
        let _ = std::fs::remove_dir_all(parent);
    }

    result
}

/// Resolve environment paths, converting relative to absolute.
fn resolve_env_paths(mut env: dylint::DylintEnv, verbose: bool) -> dylint::DylintEnv {
    let home = std::env::var("HOME").unwrap_or_default();
    env.dylint_driver = dylint::resolve_driver_path(env.dylint_driver.clone(), &home, verbose);
    env
}

/// Validate that required environment variables are set.
fn validate_env(env: &dylint::DylintEnv) -> bool {
    if env.cargo_home.is_empty() {
        eprintln!(
            "error: HOME is not set and CARGO_HOME is not set.\n\
             Set HOME, or set CARGO_HOME and RUSTUP_HOME to writable locations."
        );
        return false;
    }
    if env.rustup_home.is_empty() {
        eprintln!(
            "error: HOME is not set and RUSTUP_HOME is not set.\n\
             Set HOME, or set RUSTUP_HOME to a writable location."
        );
        return false;
    }
    if env.dylint_driver.is_empty() {
        eprintln!(
            "error: HOME is not set and DYLINT_DRIVER_PATH is not set.\n\
             Set HOME, or set DYLINT_DRIVER_PATH to a writable location."
        );
        return false;
    }
    true
}

/// Print environment debug information.
fn print_env_debug(env: &dylint::DylintEnv) {
    eprintln!("Running dylint with:");
    eprintln!("  CARGO_HOME: {}", env.cargo_home);
    eprintln!("  RUSTUP_HOME: {}", env.rustup_home);
    eprintln!("  DYLINT_DRIVER_PATH: {}", env.dylint_driver);
    eprintln!("  DYLINT_FORCE_OFFLINE: {}", env.force_offline);
}

/// Validate that directories are accessible and writable.
fn validate_directories(env: &dylint::DylintEnv) -> bool {
    let cargo_home_path = std::path::Path::new(&env.cargo_home);
    let rustup_home_path = std::path::Path::new(&env.rustup_home);
    let dylint_driver_path = std::path::Path::new(&env.dylint_driver);

    let cargo_home_writable = dylint::is_writable(cargo_home_path);

    if !cargo_home_path.exists() && !cargo_home_writable {
        eprintln!(
            "error: cannot access cargo home: {}\n\
             Set CARGO_HOME to an existing readable location.",
            env.cargo_home
        );
        return false;
    }

    let rustup_home_writable = dylint::is_writable(rustup_home_path);

    if !rustup_home_path.exists() && !rustup_home_writable {
        eprintln!(
            "error: cannot access rustup home: {}\n\
             Set RUSTUP_HOME to an existing readable location.",
            env.rustup_home
        );
        return false;
    }

    if !dylint_driver_path.exists() {
        if let Err(e) = std::fs::create_dir_all(&env.dylint_driver) {
            eprintln!(
                "error: cannot create required directory: {}\n\
                 Set DYLINT_DRIVER_PATH to a writable location.\n\
                 Details: {}",
                env.dylint_driver, e
            );
            return false;
        }
    }

    if !dylint::is_writable(dylint_driver_path) {
        eprintln!(
            "error: required directory is not writable: {}\n\
             Set DYLINT_DRIVER_PATH to a writable location.",
            env.dylint_driver
        );
        return false;
    }

    true
}

/// Bootstrap toolchain: install rustup, nightly, and components if needed.
fn bootstrap_toolchain(env: &dylint::DylintEnv, verbose: bool) -> Option<dylint::ToolchainInfo> {
    let rustup_home_writable = dylint::is_writable(std::path::Path::new(&env.rustup_home));
    let cargo_home_writable = dylint::is_writable(std::path::Path::new(&env.cargo_home));

    if !dylint::rustup_exists()
        && bootstrap_rustup(cargo_home_writable, rustup_home_writable).is_none()
    {
        return None;
    }

    let nightly_toolchain = dylint::discover_nightly_toolchain()?;

    let nightly_toolchain = ensure_nightly(&nightly_toolchain, rustup_home_writable, verbose)?;

    dylint::add_host_target(&nightly_toolchain);

    let () = ensure_components(&nightly_toolchain, rustup_home_writable)?;

    let (nightly_cargo, nightly_rustc) = dylint::resolve_nightly_paths(&nightly_toolchain)?;

    if nightly_cargo.is_empty() || nightly_rustc.is_empty() {
        eprintln!("error: could not resolve nightly toolchain paths");
        return None;
    }

    Some(dylint::ToolchainInfo {
        nightly_toolchain,
        nightly_cargo,
        nightly_rustc,
    })
}

/// Bootstrap rustup if not installed.
fn bootstrap_rustup(cargo_home_writable: bool, rustup_home_writable: bool) -> Option<()> {
    if !cargo_home_writable {
        eprintln!(
            "error: rustup is not installed and CARGO_HOME is not writable: {}\n\
             Set CARGO_HOME to a writable location or preinstall rustup.",
            std::env::var("CARGO_HOME").unwrap_or_default()
        );
        return None;
    }
    if !rustup_home_writable {
        eprintln!(
            "error: rustup is not installed and RUSTUP_HOME is not writable: {}\n\
             Set RUSTUP_HOME to a writable location or preinstall rustup.",
            std::env::var("RUSTUP_HOME").unwrap_or_default()
        );
        return None;
    }

    eprintln!("rustup not found; installing rustup (required for nightly + rustc-dev)...");

    let success = dylint::install_rustup().unwrap_or(false);
    if !success {
        eprintln!("error: failed to install rustup");
        return None;
    }

    Some(())
}

/// Ensure nightly toolchain is installed.
fn ensure_nightly(
    nightly_toolchain: &str,
    rustup_home_writable: bool,
    verbose: bool,
) -> Option<String> {
    if dylint::discover_nightly_toolchain().is_some() {
        return Some(nightly_toolchain.to_string());
    }

    if !rustup_home_writable {
        eprintln!(
            "error: nightly toolchain is missing and RUSTUP_HOME is not writable: {}\n\
             Set RUSTUP_HOME to a writable location or preinstall nightly.",
            std::env::var("RUSTUP_HOME").unwrap_or_default()
        );
        return None;
    }

    if verbose {
        eprintln!("Installing Rust nightly toolchain (required for dylint driver builds)...");
    }

    let success = dylint::install_nightly_toolchain().unwrap_or(false);
    if !success {
        eprintln!(
            "error: failed to install nightly toolchain.\n\
             If you are offline, pre-provision nightly:\n\
             rustup toolchain install nightly --profile minimal"
        );
        return None;
    }

    dylint::discover_nightly_toolchain()
}

/// Ensure required components are installed.
fn ensure_components(nightly_toolchain: &str, rustup_home_writable: bool) -> Option<()> {
    let Ok(output) = dylint::install_nightly_components(nightly_toolchain) else {
        if !rustup_home_writable {
            eprintln!(
                "error: required nightly component(s) missing and RUSTUP_HOME is not writable: {}\n\
                 Set RUSTUP_HOME to a writable location or preinstall the missing components.",
                std::env::var("RUSTUP_HOME").unwrap_or_default()
            );
            return None;
        }
        return None;
    };

    if !output {
        eprintln!(
            "error: failed to install required nightly component(s).\n\
             Provision them ahead of time (offline/sandboxed):\n\
             rustup component add rustc-dev llvm-tools-preview --toolchain {}",
            nightly_toolchain
        );
        return None;
    }

    Some(())
}

/// Setup wrapper script for cargo.
fn setup_wrapper(
    env: &dylint::DylintEnv,
    toolchain: &dylint::ToolchainInfo,
) -> Option<(PathBuf, String)> {
    let wrapper_dir = std::env::temp_dir().join(format!("dylint-wrapper-{}", std::process::id()));

    let wrapper_path = dylint::create_cargo_wrapper(
        &wrapper_dir,
        &toolchain.nightly_toolchain,
        &toolchain.nightly_cargo,
    )
    .ok()?;

    let nightly_bin_dir = std::path::Path::new(&toolchain.nightly_cargo)
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_default();

    let cargo_bin_str = std::path::Path::new(&env.cargo_home)
        .join("bin")
        .to_string_lossy()
        .to_string();

    let path_env = std::env::var("PATH")
        .map(|existing| {
            format!(
                "{}:{}:{}:{}",
                wrapper_path.to_string_lossy(),
                nightly_bin_dir.to_string_lossy(),
                cargo_bin_str,
                existing
            )
        })
        .unwrap_or_else(|_| {
            format!(
                "{}:{}:{}",
                wrapper_path.to_string_lossy(),
                nightly_bin_dir.to_string_lossy(),
                cargo_bin_str
            )
        });

    Some((wrapper_path, path_env))
}

/// Ensure cargo-dylint is installed.
fn ensure_cargo_dylint(
    path_env: &str,
    toolchain: &dylint::ToolchainInfo,
    env: &dylint::DylintEnv,
    verbose: bool,
) -> bool {
    if dylint::cargo_dylint_installed(path_env, &toolchain.nightly_toolchain, &env.cargo_home) {
        return true;
    }

    let cargo_home_writable = dylint::is_writable(std::path::Path::new(&env.cargo_home));
    if !cargo_home_writable {
        eprintln!(
            "error: cargo-dylint is not installed and CARGO_HOME is not writable: {}\n\
             Set CARGO_HOME to a writable location or preinstall cargo-dylint.",
            env.cargo_home
        );
        return false;
    }

    if verbose {
        eprintln!("Installing cargo-dylint (and dylint-link)...");
    }

    let success =
        dylint::install_cargo_dylint(path_env, &env.cargo_home, &toolchain.nightly_toolchain)
            .ok()
            .unwrap_or(false);

    if !success {
        eprintln!(
            "error: failed to install cargo-dylint.\n\
             If you are offline, preinstall it into {}/bin.\n\
             cargo install cargo-dylint dylint-link",
            env.cargo_home
        );
        return false;
    }

    true
}
