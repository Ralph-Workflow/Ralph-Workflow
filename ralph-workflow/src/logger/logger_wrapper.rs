use crate::logger::Logger;

pub struct LoggerIoWrapper {
    logger: Logger,
}

impl LoggerIoWrapper {
    pub fn new(logger: Logger) -> Self {
        Self { logger }
    }

    pub fn logger(&self) -> &Logger {
        &self.logger
    }

    pub fn into_inner(self) -> Logger {
        self.logger
    }
}

impl std::io::Write for LoggerIoWrapper {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        crate::logger::runtime::stdout_write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        crate::logger::runtime::stdout_flush()
    }
}

impl crate::json_parser::printer::Printable for LoggerIoWrapper {
    fn is_terminal(&self) -> bool {
        crate::logger::runtime::stdout_is_terminal()
    }
}
