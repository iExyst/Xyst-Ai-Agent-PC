#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::Write,
    net::TcpListener,
    net::TcpStream,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};

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

fn tray_image() -> Image<'static> {
    const SIZE: u32 = 32;
    let mut pixels = vec![0; (SIZE * SIZE * 4) as usize];
    for y in 0..SIZE {
        for x in 0..SIZE {
            if x.abs_diff(y) <= 2 || x.abs_diff(SIZE - 1 - y) <= 2 {
                let start = ((y * SIZE + x) * 4) as usize;
                pixels[start..start + 4].copy_from_slice(&[142, 219, 196, 255]);
            }
        }
    }
    Image::new_owned(pixels, SIZE, SIZE)
}

fn install_system_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Xyst AI Agent", true, None::<&str>)?;
    let quit = MenuItem::with_id(
        app,
        "quit",
        "Quit and stop the agent",
        true,
        None::<&str>,
    )?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    let tray = TrayIconBuilder::new()
        .icon(tray_image())
        .tooltip("Xyst AI Agent is running in the background")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => {
                let _ = restore_main_window(app.clone());
            }
            "quit" => quit_application(app.clone()),
            _ => {}
        })
        .build(app)?;
    let _ = app.manage(tray);
    Ok(())
}

#[tauri::command]
fn restore_main_window(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "The main window is unavailable".to_string())?;
    window
        .show()
        .map_err(|_| "The main window could not be restored".to_string())?;
    window
        .set_focus()
        .map_err(|_| "The main window could not receive focus".to_string())
}

#[tauri::command]
fn quit_application(app: tauri::AppHandle) {
    app.exit(0);
}

fn selected_log_directory(
    hidden_acceptance: bool,
    requested: Option<PathBuf>,
    default: PathBuf,
) -> PathBuf {
    if hidden_acceptance {
        if let Some(path) = requested {
            return path;
        }
    }
    default
}

#[cfg(test)]
mod tests {
    use super::{configure_engine_environment, tray_image};
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

    #[test]
    fn acceptance_log_override_is_ignored_for_visible_application() {
        let default = std::path::PathBuf::from("default-log-dir");
        let requested = std::path::PathBuf::from("acceptance-log-dir");
        assert_eq!(
            super::selected_log_directory(false, Some(requested.clone()), default.clone()),
            default
        );
        assert_eq!(
            super::selected_log_directory(true, Some(requested.clone()), default),
            requested
        );
    }

    #[test]
    fn tray_icon_is_a_transparent_rgba_x_mark() {
        let icon = tray_image();

        assert_eq!((icon.width(), icon.height()), (32, 32));
        assert_eq!(icon.rgba().len(), 32 * 32 * 4);
        assert_eq!(icon.rgba()[(10 * 32) * 4 + 3], 0);
        assert_eq!(icon.rgba()[(16 * 32 + 16) * 4 + 3], 255);
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
            install_system_tray(app)?;
            let listener = TcpListener::bind("127.0.0.1:0")?;
            let port = listener.local_addr()?.port();
            let token = uuid::Uuid::new_v4().simple().to_string();
            let mut executable = std::env::current_exe()?.with_file_name("xaa-engine.exe");
            #[cfg(debug_assertions)]
            if !executable.exists() {
                executable = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("binaries/xaa-engine-x86_64-pc-windows-msvc.exe");
            }
            let logs = selected_log_directory(
                std::env::var("XAA_TEST_HIDDEN").as_deref() == Ok("1"),
                std::env::var_os("XAA_TEST_LOG_DIR").map(PathBuf::from),
                app.path().app_log_dir()?,
            );
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
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            engine_connection,
            restore_main_window,
            quit_application
        ])
        .build(tauri::generate_context!())
        .expect("X.A.A. desktop failed to start")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                app.state::<EngineState>().stop();
            }
        });
}
