/// Result of parsing an environment variable with optional warnings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ParsedEnv<T> {
    pub(crate) value: Option<T>,
    pub(crate) warnings: Vec<String>,
}

impl<T> Default for ParsedEnv<T> {
    fn default() -> Self {
        Self {
            value: None,
            warnings: Vec::new(),
        }
    }
}

impl<T> ParsedEnv<T> {
    pub(crate) fn new(value: Option<T>) -> Self {
        Self {
            value,
            warnings: Vec::new(),
        }
    }

    pub(crate) fn with_warning(self, warning: impl Into<String>) -> Self {
        let new_warnings = self
            .warnings
            .into_iter()
            .chain(std::iter::once(warning.into()))
            .collect();
        Self {
            value: self.value,
            warnings: new_warnings,
        }
    }
}

/// Parse a u32 environment variable with validation.
pub(super) fn parse_env_u32(
    name: &str,
    get_env: impl Fn(&str) -> Option<String>,
    max: u32,
) -> ParsedEnv<u32> {
    let raw = match get_env(name) {
        Some(v) => v,
        None => return ParsedEnv::default(),
    };
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return ParsedEnv::default();
    }
    match trimmed.parse::<u32>() {
        Ok(n) if n <= max => ParsedEnv::new(Some(n)),
        Ok(n) => ParsedEnv::new(Some(max)).with_warning(format!(
            "Env var {name}={n} is too large; clamping to {max}."
        )),
        Err(_) => ParsedEnv::default().with_warning(format!(
            "Env var {name}='{trimmed}' is not a valid number; ignoring."
        )),
    }
}

/// Parse a u8 environment variable with validation.
pub(super) fn parse_env_u8(
    name: &str,
    get_env: impl Fn(&str) -> Option<String>,
    max: u8,
) -> ParsedEnv<u8> {
    let raw = match get_env(name) {
        Some(v) => v,
        None => return ParsedEnv::default(),
    };
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return ParsedEnv::default();
    }
    match trimmed.parse::<u8>() {
        Ok(n) if n <= max => ParsedEnv::new(Some(n)),
        Ok(n) => ParsedEnv::new(Some(max)).with_warning(format!(
            "Env var {name}={n} is out of range; clamping to {max}."
        )),
        Err(_) => ParsedEnv::default().with_warning(format!(
            "Env var {name}='{trimmed}' is not a valid number; ignoring."
        )),
    }
}
