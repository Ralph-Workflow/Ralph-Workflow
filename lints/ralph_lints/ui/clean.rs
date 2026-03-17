// A file with no lint violations
fn main() {
    let x = 42;
    let result = Some(x).map(|n| n * 2);
    let _ = result;
}
