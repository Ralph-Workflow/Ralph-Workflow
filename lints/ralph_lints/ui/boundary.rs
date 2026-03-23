fn run_everything(flag: bool, retry: bool) -> Result<(), std::io::Error> {
    let path = if flag { "a.txt" } else { "b.txt" };

    if retry {
        std::fs::write(path, "first")?;
    }

    let contents = std::fs::read_to_string(path)?;

    let output = match contents.trim() {
        "ok" => "done",
        "retry" => {
            std::fs::write(path, "second")?;
            "retried"
        }
        _ => "fallback",
    };

    std::fs::write(path, output)?;
    Ok(())
}

fn main() {
    let _ = run_everything(true, false);
}
