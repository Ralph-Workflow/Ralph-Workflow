//! Runtime module for dylint execution.
//!
//! This module contains the process spawning and OS interaction code for
//! running custom dylint lints.

use std::path::PathBuf;
use std::process::{Command, ExitCode, Output};

use serde::Deserialize;

const LINT_CRATE_SUFFIX: &str = "_lints";

#[derive(Debug, Deserialize)]
struct CargoMetadata {
    packages: Vec<CargoPackage>,
    workspace_members: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct CargoPackage {
    id: String,
    name: String,
}

/// Environment configuration for dylint execution.
#[derive(Debug, Clone)]
pub struct DylintEnv {
    pub cargo_home: String,
    pub rustup_home: String,
    pub dylint_driver: String,
    pub force_offline: bool,
}

/// Toolchain information discovered at runtime.
#[derive(Debug, Clone)]
pub struct ToolchainInfo {
    pub nightly_toolchain: String,
    pub nightly_cargo: String,
    pub nightly_rustc: String,
}

/// Result of checking if a path is writable.
pub fn is_writable(path: &std::path::Path) -> bool {
    if !path.exists() {
        let Some(parent) = path.parent() else {
            return false;
        };
        return parent.exists() && is_writable(parent);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(path)
            .map(|m| (m.permissions().mode() & 0o200) != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        std::fs::OpenOptions::new()
            .write(true)
            .append(true)
            .open(path)
            .is_ok()
    }
}

/// Discover environment variables with fallbacks.
pub fn discover_env() -> DylintEnv {
    let home = std::env::var("HOME").unwrap_or_default();
    let cargo_home = std::env::var("CARGO_HOME").unwrap_or_else(|_| format!("{}/.cargo", home));
    let rustup_home = std::env::var("RUSTUP_HOME").unwrap_or_else(|_| format!("{}/.rustup", home));
    let dylint_driver =
        std::env::var("DYLINT_DRIVER_PATH").unwrap_or_else(|_| format!("{}/.dylint_drivers", home));
    let force_offline = std::env::var("DYLINT_FORCE_OFFLINE").unwrap_or_default() == "1";

    DylintEnv {
        cargo_home,
        rustup_home,
        dylint_driver,
        force_offline,
    }
}

/// Resolve dylint driver path, converting relative to absolute.
pub fn resolve_driver_path(dylint_driver: String, home: &str, verbose: bool) -> String {
    if std::path::Path::new(&dylint_driver).is_relative() {
        resolve_relative_driver(&dylint_driver, home, verbose)
    } else {
        dylint_driver
    }
}

fn resolve_relative_driver(dylint_driver: &str, home: &str, verbose: bool) -> String {
    let absolute_path = std::env::current_dir()
        .map(|cwd| cwd.join(dylint_driver).to_string_lossy().to_string())
        .unwrap_or_else(|_| dylint_driver.to_string());

    if driver_path_contains_driver(&absolute_path) {
        absolute_path
    } else {
        log_missing_driver(&absolute_path, verbose);
        format!("{}/.dylint_drivers", home)
    }
}

fn driver_path_contains_driver(candidate: &str) -> bool {
    std::path::Path::new(candidate)
        .join("nightly-aarch64-apple-darwin")
        .join("dylint-driver")
        .exists()
}

fn log_missing_driver(path: &str, verbose: bool) {
    if verbose {
        eprintln!(
            "  Note: No driver found at {}, falling back to default",
            path
        );
    }
}

/// Check if rustup is available.
pub fn rustup_exists() -> bool {
    Command::new("rustup")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Discover the nightly toolchain name.
pub fn discover_nightly_toolchain() -> Option<String> {
    // Prefer the active toolchain as resolved by rust-toolchain.toml.
    // This ensures the pinned nightly version is used when one is specified.
    if let Some(active) = active_nightly_toolchain() {
        return Some(active);
    }

    // Fall back to scanning the installed toolchain list.
    let output = Command::new("rustup")
        .args(["toolchain", "list"])
        .output()
        .ok()?;

    let output_str = String::from_utf8_lossy(&output.stdout);
    if !output_str.contains("nightly") {
        return Some("nightly".to_string());
    }

    output_str
        .lines()
        .find(|l| l.contains("nightly"))
        .map(|l| l.split_whitespace().next().unwrap_or("nightly").to_string())
}

fn active_nightly_toolchain() -> Option<String> {
    let output = Command::new("rustup")
        .args(["show", "active-toolchain"])
        .output()
        .ok()?;
    let s = String::from_utf8_lossy(&output.stdout);
    let toolchain = s.split_whitespace().next()?;
    toolchain.contains("nightly").then(|| toolchain.to_string())
}

/// Install rustup using curl or wget.
pub fn install_rustup() -> std::io::Result<bool> {
    attempt_install(
        "curl",
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path",
    )
    .or_else(|| {
        attempt_install(
            "wget",
            "wget -qO- https://sh.rustup.rs | sh -s -- -y --no-modify-path",
        )
    })
    .unwrap_or(Ok(false))
}

fn attempt_install(command: &str, script: &str) -> Option<std::io::Result<bool>> {
    if Command::new(command)
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        Some(
            Command::new("bash")
                .args(["-c", script])
                .status()
                .map(|s| s.success()),
        )
    } else {
        None
    }
}

/// Install nightly toolchain.
pub fn install_nightly_toolchain() -> std::io::Result<bool> {
    Command::new("rustup")
        .args(["toolchain", "install", "nightly", "--profile", "minimal"])
        .status()
        .map(|s| s.success())
}

/// Add target for host platform.
pub fn add_host_target(nightly_toolchain: &str) {
    let Ok(output) = Command::new("rustup")
        .args(["run", nightly_toolchain, "rustc", "-vV"])
        .output()
    else {
        return;
    };

    let output_str = String::from_utf8_lossy(&output.stdout);
    let Some(host_line) = output_str.lines().find(|l| l.starts_with("host:")) else {
        return;
    };
    let host = host_line.trim_start_matches("host:").trim();
    let _ = Command::new("rustup")
        .args(["target", "add", host, "--toolchain", nightly_toolchain])
        .output();
}

/// Install required nightly components.
pub fn install_nightly_components(nightly_toolchain: &str) -> std::io::Result<bool> {
    let Some(installed) = query_installed_components(nightly_toolchain)? else {
        return Ok(false);
    };

    let missing = missing_components(&installed);
    if missing.is_empty() {
        return Ok(true);
    }

    install_missing_components(nightly_toolchain, &missing)
}

fn query_installed_components(nightly_toolchain: &str) -> std::io::Result<Option<String>> {
    let output = Command::new("rustup")
        .args([
            "component",
            "list",
            "--toolchain",
            nightly_toolchain,
            "--installed",
        ])
        .output()?;

    if !output.status.success() {
        return Ok(None);
    }

    Ok(Some(String::from_utf8_lossy(&output.stdout).to_string()))
}

fn missing_components(installed: &str) -> Vec<&'static str> {
    let mut missing = Vec::new();
    if !installed.contains("rustc-dev") {
        missing.push("rustc-dev");
    }
    if !installed.contains("llvm-tools-preview") && !installed.contains("llvm-tools") {
        missing.push("llvm-tools-preview");
    }
    missing
}

fn install_missing_components(nightly_toolchain: &str, missing: &[&str]) -> std::io::Result<bool> {
    let args = build_component_args(nightly_toolchain, missing);
    Command::new("rustup")
        .args(&args)
        .env("RUSTUP_TERM_QUIET", "true")
        .status()
        .map(|s| s.success())
}

fn build_component_args(nightly_toolchain: &str, missing: &[&str]) -> Vec<String> {
    let mut args = vec!["component".to_string(), "add".to_string()];
    args.extend(missing.iter().map(|m| m.to_string()));
    args.push("--toolchain".to_string());
    args.push(nightly_toolchain.to_string());
    args
}

/// Resolve nightly cargo and rustc paths.
pub fn resolve_nightly_paths(nightly_toolchain: &str) -> Option<(String, String)> {
    let cargo = Command::new("rustup")
        .args(["which", "cargo", "--toolchain", nightly_toolchain])
        .output()
        .ok()
        .and_then(successful_output_path)?;

    let rustc = Command::new("rustup")
        .args(["which", "rustc", "--toolchain", nightly_toolchain])
        .output()
        .ok()
        .and_then(successful_output_path)?;

    Some((cargo, rustc))
}

fn successful_output_path(output: Output) -> Option<String> {
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_string())
}

/// Create cargo wrapper script for nightly toolchain.
pub fn create_cargo_wrapper(
    wrapper_dir: &std::path::Path,
    nightly_toolchain: &str,
    nightly_cargo: &str,
) -> std::io::Result<PathBuf> {
    std::fs::create_dir_all(wrapper_dir)?;

    let wrapper_script = format!(
        "#!/usr/bin/env bash\nexport RUSTUP_TOOLCHAIN=\"{}\"\nexec \"{}\" \"$@\"",
        nightly_toolchain, nightly_cargo
    );

    let wrapper_path = wrapper_dir.join("cargo");
    std::fs::write(&wrapper_path, wrapper_script)?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&wrapper_path, std::fs::Permissions::from_mode(0o755))?;
    }

    Ok(wrapper_path)
}

/// Check if cargo-dylint is installed.
pub fn cargo_dylint_installed(path_env: &str, nightly_toolchain: &str, cargo_home: &str) -> bool {
    let output = Command::new("cargo")
        .args(["dylint", "--version"])
        .env("PATH", path_env)
        .env("RUSTUP_TOOLCHAIN", nightly_toolchain)
        .env("CARGO_HOME", cargo_home)
        .output();
    matches!(output, Ok(ref o) if o.status.success()
        && String::from_utf8_lossy(&o.stdout).contains(CARGO_DYLINT_VERSION))
}

/// Pinned cargo-dylint / dylint-link version.
///
/// This must match the version installed on developer machines.  Changing it
/// here without updating local installs will cause a mismatch between the lint
/// binary format and the driver, producing cryptic link errors.
pub const CARGO_DYLINT_VERSION: &str = "3.5.1";

/// Install cargo-dylint.
pub fn install_cargo_dylint(
    path_env: &str,
    cargo_home: &str,
    nightly_toolchain: &str,
) -> std::io::Result<bool> {
    Command::new("cargo")
        .args([
            "install",
            "cargo-dylint",
            "dylint-link",
            "--version",
            CARGO_DYLINT_VERSION,
        ])
        .env("PATH", path_env)
        .env("CARGO_HOME", cargo_home)
        .env("RUSTUP_TOOLCHAIN", nightly_toolchain)
        .status()
        .map(|s| s.success())
}

fn read_workspace_package_names(cargo_bin: &std::path::Path) -> std::io::Result<Vec<String>> {
    let metadata_output = Command::new(cargo_bin)
        .args(["metadata", "--no-deps", "--format-version", "1"])
        .output()?;

    if !metadata_output.status.success() {
        return Err(std::io::Error::other(format!(
            "cargo metadata failed: {}",
            String::from_utf8_lossy(&metadata_output.stderr)
        )));
    }

    let metadata: CargoMetadata = serde_json::from_slice(&metadata_output.stdout)
        .map_err(|error| std::io::Error::other(format!("invalid cargo metadata JSON: {error}")))?;

    let name_by_id = metadata
        .packages
        .into_iter()
        .map(|package| (package.id, package.name))
        .collect::<std::collections::HashMap<_, _>>();

    metadata
        .workspace_members
        .into_iter()
        .map(|member_id| {
            name_by_id.get(&member_id).cloned().ok_or_else(|| {
                std::io::Error::other(format!(
                    "workspace member '{member_id}' missing from cargo metadata package list"
                ))
            })
        })
        .collect()
}

fn build_dylint_package_args(package_names: &[String]) -> Vec<String> {
    let mut filtered_names = package_names
        .iter()
        .filter(|name| !name.ends_with(LINT_CRATE_SUFFIX))
        .cloned()
        .collect::<Vec<_>>();

    filtered_names.sort();
    filtered_names.dedup();

    filtered_names
        .into_iter()
        .flat_map(|name| ["-p".to_string(), name])
        .collect()
}

/// Execute dylint with the configured environment.
pub fn execute_dylint(
    wrapper_path: &std::path::Path,
    dylint_env: &DylintEnv,
    toolchain: &ToolchainInfo,
    path_env: &str,
    verbose: bool,
) -> std::io::Result<ExitCode> {
    let package_names = collect_target_packages(wrapper_path)?;
    if run_dylint_packages(
        wrapper_path,
        dylint_env,
        toolchain,
        path_env,
        verbose,
        &package_names,
    )? {
        Ok(ExitCode::SUCCESS)
    } else {
        Ok(ExitCode::from(1))
    }
}

fn extract_package_names(package_args: &[String]) -> Vec<String> {
    package_args.iter().skip(1).step_by(2).cloned().collect()
}

fn collect_target_packages(wrapper_path: &std::path::Path) -> std::io::Result<Vec<String>> {
    let workspace_packages = read_workspace_package_names(wrapper_path)?;
    let package_args = build_dylint_package_args(&workspace_packages);
    let package_names = extract_package_names(&package_args);

    if package_names.is_empty() {
        return Err(std::io::Error::other(
            "no workspace packages available for dylint after lint-crate exclusion",
        ));
    }

    Ok(package_names)
}

fn run_dylint_packages(
    wrapper_path: &std::path::Path,
    dylint_env: &DylintEnv,
    toolchain: &ToolchainInfo,
    path_env: &str,
    verbose: bool,
    package_names: &[String],
) -> std::io::Result<bool> {
    for package_name in package_names {
        if !run_dylint_for_package(
            wrapper_path,
            dylint_env,
            toolchain,
            path_env,
            verbose,
            package_name,
        )? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn run_dylint_for_package(
    wrapper_path: &std::path::Path,
    dylint_env: &DylintEnv,
    toolchain: &ToolchainInfo,
    path_env: &str,
    verbose: bool,
    package_name: &str,
) -> std::io::Result<bool> {
    let mut cmd = Command::new(wrapper_path);
    cmd.arg("dylint");
    if !verbose {
        cmd.arg("-q");
    }
    configure_package_args(&mut cmd, package_name, verbose);
    configure_dylint_envs(&mut cmd, dylint_env, toolchain, path_env, verbose);
    let status = cmd.status()?;
    Ok(status.success())
}

fn configure_package_args(cmd: &mut Command, package_name: &str, verbose: bool) {
    cmd.arg("--lib")
        .arg("ralph_lints")
        .arg("-p")
        .arg(package_name);
    if package_name == "ralph-workflow" {
        cmd.arg("--").arg("--lib");
        if !verbose {
            cmd.arg("--quiet");
        }
    }
}

fn configure_dylint_envs(
    cmd: &mut Command,
    dylint_env: &DylintEnv,
    toolchain: &ToolchainInfo,
    path_env: &str,
    verbose: bool,
) {
    cmd.env("RUSTFLAGS", "--cap-lints=deny -D warnings")
        .env("PATH", path_env)
        .env("CARGO_HOME", &dylint_env.cargo_home)
        .env("RUSTUP_HOME", &dylint_env.rustup_home)
        .env("DYLINT_DRIVER_PATH", &dylint_env.dylint_driver)
        .env("RUSTUP_TOOLCHAIN", &toolchain.nightly_toolchain)
        .env("RUSTC", &toolchain.nightly_rustc)
        .env("CARGO_TERM_QUIET", if verbose { "false" } else { "true" });

    if dylint_env.force_offline {
        cmd.env("CARGO_NET_OFFLINE", "true");
    }
}

#[cfg(test)]
mod tests {
    use super::build_dylint_package_args;
    use std::process::Output;

    #[cfg(unix)]
    use std::os::unix::process::ExitStatusExt;

    use super::successful_output_path;

    #[test]
    fn successful_output_path_trims_stdout() {
        let output = Output {
            status: std::process::ExitStatus::from_raw(0),
            stdout: b" /tmp/nightly/cargo\n".to_vec(),
            stderr: Vec::new(),
        };

        assert_eq!(
            successful_output_path(output),
            Some("/tmp/nightly/cargo".to_string())
        );
    }

    #[test]
    fn failed_output_path_returns_none() {
        let output = Output {
            status: std::process::ExitStatus::from_raw(1),
            stdout: b"/tmp/nightly/cargo\n".to_vec(),
            stderr: Vec::new(),
        };

        assert_eq!(successful_output_path(output), None);
    }

    #[test]
    fn build_dylint_package_args_targets_each_workspace_package_except_lint_crate() {
        let package_names = vec![
            "ralph-workflow".to_string(),
            "test-helpers".to_string(),
            "xtask".to_string(),
            "tests".to_string(),
            "ralph_lints".to_string(),
        ];

        let args = build_dylint_package_args(&package_names);

        assert_eq!(
            args,
            vec![
                "-p",
                "ralph-workflow",
                "-p",
                "test-helpers",
                "-p",
                "tests",
                "-p",
                "xtask",
            ],
            "dylint should target all workspace packages except lint crates"
        );
    }

    #[test]
    fn build_dylint_package_args_omits_lint_crates_when_only_lints_are_present() {
        let package_names = vec!["ralph_lints".to_string(), "foo_lints".to_string()];

        let args = build_dylint_package_args(&package_names);

        assert!(
            args.is_empty(),
            "lint crates should never be linted directly"
        );
    }
}
