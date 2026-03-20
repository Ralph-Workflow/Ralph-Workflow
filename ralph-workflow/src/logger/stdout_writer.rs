pub fn stdout_write_line(msg: &str) -> std::io::Result<()> {
    crate::logger::runtime::stdout_write_line(msg)
}

pub fn stderr_write_line(msg: &str) -> std::io::Result<()> {
    crate::logger::runtime::stderr_write_line(msg)
}
