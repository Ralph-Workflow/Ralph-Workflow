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
pub fn run_dylint(verbose: bool) -> ExitCode {
    run_dylint_inner(verbose).unwrap_or_else(|code| code)
}

fn run_dylint_inner(verbose: bool) -> Result<ExitCode, ExitCode> {
    let plan = prepare_dylint_plan(verbose)?;
    let result = execute_dylint_plan(&plan, verbose);
    cleanup_wrapper(&plan);
    Ok(result)
}

fn prepare_dylint_env(verbose: bool) -> Result<dylint::DylintEnv, ExitCode> {
    let env = resolve_env_paths(dylint::discover_env(), verbose);
    ensure_env_ready(&env)?;
    if verbose {
        print_env_debug(&env);
    }
    ensure_directories_ready(&env)?;
    Ok(env)
}

fn ensure_env_ready(env: &dylint::DylintEnv) -> Result<(), ExitCode> {
    if validate_env(env) {
        Ok(())
    } else {
        Err(failure_code())
    }
}

fn ensure_directories_ready(env: &dylint::DylintEnv) -> Result<(), ExitCode> {
    if validate_directories(env) {
        Ok(())
    } else {
        Err(failure_code())
    }
}

fn ensure_cargo_dylint_available(
    path_env: &str,
    toolchain: &dylint::ToolchainInfo,
    env: &dylint::DylintEnv,
    verbose: bool,
) -> Result<(), ExitCode> {
    if ensure_cargo_dylint(path_env, toolchain, env, verbose) {
        Ok(())
    } else {
        Err(failure_code())
    }
}

fn failure_code() -> ExitCode {
    ExitCode::from(1)
}

struct DylintPlan {
    dylint_env: dylint::DylintEnv,
    toolchain: dylint::ToolchainInfo,
    wrapper_path: PathBuf,
    path_env: String,
}

fn prepare_dylint_plan(verbose: bool) -> Result<DylintPlan, ExitCode> {
    let dylint_env = prepare_dylint_env(verbose)?;
    let toolchain = bootstrap_toolchain(&dylint_env, verbose).ok_or(failure_code())?;
    let (wrapper_path, path_env) = setup_wrapper(&dylint_env, &toolchain).ok_or(failure_code())?;

    ensure_cargo_dylint_available(&path_env, &toolchain, &dylint_env, verbose)?;

    Ok(DylintPlan {
        dylint_env,
        toolchain,
        wrapper_path,
        path_env,
    })
}

fn execute_dylint_plan(plan: &DylintPlan, verbose: bool) -> ExitCode {
    dylint::execute_dylint(
        &plan.wrapper_path,
        &plan.dylint_env,
        &plan.toolchain,
        &plan.path_env,
        verbose,
    )
    .unwrap_or_else(|_| failure_code())
}

fn cleanup_wrapper(plan: &DylintPlan) {
    if let Some(parent) = plan.wrapper_path.parent() {
        let _ = std::fs::remove_dir_all(parent);
    }
}

/// Resolve environment paths, converting relative to absolute.
fn resolve_env_paths(mut env: dylint::DylintEnv, verbose: bool) -> dylint::DylintEnv {
    let home = std::env::var("HOME").unwrap_or_default();
    env.dylint_driver = dylint::resolve_driver_path(env.dylint_driver.clone(), &home, verbose);
    env
}

/// Validate that required environment variables are set.
fn validate_env(env: &dylint::DylintEnv) -> bool {
    ensure_env_variable(&env.cargo_home, "error: HOME is not set and CARGO_HOME is not set.\n             Set HOME, or set CARGO_HOME and RUSTUP_HOME to writable locations.")
        && ensure_env_variable(&env.rustup_home, "error: HOME is not set and RUSTUP_HOME is not set.\n             Set HOME, or set RUSTUP_HOME to a writable location.")
        && ensure_env_variable(&env.dylint_driver, "error: HOME is not set and DYLINT_DRIVER_PATH is not set.\n             Set HOME, or set DYLINT_DRIVER_PATH to a writable location.")
}

fn ensure_env_variable(value: &str, message: &str) -> bool {
    if value.is_empty() {
        print_formatted_message(message, value);
        false
    } else {
        true
    }
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

    if !ensure_directory_accessible(
        cargo_home_path,
        &env.cargo_home,
        "error: cannot access cargo home: {}\n             Set CARGO_HOME to an existing readable location.",
    ) {
        return false;
    }

    if !ensure_directory_accessible(
        rustup_home_path,
        &env.rustup_home,
        "error: cannot access rustup home: {}\n             Set RUSTUP_HOME to an existing readable location.",
    ) {
        return false;
    }

    if !ensure_dylint_driver_directory(dylint_driver_path, &env.dylint_driver) {
        return false;
    }

    true
}

fn ensure_directory_accessible(path: &std::path::Path, env_value: &str, message: &str) -> bool {
    let writable = dylint::is_writable(path);
    if !path.exists() && !writable {
        print_formatted_message(message, env_value);
        return false;
    }
    true
}

fn ensure_dylint_driver_directory(path: &std::path::Path, env_value: &str) -> bool {
    if !path.exists() {
        if let Err(e) = std::fs::create_dir_all(env_value) {
            eprintln!(
                "error: cannot create required directory: {}\n             Set DYLINT_DRIVER_PATH to a writable location.\n             Details: {}",
                env_value, e
            );
            return false;
        }
    }

    if !dylint::is_writable(path) {
        eprintln!(
            "error: required directory is not writable: {}\n             Set DYLINT_DRIVER_PATH to a writable location.",
            env_value
        );
        return false;
    }

    true
}

fn print_formatted_message(template: &str, value: &str) {
    let message = template.replace("{}", value);
    eprintln!("{}", message);
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
