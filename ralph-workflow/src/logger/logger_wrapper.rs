use crate::json_parser::printer::Printable;
use crate::logger::Logger;
use std::io::Write;

use crate::logger::stdout_writer::{stdout_flush, stdout_is_terminal, stdout_write};

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

impl Write for LoggerIoWrapper {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        stdout_write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        stdout_flush()
    }
}

impl Printable for LoggerIoWrapper {
    fn is_terminal(&self) -> bool {
        stdout_is_terminal()
    }
}
