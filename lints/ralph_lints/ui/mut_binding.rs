// Test: forbid_mut_binding
#![allow(dropping_copy_types)]

fn main() {
    let mut x = 42; //~ ERROR `let mut x` is forbidden
    x += 1;
    drop(x);
}
