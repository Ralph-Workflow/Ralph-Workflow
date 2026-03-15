// No &mut self method calls — should produce no warnings.
fn main() {
    let s = String::from("hello");
    let _len = s.len();
}
