//! Current-user Windows sign-in registration for the optional background launch.

use std::{io, mem::size_of, ptr};
use windows_sys::Win32::{
    Foundation::ERROR_FILE_NOT_FOUND,
    System::Registry::{
        RegCloseKey, RegCreateKeyExW, RegDeleteValueW, RegOpenKeyExW, RegQueryValueExW,
        RegSetValueExW, HKEY, HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE,
        REG_OPTION_NON_VOLATILE, REG_SZ,
    },
};

const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const RUN_VALUE: &str = "Xyst AI Agent";

struct RegistryKey(HKEY);

impl Drop for RegistryKey {
    fn drop(&mut self) {
        unsafe {
            RegCloseKey(self.0);
        }
    }
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

fn selected_value_name() -> String {
    if std::env::var("XAA_TEST_ACCEPTANCE").as_deref() == Ok("1")
        || std::env::var("XAA_TEST_HIDDEN").as_deref() == Ok("1")
    {
        if let Some(value) = std::env::var_os("XAA_TEST_AUTOSTART_VALUE_NAME") {
            return value.to_string_lossy().into_owned();
        }
    }
    RUN_VALUE.to_string()
}

fn launch_command() -> Result<String, String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("Could not locate X.A.A. for sign-in startup: {error}"))?;
    Ok(format!("\"{}\" --background", executable.display()))
}

fn open_run_key(access: u32) -> Result<Option<RegistryKey>, String> {
    let key_path = wide(RUN_KEY);
    let mut key = ptr::null_mut();
    let status =
        unsafe { RegOpenKeyExW(HKEY_CURRENT_USER, key_path.as_ptr(), 0, access, &mut key) };
    if status == ERROR_FILE_NOT_FOUND {
        return Ok(None);
    }
    if status != 0 {
        return Err(format!(
            "Could not open the current-user startup settings: {}",
            io::Error::from_raw_os_error(status as i32)
        ));
    }
    Ok(Some(RegistryKey(key)))
}

pub(crate) fn is_enabled() -> Result<bool, String> {
    let Some(key) = open_run_key(KEY_QUERY_VALUE)? else {
        return Ok(false);
    };
    let name = wide(&selected_value_name());
    let mut value_type = 0;
    let mut byte_count = 0;
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            name.as_ptr(),
            ptr::null(),
            &mut value_type,
            ptr::null_mut(),
            &mut byte_count,
        )
    };
    if status == ERROR_FILE_NOT_FOUND {
        return Ok(false);
    }
    if status != 0 {
        return Err(format!(
            "Could not read the current-user startup setting: {}",
            io::Error::from_raw_os_error(status as i32)
        ));
    }
    if value_type != REG_SZ
        || byte_count as usize > 64 * 1024
        || byte_count as usize % size_of::<u16>() != 0
    {
        return Ok(false);
    }
    let mut value = vec![0u16; byte_count as usize / size_of::<u16>()];
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            name.as_ptr(),
            ptr::null(),
            &mut value_type,
            value.as_mut_ptr().cast(),
            &mut byte_count,
        )
    };
    if status != 0 {
        return Err(format!(
            "Could not read the current-user startup command: {}",
            io::Error::from_raw_os_error(status as i32)
        ));
    }
    let end = value
        .iter()
        .position(|unit| *unit == 0)
        .unwrap_or(value.len());
    Ok(String::from_utf16_lossy(&value[..end]) == launch_command()?)
}

pub(crate) fn set_enabled(enabled: bool) -> Result<(), String> {
    let name = wide(&selected_value_name());
    if !enabled {
        let Some(key) = open_run_key(KEY_SET_VALUE)? else {
            return Ok(());
        };
        let status = unsafe { RegDeleteValueW(key.0, name.as_ptr()) };
        if status == ERROR_FILE_NOT_FOUND {
            return Ok(());
        }
        if status != 0 {
            return Err(format!(
                "Could not disable current-user sign-in startup: {}",
                io::Error::from_raw_os_error(status as i32)
            ));
        }
        return Ok(());
    }

    let key_path = wide(RUN_KEY);
    let mut key = ptr::null_mut();
    let status = unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            key_path.as_ptr(),
            0,
            ptr::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE,
            ptr::null(),
            &mut key,
            ptr::null_mut(),
        )
    };
    if status != 0 {
        return Err(format!(
            "Could not create the current-user startup setting: {}",
            io::Error::from_raw_os_error(status as i32)
        ));
    }
    let key = RegistryKey(key);
    let command = wide(&launch_command()?);
    let status = unsafe {
        RegSetValueExW(
            key.0,
            name.as_ptr(),
            0,
            REG_SZ,
            command.as_ptr().cast(),
            (command.len() * size_of::<u16>()) as u32,
        )
    };
    if status != 0 {
        return Err(format!(
            "Could not enable current-user sign-in startup: {}",
            io::Error::from_raw_os_error(status as i32)
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{selected_value_name, wide, RUN_VALUE};

    #[test]
    fn startup_value_uses_a_unique_name_only_for_hidden_acceptance() {
        unsafe {
            std::env::set_var("XAA_TEST_HIDDEN", "1");
            std::env::set_var("XAA_TEST_AUTOSTART_VALUE_NAME", "XAA Acceptance fixture");
        }
        assert_eq!(selected_value_name(), "XAA Acceptance fixture");

        unsafe {
            std::env::set_var("XAA_TEST_HIDDEN", "0");
            std::env::set_var("XAA_TEST_ACCEPTANCE", "1");
        }
        assert_eq!(selected_value_name(), "XAA Acceptance fixture");

        unsafe {
            std::env::set_var("XAA_TEST_ACCEPTANCE", "0");
        }
        assert_eq!(selected_value_name(), RUN_VALUE);

        unsafe {
            std::env::remove_var("XAA_TEST_HIDDEN");
            std::env::remove_var("XAA_TEST_ACCEPTANCE");
            std::env::remove_var("XAA_TEST_AUTOSTART_VALUE_NAME");
        }
    }

    #[test]
    fn registry_strings_are_nul_terminated_utf16() {
        assert_eq!(wide("Xyst").as_slice(), &[88, 121, 115, 116, 0]);
    }
}
