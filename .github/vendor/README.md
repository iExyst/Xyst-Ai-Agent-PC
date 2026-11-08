# Tauri NSIS build asset

`nsis_tauri_utils-v0.5.3.dll` is the official Tauri NSIS utility plugin release used by the pinned Tauri bundler. It is copied into the Windows runner's Tauri tool cache so Actions does not need to fetch the GitHub Release asset, which has repeatedly returned HTTP 504 during CI.

- Source release: https://github.com/tauri-apps/nsis-tauri-utils/releases/tag/nsis_tauri_utils-v0.5.3
- Upstream SHA-1: `75197FEE3C6A814FE035788D1C34EAD39349B860`
- License: MIT OR Apache-2.0, from the source release's `LICENSE_MIT` and `LICENSE_APACHE-2.0` files.
- License texts: https://github.com/tauri-apps/nsis-tauri-utils/tree/nsis_tauri_utils-v0.5.3

The installer workflow verifies the upstream SHA-1 before the bundler uses this file. The source, version, and hash must be updated together if the pinned Tauri bundler changes its plugin release.
