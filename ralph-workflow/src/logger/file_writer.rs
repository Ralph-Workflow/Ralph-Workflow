pub fn append_to_file(path: &str, msg: &str) -> std::io::Result<()> {
    crate::logger::io::append_to_file(path, msg)
}
