use crate::logger::Logger;

fn parse_yes_no_response(input: String) -> bool {
    let response = input.trim().to_lowercase();
    matches!(response.as_str(), "y" | "yes" | "Y" | "YES")
}

fn write_prompt(logger: &Logger, prompt_text: &str) {
    use std::io::Write;
    logger.info("");
    logger.info(prompt_text);
    let prompt = "Resume? [y/N] ";
    let colors = crate::logger::Colors::new();
    let _ = io::stdout().write_all(format!("{}{}", colors.yellow(), prompt).as_bytes());
    let _ = io::stdout().flush();
    let _ = io::stdout().write_all(colors.reset().as_bytes());
}

pub fn prompt_yes_no(logger: &Logger, prompt_text: &str) -> bool {
    write_prompt(logger, prompt_text);
    let input = crate::app::io::effect_io::read_user_input();
    logger.info("");
    parse_yes_no_response(input)
}
