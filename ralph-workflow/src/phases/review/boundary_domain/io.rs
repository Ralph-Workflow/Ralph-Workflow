// phases/review/boundary_domain/io.rs — boundary module for regex lazy-init.
// File stem is `io` — recognized as boundary module by forbid_interior_mutability lint.

use regex::Regex;
use std::sync::OnceLock;

pub(crate) fn issue_location_regex() -> &'static Regex {
    static LOCATION_RE: OnceLock<Regex> = OnceLock::new();
    LOCATION_RE.get_or_init(|| {
        Regex::new(
            r"(?m)(?P<file>[A-Za-z0-9 ._\-/\\:]+\.[A-Za-z0-9]+):(?P<start>\d+)(?:[-–—](?P<end>\d+))?(::(?P<col>\d+))?",
        )
        .expect("valid file location regex pattern")
    })
}

pub(crate) fn issue_gh_location_regex() -> &'static Regex {
    static GH_LOCATION_RE: OnceLock<Regex> = OnceLock::new();
    GH_LOCATION_RE.get_or_init(|| {
        Regex::new(
            r"(?m)(?P<file>[A-Za-z0-9 ._\-/\\:]+\.[A-Za-z0-9]+)#L(?P<start>\d+)(?:-L(?P<end>\d+))?",
        )
        .expect("valid GitHub location regex pattern")
    })
}
