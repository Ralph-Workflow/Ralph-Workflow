/// Read raw bytes from a path, returning `None` if the file does not exist or
/// cannot be read.
#[must_use]
pub fn read_file_bytes(path: &std::path::Path) -> Option<Vec<u8>> {
    std::fs::read(path).ok()
}
