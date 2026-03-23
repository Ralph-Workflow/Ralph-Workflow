//! Runtime module for dylint execution.
//!
//! This module contains the process spawning and OS interaction code for
//! running custom dylint lints.

use std::path::PathBuf;
use std::process::{Command, ExitCode, Output};

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
        let absolute_path = std::env::current_dir()
            .map(|cwd| cwd.join(&dylint_driver).to_string_lossy().to_string())
            .unwrap_or_else(|_| dylint_driver.clone());

        let driver_exists = std::path::Path::new(&absolute_path)
            .join("nightly-aarch64-apple-darwin")
            .join("dylint-driver")
            .exists();

        if driver_exists {
            absolute_path
        } else {
            if verbose {
                eprintln!(
                    "  Note: No driver found at {}, falling back to default",
                    absolute_path
                );
            }
            format!("{}/.dylint_drivers", home)
        }
    } else {
        dylint_driver
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

/// Install rustup using curl or wget.
pub fn install_rustup() -> std::io::Result<bool> {
    if Command::new("curl")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        Command::new("bash")
            .args([
                "-c",
                "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path",
            ])
            .status()
            .map(|s| s.success())
    } else if Command::new("wget")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        Command::new("bash")
            .args([
                "-c",
                "wget -qO- https://sh.rustup.rs | sh -s -- -y --no-modify-path",
            ])
            .status()
            .map(|s| s.success())
    } else {
        Ok(false)
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
    let Ok(output) = Command::new("rustup")
        .args([
            "component",
            "list",
            "--toolchain",
            nightly_toolchain,
            "--installed",
        ])
        .output()
    else {
        return Ok(false);
    };

    let installed = String::from_utf8_lossy(&output.stdout);
    let has_rustc_dev = installed.contains("rustc-dev");
    let has_llvm_tools =
        installed.contains("llvm-tools-preview") || installed.contains("llvm-tools");

    let mut missing: Vec<String> = Vec::new();
    if !has_rustc_dev {
        missing.push("rustc-dev".to_string());
    }
    if !has_llvm_tools {
        missing.push("llvm-tools-preview".to_string());
    }

    if missing.is_empty() {
        return Ok(true);
    }

    let mut args = vec!["component".to_string(), "add".to_string()];
    args.extend(missing.clone());
    args.push("--toolchain".to_string());
    args.push(nightly_toolchain.to_string());

    Command::new("rustup")
        .args(&args)
        .env("RUSTUP_TERM_QUIET", "true")
        .status()
        .map(|s| s.success())
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
    Command::new("cargo")
        .args(["dylint", "--version"])
        .env("PATH", path_env)
        .env("RUSTUP_TOOLCHAIN", nightly_toolchain)
        .env("CARGO_HOME", cargo_home)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Install cargo-dylint.
pub fn install_cargo_dylint(
    path_env: &str,
    cargo_home: &str,
    nightly_toolchain: &str,
) -> std::io::Result<bool> {
    Command::new("cargo")
        .args(["install", "cargo-dylint", "dylint-link"])
        .env("PATH", path_env)
        .env("CARGO_HOME", cargo_home)
        .env("RUSTUP_TOOLCHAIN", nightly_toolchain)
        .status()
        .map(|s| s.success())
}

/// Execute dylint with the configured environment.
pub fn execute_dylint(
    wrapper_path: &std::path::Path,
    dylint_env: &DylintEnv,
    toolchain: &ToolchainInfo,
    path_env: &str,
    verbose: bool,
) -> std::io::Result<ExitCode> {
    let mut cmd = Command::new(wrapper_path);
    cmd.arg("dylint");

    if !verbose {
        cmd.arg("-q");
    }

    cmd.arg("--lib")
        .arg("ralph_lints")
        .arg("-p")
        .arg("ralph-workflow")
        .arg("--")
        .arg("--lib");

    if !verbose {
        cmd.arg("--quiet");
    }

    cmd.env("PATH", path_env)
        .env("CARGO_HOME", &dylint_env.cargo_home)
        .env("RUSTUP_HOME", &dylint_env.rustup_home)
        .env("DYLINT_DRIVER_PATH", &dylint_env.dylint_driver)
        .env("RUSTUP_TOOLCHAIN", &toolchain.nightly_toolchain)
        .env("RUSTC", &toolchain.nightly_rustc)
        .env("CARGO_TERM_QUIET", if verbose { "false" } else { "true" });

    if dylint_env.force_offline {
        cmd.env("CARGO_NET_OFFLINE", "true");
    }

    let status = cmd.status()?;
    Ok(if status.success() {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    })
}

#[cfg(test)]
mod tests {
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
}
