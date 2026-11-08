!macro NSIS_HOOK_POSTUNINSTALL
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
