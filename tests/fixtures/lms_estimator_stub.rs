use std::env;
use std::fs;
use std::process;

fn json_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let executable = env::current_exe().expect("current executable path");
    let report_path = executable
        .parent()
        .expect("executable parent")
        .join("invocation.json");
    let serialized = args
        .iter()
        .map(|argument| json_string(argument))
        .collect::<Vec<_>>()
        .join(",");
    fs::write(report_path, format!("[{serialized}]\n")).expect("write invocation evidence");

    if args.len() != 7
        || args[0] != "load"
        || args[1] != "--estimate-only"
        || args[2] != "xaa-native-browser-cancel-fixture"
        || args[3] != "--host"
        || args[4] != "127.0.0.1"
        || args[5] != "--port"
        || args[6].parse::<u16>().is_err()
    {
        eprintln!("Unexpected LM Studio estimate-only arguments.");
        process::exit(23);
    }

    eprintln!("Model: xaa-native-browser-cancel-fixture");
    eprintln!("Estimated GPU Memory: 2.00 GiB");
    eprintln!("Estimated Total Memory: 3.00 GiB");
    eprintln!("Confidence: HIGH");
    eprintln!("Estimate: This model may be loaded based on your resource guardrails settings.");
}
