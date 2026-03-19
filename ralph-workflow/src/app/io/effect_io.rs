use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub fn set_current_dir(path: &Path) -> Result<(), io::Error> {
    std::env::set_current_dir(path)
}

pub fn write_file(path: &Path, content: String) -> Result<(), io::Error> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, content)
}

pub fn read_file(path: &Path) -> Result<String, io::Error> {
    fs::read_to_string(path)
}

pub fn delete_file(path: &Path) -> Result<(), io::Error> {
    fs::remove_file(path)
}

pub fn create_dir(path: &Path) -> Result<(), io::Error> {
    fs::create_dir_all(path)
}

pub fn path_exists(path: &Path) -> bool {
    path.exists()
}

pub fn set_read_only(path: &Path, readonly: bool) -> Result<(), io::Error> {
    let metadata = fs::metadata(path)?;
    let mut permissions = metadata.permissions();
    permissions.set_readonly(readonly);
    fs::set_permissions(path, permissions)
}

pub fn get_env_var(name: &str) -> Result<String, std::env::VarError> {
    std::env::var(name)
}

pub fn set_env_var(name: &str, value: &str) {
    std::env::set_var(name, value);
}

pub fn resolve_path(workspace_root: &Option<PathBuf>, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else if let Some(ref root) = workspace_root {
        root.join(path)
    } else {
        path.to_path_buf()
    }
}

pub fn check_no_resume_prompt() -> bool {
    std::env::var("RALPH_NO_RESUME_PROMPT").is_ok()
}

pub fn is_terminal_io() -> bool {
    std::io::stdin().is_terminal()
        && (std::io::stdout().is_terminal() || std::io::stderr().is_terminal())
}

pub fn get_current_dir() -> PathBuf {
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

pub fn get_args() -> Vec<String> {
    std::env::args().collect()
}

pub fn get_program_args() -> Vec<String> {
    std::env::args().skip(1).collect()
}

pub fn get_process_id() -> u32 {
    std::process::id()
}

pub fn exit_with_code(code: i32) -> ! {
    std::process::exit(code)
}
