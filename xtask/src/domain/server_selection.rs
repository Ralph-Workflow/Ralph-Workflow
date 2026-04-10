//! Pure domain functions for build server selection policy.
//!
//! This module contains the load-based server selection algorithm. No I/O, no
//! process spawning, no environment access.

const TOLERANCE: f32 = 0.1;

/// Select the preferred build server given a slice of `(name, load)` pairs.
///
/// - If no server is reachable (`load` is `None` for all), returns `None`.
/// - If exactly one server is reachable, returns that server.
/// - If multiple servers are reachable, returns the one with the lowest
///   1-minute load average.  When loads are within [`TOLERANCE`] of each
///   other, they are treated as equivalent and `tiebreak_nanos` determines
///   the choice: even → first, odd → second.
///
/// The `tiebreak_nanos` parameter is the sub-second nanosecond component of
/// the current time, supplied by the caller to keep this function pure.
pub fn pick_server<'a>(loads: &[(&'a str, Option<f32>)], tiebreak_nanos: u32) -> Option<&'a str> {
    let reachable: Vec<(&str, f32)> = loads
        .iter()
        .filter_map(|&(name, load)| load.map(|l| (name, l)))
        .collect();
    match reachable.as_slice() {
        [] => None,
        [(name, _)] => Some(name),
        [(name_a, load_a), (name_b, load_b), ..] => {
            Some(select_two(name_a, *load_a, name_b, *load_b, tiebreak_nanos))
        }
    }
}

fn select_two<'a>(
    name_a: &'a str,
    load_a: f32,
    name_b: &'a str,
    load_b: f32,
    tiebreak_nanos: u32,
) -> &'a str {
    if (load_a - load_b).abs() < TOLERANCE {
        if tiebreak_nanos.is_multiple_of(2) {
            name_a
        } else {
            name_b
        }
    } else if load_a < load_b {
        name_a
    } else {
        name_b
    }
}

#[cfg(test)]
mod tests {
    use super::pick_server;

    #[test]
    fn none_when_all_unreachable() {
        let loads = [("rw-build-server", None), ("rw-build-server-2", None)];
        assert_eq!(pick_server(&loads, 0), None);
    }

    #[test]
    fn returns_only_reachable_server() {
        let loads = [("rw-build-server", None), ("rw-build-server-2", Some(1.5))];
        assert_eq!(pick_server(&loads, 0), Some("rw-build-server-2"));
    }

    #[test]
    fn returns_only_reachable_server_first_position() {
        let loads = [("rw-build-server", Some(0.8)), ("rw-build-server-2", None)];
        assert_eq!(pick_server(&loads, 0), Some("rw-build-server"));
    }

    #[test]
    fn prefers_lower_load() {
        let loads = [
            ("rw-build-server", Some(3.0)),
            ("rw-build-server-2", Some(1.0)),
        ];
        assert_eq!(pick_server(&loads, 0), Some("rw-build-server-2"));
    }

    #[test]
    fn reverse_order_prefers_lower_load() {
        let loads = [
            ("rw-build-server", Some(1.0)),
            ("rw-build-server-2", Some(3.0)),
        ];
        assert_eq!(pick_server(&loads, 0), Some("rw-build-server"));
    }

    #[test]
    fn within_tolerance_even_nanos_picks_first() {
        let loads = [
            ("rw-build-server", Some(1.0)),
            ("rw-build-server-2", Some(1.05)),
        ];
        assert_eq!(pick_server(&loads, 0), Some("rw-build-server"));
    }

    #[test]
    fn within_tolerance_odd_nanos_picks_second() {
        let loads = [
            ("rw-build-server", Some(1.0)),
            ("rw-build-server-2", Some(1.05)),
        ];
        assert_eq!(pick_server(&loads, 1), Some("rw-build-server-2"));
    }

    #[test]
    fn empty_slice_returns_none() {
        assert_eq!(pick_server(&[], 0), None);
    }
}
