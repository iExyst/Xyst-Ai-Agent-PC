!macro NSIS_HOOK_POSTUNINSTALL
  ; Remove the app's opt-in current-user sign-in entry on a full uninstall.
  ; Preserve it during upgrades so the existing startup preference survives.
  ${If} $UpdateMode <> 1
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Xyst AI Agent"
  ${EndIf}
  ; X.A.A. stores its SQLite database and artifacts in this app-specific
  ; directory rather than under the Tauri bundle identifier. Match Tauri's
  ; explicit "Delete the application data" choice after the app is stopped and
  ; never remove it during an update.
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    RmDir /r "$LOCALAPPDATA\XAA"
  ${EndIf}
!macroend
