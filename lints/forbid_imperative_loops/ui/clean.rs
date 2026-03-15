// No imperative loops — should produce no warnings.
fn main() {
    let data = vec![1, 2, 3];
    let _sum: i32 = data.iter().sum();
}
