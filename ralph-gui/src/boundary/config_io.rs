use std::path::{Path, PathBuf};

pub fn read_to_string(path: impl AsRef<Path>) -> std::io::Result<String> {
    std::fs::read_to_string(path)
}

pub fn write(path: impl AsRef<Path>, content: impl AsRef<[u8]>) -> std::io::Result<()> {
    std::fs::write(path, content)
}

pub fn create_dir_all(path: impl AsRef<Path>) -> std::io::Result<()> {
    std::fs::create_dir_all(path)
}

pub fn path_exists(path: impl AsRef<Path>) -> bool {
    path.as_ref().exists()
}

pub fn home_dir() -> Option<PathBuf> {
    dirs::home_dir()
}

pub fn env_var(name: &str) -> Option<String> {
    std::env::var(name).ok()
}

pub fn map_io_error<T>(result: std::io::Result<T>, context: &str) -> Result<T, String> {
    result.map_err(|error| format!("{context}: {error}"))
}

#[cfg(unix)]
pub fn set_permissions_mode(path: impl AsRef<Path>, mode: u32) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))
}
