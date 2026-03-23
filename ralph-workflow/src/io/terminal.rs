//! Terminal I/O boundary module.
//!
//! Handles terminal input/output operations. This is a boundary module
//! where mutation and imperative code are allowed.

use std::io::{Read, Write};

pub trait BannerOutput: Write + Send {}
impl<W: Write + Send> BannerOutput for W {}

pub trait TerminalOutput: Write + Send {}
impl<W: Write + Send> TerminalOutput for W {}

pub trait TerminalInput: Read + Send {}
impl<R: Read + Send> TerminalInput for R {}

pub fn write_banner_to<W: BannerOutput>(mut output: W, content: &str) -> std::io::Result<()> {
    output.write_all(content.as_bytes())
}

pub fn pause_for_enter_with<T: TerminalInput, W: TerminalOutput>(
    mut input: T,
    mut output: W,
) -> std::io::Result<()> {
    output.write_all(b"\nPress Enter to close... ")?;
    let mut buf = String::new();
    input.read_to_string(&mut buf)?;
    Ok(())
}
