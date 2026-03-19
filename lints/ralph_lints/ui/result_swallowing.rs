// Test: forbid_result_swallowing
#![allow(dropping_copy_types)]

fn might_fail() -> Result<i32, ()> {
    Ok(42)
}

fn main() {
    let _ = might_fail(); //~ ERROR `let _ =` discards Result
    might_fail().ok(); //~ ERROR `.ok()` discards Result
    if let Err(_) = might_fail() {} //~ ERROR `if let Err(_)` discards Result
}
