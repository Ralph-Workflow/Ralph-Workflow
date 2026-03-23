fn retry_with_counter() {
    let mut attempt = 0;
    let max_attempts = 3;

    loop {
        attempt += 1;
        //~ ERROR retry loop with effect call and counter is forbidden in boundary modules
        let _ = std::fs::read_to_string("config.toml");
        if attempt >= max_attempts {
            break;
        }
    }
}

fn retry_while() {
    let mut attempts = 0;
    //~ ERROR retry loop with effect call and counter is forbidden in boundary modules
    while attempts < 3 {
        attempts += 1;
        let _ = std::env::var("FOO");
    }
}

fn retry_for() {
    for attempt in 0..3 {
        //~ ERROR retry loop with effect call and counter is forbidden in boundary modules
        let _ = std::process::Command::new("echo").spawn();
        if attempt == 2 {
            break;
        }
    }
}

fn retry_with_increment() {
    let mut retry_count = 0;
    loop {
        retry_count = retry_count + 1;
        //~ ERROR retry loop with effect call and counter is forbidden in boundary modules
        let _ = std::net::TcpStream::connect("localhost:8080");
        if retry_count > 5 {
            break;
        }
    }
}

fn retry_max_comparison() {
    let mut counter = 0;
    let max_retries = 10;
    loop {
        counter += 1;
        //~ ERROR retry loop with effect call and counter is forbidden in boundary modules
        let _ = tokio::time::sleep(std::time::Duration::from_secs(1));
        if counter >= max_retries {
            break;
        }
    }
}

fn boundary_loop_without_retry() {
    loop {
        //~ ERROR loops in boundary modules require a retry policy helper
        let _ = std::fs::read_to_string("config.toml");
    }
}

fn domain_retry_loop() {
    let mut attempt = 0;
    loop {
        attempt += 1;
        let _ = std::fs::read_to_string("config.toml");
        if attempt > 3 {
            break;
        }
    }
}

fn effect_no_retry_counter() {
    loop {
        let _ = std::fs::read_to_string("config.toml");
        break;
    }
}
