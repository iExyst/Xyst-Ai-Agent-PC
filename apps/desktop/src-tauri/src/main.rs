#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs,
    io::Write,
    net::TcpListener,
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};

mod backup_export;

#[cfg(windows)]
mod autostart;
#[cfg(windows)]
mod confinement;

struct EngineState {
    child: Mutex<Child>,
    connection: Connection,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
enum CloseBehavior {
    #[default]
    KeepRunning,
    ExitOnClose,
}

#[derive(Default, serde::Deserialize, serde::Serialize)]
#[serde(default)]
struct DesktopSettings {
    close_behavior: CloseBehavior,
    active_data_root: Option<PathBuf>,
}

struct CloseBehaviorState {
    value: Mutex<CloseBehavior>,
    active_data_root: Mutex<Option<PathBuf>>,
    settings_path: PathBuf,
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
            "XAA_DATA_DIR",
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
    let quit = MenuItem::with_id(app, "quit", "Quit and stop the agent", true, None::<&str>)?;
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

fn active_data_root(state: &CloseBehaviorState) -> Option<PathBuf> {
    std::env::var_os("XAA_DATA_DIR")
        .map(PathBuf::from)
        .or_else(|| {
            state
                .active_data_root
                .lock()
                .ok()
                .and_then(|value| value.clone())
        })
}

#[tauri::command]
fn get_active_data_root(
    state: tauri::State<'_, CloseBehaviorState>,
) -> Result<Option<String>, String> {
    Ok(active_data_root(&state).map(|root| root.to_string_lossy().into_owned()))
}

fn application_backup_directory(state: &CloseBehaviorState) -> Result<PathBuf, String> {
    let configured_root = active_data_root(state);
    let data_directory = if let Some(root) = configured_root {
        root.join("XAA")
    } else if let Some(value) = std::env::var_os("LOCALAPPDATA") {
        PathBuf::from(value).join("XAA")
    } else if let Some(value) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))
    {
        PathBuf::from(value).join(".xaa")
    } else {
        return Err("The X.A.A. data directory is unavailable".into());
    };
    Ok(data_directory.join("backups"))
}

#[tauri::command]
fn export_backup_bundle(
    state: tauri::State<'_, CloseBehaviorState>,
    source_path: String,
    destination_path: String,
) -> Result<String, String> {
    let exported = backup_export::export_backup(
        Path::new(&source_path),
        Path::new(&destination_path),
        &application_backup_directory(&state)?,
    )?;
    Ok(exported.to_string_lossy().into_owned())
}

#[tauri::command]
fn activate_restored_profile(
    app: tauri::AppHandle,
    state: tauri::State<'_, CloseBehaviorState>,
    data_root: String,
) -> Result<(), String> {
    if std::env::var_os("XAA_DATA_DIR").is_some() {
        return Err(
            "X.A.A. was started with an external data directory and cannot switch profiles.".into(),
        );
    }
    let selected = PathBuf::from(data_root)
        .canonicalize()
        .map_err(|_| "The restored profile folder is unavailable".to_string())?;
    let profile_directory = selected.join("XAA");
    let database = profile_directory.join("xaa.sqlite3");
    if profile_directory.is_symlink() || !database.is_file() || database.is_symlink() {
        return Err("The selected folder does not contain a verified restored profile".into());
    }
    let current =
        active_data_root(&state).or_else(|| std::env::var_os("LOCALAPPDATA").map(PathBuf::from));
    if let Some(current) = current.and_then(|path| path.canonicalize().ok()) {
        if current == selected {
            return Err("This restored profile is already active".into());
        }
    }
    save_active_data_root(&state.settings_path, Some(selected.clone()))?;
    *state
        .active_data_root
        .lock()
        .map_err(|_| "Desktop settings are unavailable".to_string())? = Some(selected);
    app.restart();
}

#[tauri::command]
fn use_default_data_profile(
    app: tauri::AppHandle,
    state: tauri::State<'_, CloseBehaviorState>,
) -> Result<(), String> {
    if std::env::var_os("XAA_DATA_DIR").is_some() {
        return Err(
            "X.A.A. was started with an external data directory and cannot switch profiles.".into(),
        );
    }
    if state
        .active_data_root
        .lock()
        .map_err(|_| "Desktop settings are unavailable".to_string())?
        .is_none()
    {
        return Err("The default X.A.A. profile is already active".into());
    }
    save_active_data_root(&state.settings_path, None)?;
    *state
        .active_data_root
        .lock()
        .map_err(|_| "Desktop settings are unavailable".to_string())? = None;
    app.restart();
}

#[tauri::command]
fn get_start_on_login() -> Result<bool, String> {
    #[cfg(windows)]
    {
        autostart::is_enabled()
    }
    #[cfg(not(windows))]
    {
        Err("Start at sign-in is currently supported on Windows only".into())
    }
}

#[tauri::command]
fn set_start_on_login(enabled: bool) -> Result<bool, String> {
    #[cfg(windows)]
    {
        autostart::set_enabled(enabled)?;
        autostart::is_enabled()
    }
    #[cfg(not(windows))]
    {
        let _ = enabled;
        Err("Start at sign-in is currently supported on Windows only".into())
    }
}

fn should_show_main_window(
    acceptance_hidden: bool,
    arguments: impl IntoIterator<Item = std::ffi::OsString>,
) -> bool {
    !acceptance_hidden
        && !arguments
            .into_iter()
            .any(|argument| argument == "--background")
}

fn load_desktop_settings(settings_path: &Path) -> DesktopSettings {
    fs::read(settings_path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<DesktopSettings>(&bytes).ok())
        .unwrap_or_default()
}

fn save_desktop_settings(settings_path: &Path, settings: &DesktopSettings) -> Result<(), String> {
    let parent = settings_path
        .parent()
        .ok_or_else(|| "Desktop settings path has no parent directory".to_string())?;
    fs::create_dir_all(parent).map_err(|_| "Could not create the desktop settings directory")?;
    let temporary =
        settings_path.with_file_name(format!("desktop-settings.{}.tmp", uuid::Uuid::new_v4()));
    let bytes = serde_json::to_vec(settings).map_err(|_| "Could not encode desktop settings")?;
    let mut file = fs::File::create(&temporary).map_err(|_| "Could not write desktop settings")?;
    file.write_all(&bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| "Could not save desktop settings")?;
    fs::rename(&temporary, settings_path)
        .map_err(|_| "Could not replace desktop settings".to_string())
}

#[cfg(test)]
fn load_close_behavior(settings_path: &Path) -> CloseBehavior {
    load_desktop_settings(settings_path).close_behavior
}

fn save_close_behavior(settings_path: &Path, behavior: CloseBehavior) -> Result<(), String> {
    let mut settings = load_desktop_settings(settings_path);
    settings.close_behavior = behavior;
    save_desktop_settings(settings_path, &settings)
}

fn save_active_data_root(settings_path: &Path, data_root: Option<PathBuf>) -> Result<(), String> {
    let mut settings = load_desktop_settings(settings_path);
    settings.active_data_root = data_root;
    save_desktop_settings(settings_path, &settings)
}

#[tauri::command]
fn get_close_behavior(
    state: tauri::State<'_, CloseBehaviorState>,
) -> Result<CloseBehavior, String> {
    state
        .value
        .lock()
        .map(|behavior| *behavior)
        .map_err(|_| "Desktop settings are unavailable".into())
}

#[tauri::command]
fn set_close_behavior(
    state: tauri::State<'_, CloseBehaviorState>,
    behavior: CloseBehavior,
) -> Result<CloseBehavior, String> {
    let mut current = state
        .value
        .lock()
        .map_err(|_| "Desktop settings are unavailable".to_string())?;
    save_close_behavior(&state.settings_path, behavior)?;
    *current = behavior;
    Ok(behavior)
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

fn selected_settings_path(
    hidden_acceptance: bool,
    requested: Option<PathBuf>,
    data_directory: Option<PathBuf>,
    default: PathBuf,
) -> PathBuf {
    let directory = if hidden_acceptance {
        requested.or(data_directory).unwrap_or(default)
    } else {
        default
    };
    directory.join("desktop-settings.json")
}

#[cfg(test)]
mod tests {
    use super::{
        configure_engine_environment, load_close_behavior, load_desktop_settings,
        save_active_data_root, save_close_behavior, selected_settings_path, tray_image,
        CloseBehavior,
    };
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

    #[test]
    fn close_behavior_defaults_to_keep_running_and_persists_user_choice() {
        let directory = std::env::temp_dir().join(format!("xaa-settings-{}", uuid::Uuid::new_v4()));
        let settings_path = directory.join("desktop-settings.json");
        assert_eq!(
            load_close_behavior(&settings_path),
            CloseBehavior::KeepRunning
        );

        save_close_behavior(&settings_path, CloseBehavior::ExitOnClose).unwrap();
        assert_eq!(
            load_close_behavior(&settings_path),
            CloseBehavior::ExitOnClose
        );

        save_close_behavior(&settings_path, CloseBehavior::KeepRunning).unwrap();
        assert_eq!(
            load_close_behavior(&settings_path),
            CloseBehavior::KeepRunning
        );
        let _ = std::fs::remove_dir_all(directory);
    }

    #[test]
    fn restored_profile_setting_survives_close_behavior_updates() {
        let directory = std::env::temp_dir().join(format!("xaa-profile-{}", uuid::Uuid::new_v4()));
        let settings_path = directory.join("desktop-settings.json");
        let restored = directory.join("profile");
        save_active_data_root(&settings_path, Some(restored.clone())).unwrap();
        save_close_behavior(&settings_path, CloseBehavior::ExitOnClose).unwrap();
        assert_eq!(
            load_close_behavior(&settings_path),
            CloseBehavior::ExitOnClose
        );
        assert_eq!(
            load_desktop_settings(&settings_path).active_data_root,
            Some(restored)
        );
        save_active_data_root(&settings_path, None).unwrap();
        assert!(load_desktop_settings(&settings_path)
            .active_data_root
            .is_none());
        let _ = std::fs::remove_dir_all(directory);
    }

    #[test]
    fn acceptance_settings_use_isolated_paths_and_ignore_overrides_in_visible_app() {
        let requested = std::path::PathBuf::from("acceptance-config");
        let data = std::path::PathBuf::from("acceptance-data");
        let default = std::path::PathBuf::from("user-config");
        assert_eq!(
            selected_settings_path(
                false,
                Some(requested.clone()),
                Some(data.clone()),
                default.clone()
            ),
            default.join("desktop-settings.json")
        );
        assert_eq!(
            selected_settings_path(
                true,
                Some(requested.clone()),
                Some(data.clone()),
                default.clone()
            ),
            requested.join("desktop-settings.json")
        );
        assert_eq!(
            selected_settings_path(true, None, Some(data.clone()), default),
            data.join("desktop-settings.json")
        );
    }

    #[test]
    fn background_startup_keeps_the_window_hidden_but_acceptance_can_show_it() {
        use super::should_show_main_window;
        use std::ffi::OsString;

        assert!(!should_show_main_window(
            false,
            [
                OsString::from("xaa-desktop.exe"),
                OsString::from("--background")
            ]
        ));
        assert!(!should_show_main_window(
            true,
            [OsString::from("xaa-desktop.exe")]
        ));
        assert!(should_show_main_window(
            false,
            [OsString::from("xaa-desktop.exe")]
        ));
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
            let acceptance_mode = std::env::var("XAA_TEST_ACCEPTANCE").as_deref() == Ok("1")
                || std::env::var("XAA_TEST_HIDDEN").as_deref() == Ok("1");
            let settings_path = selected_settings_path(
                acceptance_mode,
                std::env::var_os("XAA_TEST_CONFIG_DIR").map(PathBuf::from),
                std::env::var_os("XAA_DATA_DIR").map(PathBuf::from),
                app.path().app_config_dir()?,
            );
            let settings = load_desktop_settings(&settings_path);
            let active_data_root = settings.active_data_root.clone();
            app.manage(CloseBehaviorState {
                value: Mutex::new(settings.close_behavior),
                active_data_root: Mutex::new(active_data_root.clone()),
                settings_path,
            });
            let listener = TcpListener::bind("127.0.0.1:0")?;
            let port = listener.local_addr()?.port();
            let token = uuid::Uuid::new_v4().simple().to_string();
            let executable = std::env::current_exe()?.with_file_name("xaa-engine.exe");
            #[cfg(debug_assertions)]
            let executable = if executable.exists() {
                executable
            } else {
                std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("binaries/xaa-engine-x86_64-pc-windows-msvc.exe")
            };
            let logs = selected_log_directory(
                acceptance_mode,
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
            if std::env::var_os("XAA_DATA_DIR").is_none() {
                if let Some(data_root) = active_data_root {
                    command.env("XAA_DATA_DIR", data_root);
                }
            }
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
            if should_show_main_window(
                std::env::var("XAA_TEST_HIDDEN").as_deref() == Ok("1"),
                std::env::args_os(),
            ) {
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
                let behavior = window
                    .app_handle()
                    .state::<CloseBehaviorState>()
                    .value
                    .lock()
                    .map(|value| *value)
                    .unwrap_or_default();
                if behavior == CloseBehavior::KeepRunning {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            engine_connection,
            restore_main_window,
            quit_application,
            get_close_behavior,
            set_close_behavior,
            get_start_on_login,
            set_start_on_login,
            get_active_data_root,
            export_backup_bundle,
            activate_restored_profile,
            use_default_data_profile
        ])
        .build(tauri::generate_context!())
        .expect("X.A.A. desktop failed to start")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                app.state::<EngineState>().stop();
            }
        });
}
