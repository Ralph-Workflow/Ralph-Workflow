use std::sync::LazyLock;

pub static ANSI_RE: LazyLock<Result<regex::Regex, regex::Error>> =
    LazyLock::new(|| regex::Regex::new(r"\x1b\[[0-9;]*m"));

#[must_use]
pub fn strip_ansi_codes(s: &str) -> String {
    ANSI_RE
        .as_ref()
        .map_or_else(|_| s.to_string(), |re| re.replace_all(s, "").to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strip_ansi_codes() {
        let input = "\x1b[31mred\x1b[0m text";
        assert_eq!(strip_ansi_codes(input), "red text");
    }

    #[test]
    fn test_strip_ansi_codes_no_codes() {
        let input = "plain text";
        assert_eq!(strip_ansi_codes(input), "plain text");
    }

    #[test]
    fn test_strip_ansi_codes_multiple() {
        let input = "\x1b[1m\x1b[32mbold green\x1b[0m \x1b[34mblue\x1b[0m";
        assert_eq!(strip_ansi_codes(input), "bold green blue");
    }
}
