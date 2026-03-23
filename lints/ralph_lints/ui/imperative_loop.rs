// Test: forbid_imperative_loops
fn main() {
    for i in 0..10 {
        //~ ERROR `for` loop is forbidden
        let _ = i;
    }
}
