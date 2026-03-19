use crate::app::io::effect_io::{flush_stdout, write_stdout};
use crate::logger::Logger;

fn parse_yes_no_response(input: String) -> bool {
    let response = input.trim().to_lowercase();
    matches!(response.as_str(), "y" | "yes" | "Y" | "YES")
}

fn write_prompt(logger: &Logger, prompt_text: &str) {
    logger.info("");
    logger.info(prompt_text);
    let prompt = "Resume? [y/N] ";
    let colors = crate::logger::Colors::new();
    let _ = write_stdout(&format!("{}{}", colors.yellow(), prompt));
    let _ = flush_stdout();
    let _ = write_stdout(colors.reset());
}

pub fn prompt_yes_no(logger: &Logger, prompt_text: &str) -> bool {
    write_prompt(logger, prompt_text);
    let input = crate::app::io::effect_io::read_user_input();
    logger.info("");
    parse_yes_no_response(input)
}
