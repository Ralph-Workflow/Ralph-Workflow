//! Terminal I/O operations.
//!
//! This module contains terminal input/output operations that require direct
//! std::io access. As a boundary module, it is exempt from functional programming lints.

use crate::logger::Logger;

pub fn prompt_yes_no(logger: &Logger, prompt_text: &str) -> bool {
    use std::io::{self, Write};

    logger.info("");
    logger.info(prompt_text);

    let prompt = "Resume? [y/N] ";
    let colors = crate::logger::Colors::new();

    let mut input = String::new();
    let _ = io::stdout().write_all(format!("{}{}", colors.yellow(), prompt).as_bytes());
    let _ = io::stdout().flush();
    let _ = io::stdout().write_all(colors.reset().as_bytes());

    match io::stdin().read_line(&mut input) {
        Ok(0) => {
            logger.info("");
            false
        }
        Ok(_) => {
            let response = input.trim().to_lowercase();
            logger.info("");

            matches!(response.as_str(), "y" | "yes" | "Y" | "YES")
        }
        Err(_) => false,
    }
}
