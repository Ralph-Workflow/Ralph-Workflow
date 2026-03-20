use crate::executor::ProcessOutput;

pub fn is_success(output: &ProcessOutput) -> bool {
    output.status.success()
}
