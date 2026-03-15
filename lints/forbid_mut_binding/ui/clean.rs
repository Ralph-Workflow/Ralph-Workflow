// No `let mut` — should produce no warnings.
fn main() {
    let x = 42;
    let _y = x;
}
