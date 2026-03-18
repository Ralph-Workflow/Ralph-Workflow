pub static ANSI_RE: std::sync::LazyLock<Result<regex::Regex, regex::Error>> =
    std::sync::LazyLock::new(|| regex::Regex::new(r"\x1b\[[0-9;]*m"));
