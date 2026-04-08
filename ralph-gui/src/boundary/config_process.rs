pub struct ProcessOutput {
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
}

pub fn probe_binary_location(binary: &str) -> Option<String> {
    which::which(binary)
        .ok()
        .map(|path| path.to_string_lossy().to_string())
}

pub fn read_version_line(binary: &str) -> Option<String> {
    run_output(binary, &["--version"]).and_then(|output| {
        output.success.then_some(())?;
        output
            .stdout
            .lines()
            .next()
            .map(str::trim)
            .map(str::to_string)
    })
}

pub fn read_model_list(binary: &str) -> Option<Vec<String>> {
    run_output(binary, &["--list-models"]).and_then(|output| {
        output.success.then_some(())?;
        Some(
            output
                .stdout
                .lines()
                .map(str::trim)
                .filter(|line| !line.is_empty())
                .map(str::to_string)
                .collect(),
        )
    })
}

pub fn run_version_output(binary: &str) -> Result<ProcessOutput, std::io::Error> {
    std::process::Command::new(binary)
        .arg("--version")
        .output()
        .map(|output| ProcessOutput {
            success: output.status.success(),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        })
}

pub fn spawn_help(binary: &str) -> Result<(), String> {
    std::process::Command::new(binary)
        .arg("--help")
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("Failed to launch {binary}: {e}"))
}

pub fn spawn_install_command(cmd: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("osascript")
            .args([
                "-e",
                &format!("tell application \"Terminal\" to do script \"{cmd}\""),
            ])
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("Failed to open terminal: {e}"))
    }

    #[cfg(target_os = "linux")]
    {
        let primary = std::process::Command::new("x-terminal-emulator")
            .args(["-e", &format!("bash -c '{cmd}; exec bash'")])
            .spawn();

        if primary.is_ok() {
            return Ok(());
        }

        std::process::Command::new("gnome-terminal")
            .args(["--", "bash", "-c", &format!("{cmd}; exec bash")])
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("Failed to open terminal: {e}"))
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/c", "start", "cmd", "/k", cmd])
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("Failed to open terminal: {e}"))
    }
}

fn run_output(binary: &str, args: &[&str]) -> Option<ProcessOutput> {
    std::process::Command::new(binary)
        .args(args)
        .output()
        .ok()
        .map(|output| ProcessOutput {
            success: output.status.success(),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        })
}
