# Managed browser reads

Updated 2026-11-05. This is an initial Phase 7 browser slice, not general browser automation or Hermes parity.

## Run flow

The desktop has a separate per-run **Allow managed browser reads** grant. At least one exact DNS hostname must also be approved. The API saves both with the run, and the coordinator advertises `browser_run` only when both are present. Browser output is labeled `untrusted_web_content`.

`browser_run` accepts an HTTPS URL, up to five exact accessible link names, and an optional screenshot flag. It can navigate, follow links, read bounded visible page text, and capture a viewport PNG. It has no form fill, button click, download, or persistent-profile operation. A screenshot is stored as a managed `image/png` artifact and is previewable in the desktop.

The coordinator starts a short-lived Python worker with engine and provider bearer tokens removed from its environment. The worker launches the installed Microsoft Edge channel with a new nonpersistent context, blocks service workers, downloads, permissions, and QUIC, and closes both context and browser at completion. Cancellation or timeout ends the owned process tree. Worker input and output are capped at 1 MiB; visible text at 20,000 characters; screenshots at 512 KiB; the proxy limits each run to 32 tunnels and 2 MiB transferred data.

All browser requests must use HTTPS to an exact approved host and GET/HEAD. A local CONNECT proxy checks every hostname, resolves DNS, rejects the request if any answer is non-public, and opens the tunnel directly to one of the validated IP addresses. Chromium's loopback proxy bypass is removed so local and metadata destinations cannot avoid the proxy. Playwright request routing independently checks URLs/methods, including redirects and page requests.

## Limits

This is not an OS-level isolated browser. Edge runs under the current Windows account with its normal browser sandbox and processes untrusted public pages. GET requests can still trigger state changes on poorly designed sites; the run permission is therefore an explicit grant, and callers should avoid URLs/links that perform writes. Browser automation does not inherit the user's Edge cookies or profile. Edge must be installed. Sites that require login, non-GET application requests, or another host are unsupported by this slice.

## Verification

- `uv run pytest -q tests/unit/test_browser.py tests/unit/test_api.py tests/unit/test_web_fetch.py tests/unit/test_web_search.py`
- `uv run python scripts/browser_smoke.py` verifies live Edge navigation and PNG artifact registration against `example.com`.
- `uv run python scripts/build_engine.py` builds the frozen Windows engine including Playwright resources.
- `uv run python scripts/frozen_browser_diagnostic.py` launches the packaged worker against `example.com` and verifies page text and PNG output.

Live evidence is saved in [`browser-live.json`](../releases/evidence/browser-live.json) and [`browser-frozen-live.json`](../releases/evidence/browser-frozen-live.json). Coverage still needed includes controlled redirect and subresource cases, denial of every internal-address class, cancellation/process-tree observation, popup/new-tab behavior, broader live sites, and installer acceptance of the browser worker.
