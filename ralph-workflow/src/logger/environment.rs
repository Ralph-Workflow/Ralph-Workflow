use std::io::IsTerminal;

pub trait ColorEnvironment {
    fn get_var(&self, name: &str) -> Option<String>;
    fn is_terminal(&self) -> bool;
}

pub struct RealColorEnvironment;

impl ColorEnvironment for RealColorEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn is_terminal(&self) -> bool {
        std::io::stdout().is_terminal()
    }
}
