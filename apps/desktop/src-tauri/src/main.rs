#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::Write,
    net::TcpListener,
    net::TcpStream,
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::Manager;

#[cfg(windows)]
mod confinement;

struct EngineState {
    child: Mutex<Child>,
    connection: Connection,
}

#[derive(Clone, serde::Serialize)]
struct Connection {
    base_url: String,
    token: String,
}

#[tauri::command]
fn engine_connection(state: tauri::State<'_, EngineState>) -> Result<Connection, String> {
    let mut child = state.child.lock().map_err(|_| "Engine state unavailable")?;
    if child
        .try_wait()
        .map_err(|_| "Cannot inspect engine")?
        .is_some()
    {
        return Err("The local engine stopped. Restart X.A.A. and inspect its logs.".into());
    }
    Ok(state.connection.clone())
}

fn configure_engine_environment(command: &mut Command, token: &str, port: u16) {
    command.env_clear();
    #[cfg(windows)]
    {
        for name in [
            "SystemRoot",
            "WINDIR",
            "LOCALAPPDATA",
            "APPDATA",
            "USERPROFILE",
            "TEMP",
            "TMP",
        ] {
            if let Some(value) = std::env::var_os(name) {
                command.env(name, value);
            }
        }
        let system_root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
        command.env(
            "PATH",
            std::path::PathBuf::from(system_root).join("System32"),
        );
    }
    command
        .env("XAA_API_TOKEN", token)
        .env("XAA_API_PORT", port.to_string())
        .env("XAA_STREAM_RESPONSES", "1");
}

#[cfg(test)]
mod tests {
    use super::configure_engine_environment;
    use std::process::Command;

    #[test]
    fn packaged_engine_launch_enables_streamed_responses() {
        let mut command = Command::new("xaa-engine.exe");
        configure_engine_environment(&mut command, "fixture-token", 8765);
        let streaming = command
            .get_envs()
            .find(|(name, _)| *name == "XAA_STREAM_RESPONSES")
            .map(|(_, value)| value);
        assert!(matches!(streaming, Some(Some(value)) if value.to_string_lossy() == "1"));
    }
}

impl EngineState {
    fn stop(&self) {
        if let Ok(mut child) = self.child.lock() {
            if child.try_wait().ok().flatten().is_some() {
                return;
            }
            if let Ok(address) = self
                .connection
                .base_url
                .trim_start_matches("http://")
                .parse()
            {
                if let Ok(mut stream) =
                    TcpStream::connect_timeout(&address, std::time::Duration::from_secs(1))
                {
                    let _ = stream.set_write_timeout(Some(std::time::Duration::from_secs(1)));
                    let _ = write!(stream, "POST /shutdown HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", self.connection.token);
                }
            }
            for _ in 0..60 {
                if child.try_wait().ok().flatten().is_some() {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                let system = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
                let _ =
                    Command::new(std::path::PathBuf::from(system).join("System32/taskkill.exe"))
                        .args(["/PID", &child.id().to_string(), "/T", "/F"])
                        .creation_flags(0x08000000)
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status();
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn main() {
    #[cfg(windows)]
    let _process_job =
        confinement::contain_process_tree().expect("Cannot contain the desktop process tree");
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let listener = TcpListener::bind("127.0.0.1:0")?;
            let port = listener.local_addr()?.port();
            let token = uuid::Uuid::new_v4().simple().to_string();
            let mut executable = std::env::current_exe()?.with_file_name("xaa-engine.exe");
            #[cfg(debug_assertions)]
            if !executable.exists() {
                executable = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("binaries/xaa-engine-x86_64-pc-windows-msvc.exe");
            }
            let logs = app.path().app_log_dir()?;
            std::fs::create_dir_all(&logs)?;
            let log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(logs.join("engine.log"))?;
            let mut command = Command::new(executable);
            configure_engine_environment(&mut command, &token, port);
            command.stdout(log.try_clone()?).stderr(log);
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x08000000);
            }
            drop(listener);
            let child = command.spawn()?;
            app.manage(EngineState {
                child: Mutex::new(child),
                connection: Connection {
                    base_url: format!("http://127.0.0.1:{port}"),
                    token,
                },
            });
            if std::env::var("XAA_TEST_HIDDEN").as_deref() != Ok("1") {
                if let Some(window) = app.get_webview_window("main") {
                    window.show()?;
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_connection])
        .build(tauri::generate_context!())
        .expect("X.A.A. desktop failed to start")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                app.state::<EngineState>().stop();
            }
        });
}
