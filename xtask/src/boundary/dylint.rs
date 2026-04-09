//! Boundary layer for dylint: wires pure decision logic to runtime capabilities.

use std::path::PathBuf;
use std::process::ExitCode;

use crate::domain::dylint as domain_dylint;
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

    // Regenerate lints/ralph_lints/rustc-nightly as a thin shell wrapper that
    // calls the discovered nightly rustc by absolute path.  This file is
    // gitignored and platform-specific: syncing a macOS binary to a Linux
    // build server (or vice versa) would break the lint library build.
    let repo_root = std::env::current_dir().map_err(|_| failure_code())?;
    if !create_rustc_nightly_wrapper(&repo_root, &toolchain.nightly_rustc) {
        eprintln!("error: failed to write lints/ralph_lints/rustc-nightly wrapper");
        return Err(failure_code());
    }

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
    let cargo_home = std::path::Path::new(&env.cargo_home);
    let rustup_home = std::path::Path::new(&env.rustup_home);
    let driver = std::path::Path::new(&env.dylint_driver);
    ensure_directory_accessible(cargo_home, &env.cargo_home, "error: cannot access cargo home: {}\n             Set CARGO_HOME to an existing readable location.")
        && ensure_directory_accessible(rustup_home, &env.rustup_home, "error: cannot access rustup home: {}\n             Set RUSTUP_HOME to an existing readable location.")
        && ensure_dylint_driver_directory(driver, &env.dylint_driver)
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
    create_dir_if_missing(path, env_value) && check_dir_writable(path, env_value)
}

fn create_dir_if_missing(path: &std::path::Path, env_value: &str) -> bool {
    if path.exists() {
        return true;
    }
    std::fs::create_dir_all(env_value)
        .map_err(|e| eprintln!("error: cannot create required directory: {env_value}\n             Set DYLINT_DRIVER_PATH to a writable location.\n             Details: {e}"))
        .is_ok()
}

fn check_dir_writable(path: &std::path::Path, env_value: &str) -> bool {
    if dylint::is_writable(path) {
        return true;
    }
    eprintln!("error: required directory is not writable: {env_value}\n             Set DYLINT_DRIVER_PATH to a writable location.");
    false
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
    resolve_toolchain_info(&nightly_toolchain)
}

fn resolve_toolchain_info(nightly_toolchain: &str) -> Option<dylint::ToolchainInfo> {
    let (nightly_cargo, nightly_rustc) = dylint::resolve_nightly_paths(nightly_toolchain)?;
    if nightly_cargo.is_empty() || nightly_rustc.is_empty() {
        eprintln!("error: could not resolve nightly toolchain paths");
        return None;
    }
    Some(dylint::ToolchainInfo {
        nightly_toolchain: nightly_toolchain.to_string(),
        nightly_cargo,
        nightly_rustc,
    })
}

/// Bootstrap rustup if not installed.
fn bootstrap_rustup(cargo_home_writable: bool, rustup_home_writable: bool) -> Option<()> {
    check_rustup_install_preconditions(cargo_home_writable, rustup_home_writable)?;
    eprintln!("rustup not found; installing rustup (required for nightly + rustc-dev)...");
    let success = dylint::install_rustup().unwrap_or(false);
    if !success {
        eprintln!("error: failed to install rustup");
        return None;
    }
    Some(())
}

fn check_rustup_install_preconditions(
    cargo_home_writable: bool,
    rustup_home_writable: bool,
) -> Option<()> {
    if !cargo_home_writable {
        eprintln!(
            "{}",
            domain_dylint::rustup_not_installed_cargo_error(
                &std::env::var("CARGO_HOME").unwrap_or_default()
            )
        );
        return None;
    }
    if !rustup_home_writable {
        eprintln!(
            "{}",
            domain_dylint::rustup_not_installed_rustup_error(
                &std::env::var("RUSTUP_HOME").unwrap_or_default()
            )
        );
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
    install_nightly(nightly_toolchain, rustup_home_writable, verbose)
}

fn install_nightly(
    nightly_toolchain: &str,
    rustup_home_writable: bool,
    verbose: bool,
) -> Option<String> {
    check_nightly_install_precondition(rustup_home_writable)?;
    run_nightly_install(nightly_toolchain, verbose)
}

fn check_nightly_install_precondition(rustup_home_writable: bool) -> Option<()> {
    if !rustup_home_writable {
        eprintln!(
            "{}",
            domain_dylint::nightly_missing_not_writable_error(
                &std::env::var("RUSTUP_HOME").unwrap_or_default()
            )
        );
        return None;
    }
    Some(())
}

fn run_nightly_install(nightly_toolchain: &str, verbose: bool) -> Option<String> {
    if verbose {
        eprintln!("Installing Rust nightly toolchain (required for dylint driver builds)...");
    }
    let success = dylint::install_nightly_toolchain().unwrap_or(false);
    if !success {
        eprintln!(
            "{}",
            domain_dylint::nightly_install_failed_help(nightly_toolchain)
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
                wrapper_dir.to_string_lossy(),
                nightly_bin_dir.to_string_lossy(),
                cargo_bin_str,
                existing
            )
        })
        .unwrap_or_else(|_| {
            format!(
                "{}:{}:{}",
                wrapper_dir.to_string_lossy(),
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
    install_cargo_dylint(path_env, toolchain, env, verbose)
}

fn install_cargo_dylint(
    path_env: &str,
    toolchain: &dylint::ToolchainInfo,
    env: &dylint::DylintEnv,
    verbose: bool,
) -> bool {
    check_cargo_home_writable(&env.cargo_home)
        && run_cargo_dylint_install(path_env, toolchain, env, verbose)
}

fn check_cargo_home_writable(cargo_home: &str) -> bool {
    let writable = dylint::is_writable(std::path::Path::new(cargo_home));
    if !writable {
        eprintln!(
            "{}",
            domain_dylint::cargo_dylint_not_writable_error(cargo_home)
        );
    }
    writable
}

fn run_cargo_dylint_install(
    path_env: &str,
    toolchain: &dylint::ToolchainInfo,
    env: &dylint::DylintEnv,
    verbose: bool,
) -> bool {
    if verbose {
        eprintln!("Installing cargo-dylint (and dylint-link)...");
    }
    let success =
        dylint::install_cargo_dylint(path_env, &env.cargo_home, &toolchain.nightly_toolchain)
            .ok()
            .unwrap_or(false);
    if !success {
        eprintln!(
            "{}",
            domain_dylint::cargo_dylint_install_failed_help(&env.cargo_home)
        );
    }
    success
}

/// Write `lints/ralph_lints/rustc-nightly` as a thin shell script that delegates
/// to `nightly_rustc` by absolute path.
///
/// `lints/ralph_lints/.cargo/config.toml` sets `build.rustc = "./rustc-nightly"`.
/// `cargo-dylint` unsets `RUSTC` before the library build, so cargo falls back
/// to that config.  The committed file was a macOS ARM64 binary; we regenerate
/// it at runtime so the correct platform binary is always used.
fn create_rustc_nightly_wrapper(repo_root: &std::path::Path, nightly_rustc: &str) -> bool {
    let wrapper_path = repo_root.join("lints/ralph_lints/rustc-nightly");
    let script = format!("#!/usr/bin/env sh\nexec {nightly_rustc} \"$@\"\n");
    if std::fs::write(&wrapper_path, &script).is_err() {
        return false;
    }
    set_executable(&wrapper_path)
}

#[cfg(unix)]
fn set_executable(path: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).is_ok()
}

#[cfg(not(unix))]
fn set_executable(_path: &std::path::Path) -> bool {
    true
}
