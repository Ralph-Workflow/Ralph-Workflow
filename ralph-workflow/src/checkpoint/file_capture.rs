pub fn read_file_bytes(path: &std::path::Path) -> Option<Vec<u8>> {
    crate::checkpoint::io::file_capture::read_file_bytes(path)
}
