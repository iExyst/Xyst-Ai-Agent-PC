# Tauri NSIS build asset

`nsis-3.13-tools.zip` is the compressed official Tauri NSIS tool cache used by the pinned Tauri bundler. The Windows workflow expands it into Tauri's cache directory so Actions does not need to fetch GitHub Release assets, which have repeatedly returned HTTP 504 during CI.

- Archive SHA-256: `FCF5B13FD91D2D40BC425352277504DBE2E8BE43543474912C7BAD69DACE4B03`
- NSIS source release: https://github.com/tauri-apps/binary-releases/releases/tag/nsis-3.13; upstream archive SHA-1: `DB14F8AF2EA346B786C6AE19343F61AAF5E09B8E`
- Tauri plugin source release: https://github.com/tauri-apps/nsis-tauri-utils/releases/tag/nsis_tauri_utils-v0.5.3; upstream DLL SHA-1: `75197FEE3C6A814FE035788D1C34EAD39349B860`
- Licenses: NSIS includes its upstream `COPYING` file in the archive; the Tauri plugin is MIT OR Apache-2.0. License sources: https://github.com/tauri-apps/nsis-tauri-utils/tree/nsis_tauri_utils-v0.5.3

The installer workflow verifies the vendored archive SHA-256 and the Tauri bundler's expected NSIS executable and plugin DLL SHA-1 values before caching the toolset. The sources, versions, and hashes must be updated together if the pinned Tauri bundler changes.
