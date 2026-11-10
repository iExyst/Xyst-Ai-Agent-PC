# Locked dependency and license metadata inventory

Snapshot date: 2026-11-10. This is an inventory of the current lockfiles and build environment, not a legal opinion or a complete third-party notice bundle.

## Scope and collection

- Python: `uv.lock`; 80 installed distribution records from `uv run --with pip-licenses pip-licenses --format=json --with-urls` (pip-licenses 5.5.5). This environment scan omits some lockfile entries that are not installed on this host and includes the three `jaraco.*` records using their original dotted names; the lockfile itself contains those names normalized with hyphens. A full lock cross-check found five missing third-party rows: `jeepney`, `macholib`, `SecretStorage`, `setuptools`, and `uvloop`. Their exact pinned source archives were downloaded and matched against the SHA-256 hashes in `uv.lock`; license files and package metadata are preserved in `.acceptance/conditional-lock-license-audit-20261109/verification.json`. Four are platform-conditional; `setuptools` is a transitive PyInstaller dependency. This fills the lock metadata table but does not expand the separately scoped notice draft or complete the package copyright review. The added direct `uritemplate==4.2.0` dependency is licensed BSD-3-Clause OR Apache-2.0; its pinned source archive and both license texts are hash-recorded in `.acceptance/uritemplate-license-audit-20261109/verification.json`.
- JavaScript: `package-lock.json`; 6 runtime dependency records and 61 total records from `npx license-checker@25.0.1 --production --json` and `npx license-checker@25.0.1 --json`.
- Rust: `apps/desktop/src-tauri/Cargo.lock`; 411 package records from `cargo metadata --format-version 1 --locked` (Cargo 1.99.1). Target-specific and build dependencies are included.
- Combined table: 558 unique ecosystem/name/version/license/source rows. The MCP SDK v2.3.0 lock migration replaces `httpx-sse` and `pydantic-settings` with `httpcore2`, `httpx2`, platform-conditional `httpx2-jsfetch`, `mcp-types`, and `truststore`; the pinned `httpx2-jsfetch` wheel hash and BSD-3-Clause text were checked against `uv.lock`. Prior command outputs remain at `.acceptance/dependency-inventory-971fa5b/`; after adding Discord, current Python, npm, and Cargo collection outputs are retained at `.acceptance/discord-dependency-audit/`.

License expressions below are copied from package metadata without normalization. A package expression such as `MIT OR Apache-2.0` must be reviewed with the package license text; it is not a substitute for that text.

## Release review findings

- X.A.A. source is licensed under MIT in the root [`LICENSE`](../../LICENSE). The license is also declared in `pyproject.toml`, `apps/desktop/src-tauri/Cargo.toml`, and the private npm workspace metadata. Third-party dependencies retain their own licenses and notices; this inventory is not a legal opinion or release approval.
- `agent-client-protocol==0.9.0` has unknown PyPI license metadata. The installed pinned distribution's `.dist-info/licenses/LICENSE` was inspected and contains the Apache License 2.0 text; its [upstream Python SDK repository](https://github.com/agentclientprotocol/python-sdk) also identifies Apache-2.0.
- `pyinstaller==6.22.3` reports GPLv2 metadata, and `pyinstaller-hooks-contrib==2026.9` reports Apache/GPLv2 metadata. Review and preserve their packaging exception and copyright/license texts before release.
- A provisional full-text notice draft is in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). It contains 293 previously inventoried license text groups (286 earlier groups, including 37 PDFium wheel texts, plus seven Discord-related groups) and now adds 22 source-hash-grouped texts from the five previously omitted locked Python source archives. The six Windows-target crate archives omit license files, but their declared texts are represented from exact upstream VCS revisions or Mozilla’s official MPL-2.0 text. All six downloaded archive hashes match Cargo.lock. The earlier package-attribution audit found no omissions across its 2026-11-07 scoped inputs: 67 installed Python development distributions plus the locked PDFium wheel license texts, 5 external npm runtime dependencies, and 247 external Windows-target Cargo packages (excluding `xaa-desktop`). Its report is `.acceptance/dependency-license-upstream-20261107/verification.json`. The Discord dependency update adds nine host-installed Python distributions; their current license metadata is listed below, with exact package license files retained in the new virtual environment. Their exact installed license files and accompanying NOTICE files are now included in the notice draft; complete package copyright coverage still needs review before release. The Python metadata table covers every `uv.lock` package. Newly added texts include the pinned `setuptools` package and its vendored license/NOTICE files, plus uvloop, whose PyPI metadata says MIT while its pinned source includes MIT, Apache-2.0, and vendored libuv licenses. The source-archive report is `.acceptance/conditional-lock-license-audit-20261109/verification.json`. The unsigned Windows NSIS package now installs this draft, its inventory, and [`0.1.1.md`](0.1.1.md) beside the application binaries; the staged manifest and installer acceptance verify each document hash (see `../progress.md`, 2026-11-07). The application source license is MIT. Third-party attribution, copyright and platform-conditional coverage review remain open; these materials do not make the package release-complete.
- `npm audit --json` reported zero vulnerabilities across the current lockfile on 2026-11-07; raw output is `.acceptance/dependency-inventory-971fa5b/npm-audit.json`. The locked `openapi-typescript` → `@redocly/openapi-core` → `minimatch` versions from the 2026-11-03 report are still present, so this closes the current scanner finding without claiming that the dependency chain changed. Re-audit after lockfile updates.

## Package records

| Ecosystem | Package | Version | Declared license | Repository or project URL |
|---|---|---:|---|---|
| npm | @babel/code-frame | 7.29.7 | MIT | https://github.com/babel/babel |
| npm | @babel/helper-validator-identifier | 7.29.7 | MIT | https://github.com/babel/babel |
| npm | @oxc-project/types | 0.156.0 | MIT | https://github.com/oxc-project/oxc |
| npm | @playwright/test | 1.64.1 | Apache-2.0 | https://github.com/microsoft/playwright |
| npm | @redocly/ajv | 8.11.2 | MIT | https://github.com/ajv-validator/ajv |
| npm | @redocly/config | 0.22.0 | MIT | — |
| npm | @redocly/openapi-core | 1.34.23 | MIT | https://github.com/Redocly/redocly-cli |
| npm | @rolldown/binding-win32-x64-msvc | 1.2.16 | MIT | https://github.com/rolldown/rolldown |
| npm | @rolldown/pluginutils | 1.0.1 | MIT | https://github.com/rolldown/plugins |
| npm | @tauri-apps/api | 2.12.4 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| npm | @tauri-apps/cli | 2.12.4 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| npm | @tauri-apps/cli-win32-x64-msvc | 2.12.4 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| npm | @tauri-apps/plugin-dialog | 2.8.4 | MIT OR Apache-2.0 | https://github.com/tauri-apps/plugins-workspace |
| npm | @types/react | 19.3.0 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped |
| npm | @types/react-dom | 19.3.0 | MIT | https://github.com/DefinitelyTyped/DefinitelyTyped |
| npm | agent-base | 7.1.4 | MIT | https://github.com/TooTallNate/proxy-agents |
| npm | ansi-colors | 4.1.3 | MIT | https://github.com/doowb/ansi-colors |
| npm | argparse | 2.0.1 | Python-2.0 | https://github.com/nodeca/argparse |
| npm | balanced-match | 1.0.2 | MIT | https://github.com/juliangruber/balanced-match |
| npm | brace-expansion | 2.1.7 | MIT | https://github.com/juliangruber/brace-expansion |
| npm | change-case | 5.4.4 | MIT | https://github.com/blakeembrey/change-case |
| npm | colorette | 1.4.0 | MIT | https://github.com/jorgebucaran/colorette |
| npm | csstype | 3.2.3 | MIT | https://github.com/frenic/csstype |
| npm | debug | 4.4.3 | MIT | https://github.com/debug-js/debug |
| npm | detect-libc | 2.1.2 | Apache-2.0 | https://github.com/lovell/detect-libc |
| npm | fast-deep-equal | 3.1.3 | MIT | https://github.com/epoberezkin/fast-deep-equal |
| npm | fdir | 6.5.0 | MIT | https://github.com/thecodrr/fdir |
| npm | https-proxy-agent | 7.0.6 | MIT | https://github.com/TooTallNate/proxy-agents |
| npm | index-to-position | 1.2.0 | MIT | https://github.com/sindresorhus/index-to-position |
| npm | js-levenshtein | 1.1.6 | MIT | https://github.com/gustf/js-levenshtein |
| npm | js-tokens | 4.0.0 | MIT | https://github.com/lydell/js-tokens |
| npm | js-yaml | 4.3.2 | MIT | https://github.com/nodeca/js-yaml |
| npm | json-schema-traverse | 1.0.0 | MIT | https://github.com/epoberezkin/json-schema-traverse |
| npm | lightningcss | 1.33.0 | MPL-2.0 | https://github.com/parcel-bundler/lightningcss |
| npm | lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 | https://github.com/parcel-bundler/lightningcss |
| npm | minimatch | 5.1.9 | ISC | https://github.com/isaacs/minimatch |
| npm | ms | 2.1.3 | MIT | https://github.com/vercel/ms |
| npm | nanoid | 3.3.20 | MIT | https://github.com/ai/nanoid |
| npm | openapi-typescript | 7.13.0 | MIT | https://github.com/openapi-ts/openapi-typescript |
| npm | parse-json | 8.3.0 | MIT | https://github.com/sindresorhus/parse-json |
| npm | picocolors | 1.1.1 | ISC | https://github.com/alexeyraspopov/picocolors |
| npm | picomatch | 4.0.7 | MIT | https://github.com/micromatch/picomatch |
| npm | playwright | 1.64.1 | Apache-2.0 | https://github.com/microsoft/playwright |
| npm | playwright-core | 1.64.1 | Apache-2.0 | https://github.com/microsoft/playwright |
| npm | pluralize | 8.0.0 | MIT | https://github.com/blakeembrey/pluralize |
| npm | postcss | 8.5.32 | MIT | https://github.com/postcss/postcss |
| npm | react | 19.3.1 | MIT | https://github.com/react/react |
| npm | react-dom | 19.3.1 | MIT | https://github.com/react/react |
| npm | require-from-string | 2.0.2 | MIT | https://github.com/floatdrop/require-from-string |
| npm | rolldown | 1.2.16 | MIT | https://github.com/rolldown/rolldown |
| npm | scheduler | 0.28.0 | MIT | https://github.com/react/react |
| npm | source-map-js | 1.2.2 | BSD-3-Clause | https://github.com/7rulnik/source-map-js |
| npm | supports-color | 10.2.2 | MIT | https://github.com/chalk/supports-color |
| npm | tinyglobby | 0.2.17 | MIT | https://github.com/SuperchupuDev/tinyglobby |
| npm | type-fest | 4.41.0 | (MIT OR CC0-1.0) | https://github.com/sindresorhus/type-fest |
| npm | typescript | 5.9.3 | Apache-2.0 | https://github.com/microsoft/TypeScript |
| npm | uri-js-replace | 1.0.2 | MIT | https://github.com/andreinwald/uri-js-replace |
| npm | vite | 8.3.5 | MIT | https://github.com/vitejs/vite |
| npm | xaa-workspace | 0.1.1 | MIT | repository source |
| npm | yaml-ast-parser | 0.0.43 | Apache-2.0 | https://github.com/mulesoft-labs/yaml-ast-parser |
| npm | yargs-parser | 21.1.1 | ISC | https://github.com/yargs/yargs-parser |
| Python | agent-client-protocol | 0.9.0 | Apache-2.0 (upstream repository; PyPI metadata omits license) | https://github.com/agentclientprotocol/python-sdk
| Python | aiohappyeyeballs | 2.7.1 | Python Software Foundation License | https://github.com/aio-libs/aiohappyeyeballs |
| Python | aiohttp | 3.14.5 | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| Python | aiosignal | 1.4.0 | Apache Software License | https://github.com/aio-libs/aiosignal |
| Python | audioop-lts | 0.2.2 | PSF-2.0 | https://github.com/AbstractUmbra/audioop |
| Python | altgraph | 0.17.5 | MIT License | https://altgraph.readthedocs.io |
| Python | annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| Python | annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| Python | anyio | 4.16.0 | MIT | https://anyio.readthedocs.io/en/stable/versionhistory.html |
| Python | attrs | 26.1.0 | MIT | https://www.attrs.org/en/stable/changelog.html |
| Python | certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) | https://github.com/certifi/python-certifi |
| Python | cffi | 2.1.1 | MIT-0 | https://cffi.readthedocs.io/en/latest/whatsnew.html |
| Python | click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click/ |
| Python | colorama | 0.4.6 | BSD License | https://github.com/tartley/colorama |
| Python | cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| Python | discord.py | 2.7.1 | MIT License | https://github.com/Rapptz/discord.py |
| Python | fastapi | 0.162.0 | MIT | https://github.com/fastapi/fastapi |
| Python | frozenlist | 1.8.0 | Apache-2.0 | https://github.com/aio-libs/frozenlist |
| Python | greenlet | 3.5.6 | MIT AND PSF-2.0 | https://greenlet.readthedocs.io |
| Python | h11 | 0.16.0 | MIT License | https://github.com/python-hyper/h11 |
| Python | httpcore | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore/ |
| Python | httptools | 0.9.0 | MIT | https://github.com/MagicStack/httptools |
| Python | httpx | 0.28.1 | BSD License | https://github.com/encode/httpx |
| Python | httpcore2 | 2.16.0 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| Python | httpx2 | 2.16.0 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| Python | httpx2-jsfetch | 1.0 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| Python | httpx-ws | 0.9.0 | MIT License | https://github.com/frankie567/httpx-ws |
| Python | idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| Python | iniconfig | 2.3.1 | MIT | https://github.com/pytest-dev/iniconfig |
| Python | jaraco.classes | 3.4.0 | MIT License | https://github.com/jaraco/jaraco.classes |
| Python | jaraco.context | 6.1.2 | MIT | https://github.com/jaraco/jaraco.context |
| Python | jaraco.functools | 4.6.0 | MIT | https://github.com/jaraco/jaraco.functools |
| Python | jeepney | 0.9.0 | MIT | https://gitlab.com/takluyver/jeepney |
| Python | jsonschema | 4.26.0 | MIT | https://github.com/python-jsonschema/jsonschema |
| Python | jsonschema-specifications | 2025.9.1 | MIT | https://github.com/python-jsonschema/jsonschema-specifications |
| Python | keyring | 25.7.0 | MIT | https://github.com/jaraco/keyring |
| Python | lmstudio | 1.6.0b1 | MIT | https://github.com/lmstudio-ai/lmstudio-sdk-python |
| Python | macholib | 1.16.4 | MIT | https://github.com/ronaldoussoren/macholib |
| Python | mcp | 2.3.0 | MIT | https://modelcontextprotocol.io |
| Python | mcp-types | 2.3.0 | MIT | https://modelcontextprotocol.io |
| Python | more-itertools | 11.2.1 | MIT | https://github.com/more-itertools/more-itertools |
| Python | multidict | 7.2.1 | Apache-2.0 | https://github.com/aio-libs/multidict |
| Python | msgspec | 0.22.0 | BSD-3-Clause | https://msgspec.dev |
| Python | nodeenv | 1.12.0 | BSD License | https://github.com/ekalinin/nodeenv |
| Python | opentelemetry-api | 1.45.2 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| Python | packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| Python | pefile | 2024.8.26 | MIT | https://github.com/erocarrera/pefile |
| Python | playwright | 1.64.1 | Apache-2.0 | https://github.com/Microsoft/playwright-python |
| Python | pluggy | 1.7.0 | MIT License | https://github.com/pytest-dev/pluggy |
| Python | propcache | 0.5.4 | Apache-2.0 | https://github.com/aio-libs/propcache |
| Python | pycparser | 3.12 | BSD-3-Clause | https://github.com/eliben/pycparser |
| Python | pydantic | 2.14.2 | MIT | https://github.com/pydantic/pydantic |
| Python | pydantic_core | 2.50.2 | MIT | https://github.com/pydantic |
| Python | pyee | 14.0.0 | MIT License | https://github.com/jfhbrook/pyee |
| Python | Pygments | 2.21.0 | BSD-2-Clause | https://pygments.org |
| Python | pyinstaller | 6.22.3 | GNU General Public License v2 (GPLv2) | https://pyinstaller.org |
| Python | pyinstaller-hooks-contrib | 2026.9 | Apache Software License; GNU General Public License v2 (GPLv2) | https://github.com/pyinstaller/pyinstaller-hooks-contrib |
| Python | PyJWT | 2.15.1 | MIT | https://github.com/jpadilla/pyjwt |
| Python | pypdf | 6.21.4 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| Python | pypdfium2 | 5.15.1 | BSD-3-Clause, Apache-2.0, dependency licenses | https://github.com/pypdfium2-team/pypdfium2 |
| Python | pyright | 1.1.414 | MIT | https://github.com/RobertCraigie/pyright-python |
| Python | pytest | 8.4.2 | MIT License | https://docs.pytest.org/en/latest/ |
| Python | pytest-asyncio | 1.4.1.post1 | Apache-2.0 | https://github.com/pytest-dev/pytest-asyncio |
| Python | python-dotenv | 1.2.4 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| Python | python-multipart | 0.0.33 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| Python | SecretStorage | 3.5.0 | BSD-3-Clause | https://github.com/mitya57/secretstorage |
| Python | setuptools | 84.2.1 | MIT | https://github.com/pypa/setuptools |
| Python | pywin32 | 312 | Python Software Foundation License | https://github.com/mhammond/pywin32 |
| Python | pywin32-ctypes | 0.2.3 | BSD-3-Clause | https://github.com/enthought/pywin32-ctypes |
| Python | PyYAML | 6.0.3 | MIT License | https://pyyaml.org/ |
| Python | referencing | 0.37.0 | MIT | https://github.com/python-jsonschema/referencing |
| Python | rpds-py | 2026.9.1 | MIT | https://github.com/crate-py/rpds |
| Python | ruff | 0.17.3 | MIT | https://docs.astral.sh/ruff |
| Python | sse-starlette | 3.6.0 | BSD-3-Clause | https://github.com/sysid/sse-starlette |
| Python | starlette | 1.10.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| Python | truststore | 0.10.5 | MIT | https://github.com/sethmlarson/truststore |
| Python | typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| Python | typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| Python | tzdata | 2026.6 | Apache-2.0 | https://github.com/python/tzdata |
| Python | uritemplate | 4.2.0 | BSD-3-Clause OR Apache-2.0 | https://github.com/python-hyper/uritemplate |
| Python | uvicorn | 0.54.0 | BSD-3-Clause | https://uvicorn.dev/ |
| Python | uvloop | 0.23.0 | MIT License (PyPI metadata; upstream includes MIT and Apache-2.0 texts) | https://github.com/MagicStack/uvloop |
| Python | watchfiles | 1.3.0 | MIT License | https://github.com/samuelcolvin/watchfiles |
| Python | websockets | 17.4 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| Python | wsproto | 1.3.2 | MIT | https://github.com/python-hyper/wsproto/ |
| Python | yarl | 1.25.1 | Apache-2.0 | https://github.com/aio-libs/yarl |
| Python | xaa-engine | 0.1.1 | UNKNOWN | UNKNOWN |
| Rust/Cargo | adler2 | 2.0.1 | 0BSD OR MIT OR Apache-2.0 | https://github.com/oyvindln/adler2 |
| Rust/Cargo | aho-corasick | 1.1.5 | Unlicense OR MIT | https://github.com/BurntSushi/aho-corasick |
| Rust/Cargo | alloc-no-stdlib | 3.0.0 | BSD-3-Clause | https://github.com/dropbox/rust-alloc-no-stdlib |
| Rust/Cargo | alloc-stdlib | 0.3.0 | BSD-3-Clause | https://github.com/dropbox/rust-alloc-no-stdlib |
| Rust/Cargo | android_system_properties | 0.1.6 | MIT OR Apache-2.0 | https://github.com/nical/android_system_properties |
| Rust/Cargo | anyhow | 1.0.104 | MIT OR Apache-2.0 | https://github.com/dtolnay/anyhow |
| Rust/Cargo | atk | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | atk-sys | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | atomic-waker | 1.1.2 | Apache-2.0 OR MIT | https://github.com/smol-rs/atomic-waker |
| Rust/Cargo | autocfg | 1.5.1 | Apache-2.0 OR MIT | https://github.com/cuviper/autocfg |
| Rust/Cargo | base64 | 0.21.7 | MIT OR Apache-2.0 | https://github.com/marshallpierce/rust-base64 |
| Rust/Cargo | base64 | 0.23.1 | MIT OR Apache-2.0 | https://github.com/marshallpierce/rust-base64 |
| Rust/Cargo | bit-set | 0.8.0 | Apache-2.0 OR MIT | https://github.com/contain-rs/bit-set |
| Rust/Cargo | bit-vec | 0.8.0 | Apache-2.0 OR MIT | https://github.com/contain-rs/bit-vec |
| Rust/Cargo | bitflags | 1.3.2 | MIT/Apache-2.0 | https://github.com/bitflags/bitflags |
| Rust/Cargo | bitflags | 2.13.2 | MIT OR Apache-2.0 | https://github.com/bitflags/bitflags |
| Rust/Cargo | block-buffer | 0.10.4 | MIT OR Apache-2.0 | https://github.com/RustCrypto/utils |
| Rust/Cargo | block2 | 0.6.2 | MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | brotli | 9.1.1 | BSD-3-Clause AND MIT | https://github.com/dropbox/rust-brotli |
| Rust/Cargo | brotli-decompressor | 6.2.0 | BSD-3-Clause/MIT | https://github.com/dropbox/rust-brotli-decompressor |
| Rust/Cargo | bs58 | 0.5.1 | MIT/Apache-2.0 | https://github.com/Nullus157/bs58-rs |
| Rust/Cargo | bumpalo | 3.20.3 | MIT OR Apache-2.0 | https://github.com/fitzgen/bumpalo |
| Rust/Cargo | bytemuck | 1.25.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/Lokathor/bytemuck |
| Rust/Cargo | byteorder | 1.5.0 | Unlicense OR MIT | https://github.com/BurntSushi/byteorder |
| Rust/Cargo | bytes | 1.12.1 | MIT | https://github.com/tokio-rs/bytes |
| Rust/Cargo | cairo-rs | 0.18.5 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | cairo-sys-rs | 0.18.2 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | camino | 1.2.6 | MIT OR Apache-2.0 | https://github.com/camino-rs/camino |
| Rust/Cargo | cargo-platform | 0.1.9 | MIT OR Apache-2.0 | https://github.com/rust-lang/cargo |
| Rust/Cargo | cargo_metadata | 0.19.2 | MIT | https://github.com/oli-obk/cargo_metadata |
| Rust/Cargo | cargo_toml | 1.0.1 | Apache-2.0 OR MIT | https://gitlab.com/lib.rs/cargo_toml |
| Rust/Cargo | cc | 1.11.0 | MIT OR Apache-2.0 | https://github.com/rust-lang/cc-rs |
| Rust/Cargo | cesu8 | 1.1.0 | Apache-2.0/MIT | https://github.com/emk/cesu8-rs |
| Rust/Cargo | cfb | 0.14.0 | MIT | https://github.com/mdsteele/rust-cfb |
| Rust/Cargo | cfg-expr | 0.15.8 | MIT OR Apache-2.0 | https://github.com/EmbarkStudios/cfg-expr |
| Rust/Cargo | cfg-if | 1.0.5 | MIT OR Apache-2.0 | https://github.com/rust-lang/cfg-if |
| Rust/Cargo | chrono | 0.4.45 | MIT OR Apache-2.0 | https://github.com/chronotope/chrono |
| Rust/Cargo | combine | 4.6.9 | MIT | https://github.com/Marwes/combine |
| Rust/Cargo | cookie | 0.18.2 | MIT OR Apache-2.0 | https://github.com/SergioBenitez/cookie-rs |
| Rust/Cargo | core-foundation | 0.10.1 | MIT OR Apache-2.0 | https://github.com/servo/core-foundation-rs |
| Rust/Cargo | core-foundation-sys | 0.8.7 | MIT OR Apache-2.0 | https://github.com/servo/core-foundation-rs |
| Rust/Cargo | core-graphics | 0.25.0 | MIT OR Apache-2.0 | https://github.com/servo/core-foundation-rs |
| Rust/Cargo | core-graphics-types | 0.2.0 | MIT OR Apache-2.0 | https://github.com/servo/core-foundation-rs |
| Rust/Cargo | cpufeatures | 0.2.17 | MIT OR Apache-2.0 | https://github.com/RustCrypto/utils |
| Rust/Cargo | crc32fast | 1.5.4 | MIT OR Apache-2.0 | https://github.com/srijs/rust-crc32fast |
| Rust/Cargo | crossbeam-channel | 0.5.17 | MIT OR Apache-2.0 | https://github.com/crossbeam-rs/crossbeam |
| Rust/Cargo | crossbeam-utils | 0.8.23 | MIT OR Apache-2.0 | https://github.com/crossbeam-rs/crossbeam |
| Rust/Cargo | crypto-common | 0.1.7 | MIT OR Apache-2.0 | https://github.com/RustCrypto/traits |
| Rust/Cargo | cssparser | 0.37.0 | MPL-2.0 | https://github.com/servo/rust-cssparser |
| Rust/Cargo | cssparser-macros | 0.7.1 | MPL-2.0 | https://github.com/servo/rust-cssparser |
| Rust/Cargo | ctor | 1.0.13 | Apache-2.0 OR MIT | https://github.com/mmastrac/linktime |
| Rust/Cargo | darling | 0.24.1 | MIT | https://github.com/TedDriggs/darling |
| Rust/Cargo | darling_core | 0.24.1 | MIT | https://github.com/TedDriggs/darling |
| Rust/Cargo | darling_macro | 0.24.1 | MIT | https://github.com/TedDriggs/darling |
| Rust/Cargo | dbus | 0.9.12 | Apache-2.0/MIT | https://github.com/diwic/dbus-rs |
| Rust/Cargo | defmt | 1.2.0 | MIT OR Apache-2.0 | https://github.com/knurling-rs/defmt |
| Rust/Cargo | defmt-macros | 1.2.0 | MIT OR Apache-2.0 | https://github.com/knurling-rs/defmt |
| Rust/Cargo | defmt-parser | 1.1.0 | MIT OR Apache-2.0 | https://github.com/knurling-rs/defmt |
| Rust/Cargo | deranged | 0.5.9 | MIT OR Apache-2.0 | https://github.com/jhpratt/deranged |
| Rust/Cargo | derive_more | 2.1.1 | MIT | https://github.com/JelteF/derive_more |
| Rust/Cargo | derive_more-impl | 2.1.1 | MIT | https://github.com/JelteF/derive_more |
| Rust/Cargo | digest | 0.10.7 | MIT OR Apache-2.0 | https://github.com/RustCrypto/traits |
| Rust/Cargo | dirs | 7.0.0 | MIT OR Apache-2.0 | https://codeberg.org/dirs/dirs-rs |
| Rust/Cargo | dirs-sys | 0.5.0 | MIT OR Apache-2.0 | https://github.com/dirs-dev/dirs-sys-rs |
| Rust/Cargo | dispatch2 | 0.3.1 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | displaydoc | 0.2.7 | MIT OR Apache-2.0 | https://github.com/yaahc/displaydoc |
| Rust/Cargo | dlopen2 | 0.8.2 | MIT | https://github.com/OpenByteDev/dlopen2 |
| Rust/Cargo | dlopen2_derive | 0.4.3 | MIT | https://github.com/OpenByteDev/dlopen2 |
| Rust/Cargo | dom_query | 0.28.0 | MIT | https://github.com/niklak/dom_query |
| Rust/Cargo | dpi | 0.1.2 | Apache-2.0 AND MIT | https://github.com/rust-windowing/winit |
| Rust/Cargo | dtoa | 1.0.11 | MIT OR Apache-2.0 | https://github.com/dtolnay/dtoa |
| Rust/Cargo | dtoa-short | 0.3.5 | MPL-2.0 | https://github.com/upsuper/dtoa-short |
| Rust/Cargo | dunce | 1.0.5 | CC0-1.0 OR MIT-0 OR Apache-2.0 | https://gitlab.com/kornelski/dunce |
| Rust/Cargo | dyn-clone | 1.0.20 | MIT OR Apache-2.0 | https://github.com/dtolnay/dyn-clone |
| Rust/Cargo | embed-resource | 3.0.12 | MIT | https://github.com/nabijaczleweli/rust-embed-resource |
| Rust/Cargo | embed_plist | 1.2.2 | MIT OR Apache-2.0 | https://github.com/nvzqz/embed-plist-rs |
| Rust/Cargo | equivalent | 1.0.3 | Apache-2.0 OR MIT | https://github.com/indexmap-rs/equivalent |
| Rust/Cargo | erased-serde | 0.4.11 | MIT OR Apache-2.0 | https://github.com/dtolnay/erased-serde |
| Rust/Cargo | fastrand | 2.5.0 | Apache-2.0 OR MIT | https://github.com/smol-rs/fastrand |
| Rust/Cargo | fdeflate | 0.3.7 | MIT OR Apache-2.0 | https://github.com/image-rs/fdeflate |
| Rust/Cargo | field-offset | 0.3.6 | MIT OR Apache-2.0 | https://github.com/Diggsey/rust-field-offset |
| Rust/Cargo | find-msvc-tools | 0.1.15 | MIT OR Apache-2.0 | https://github.com/rust-lang/cc-rs |
| Rust/Cargo | flate2 | 1.1.10 | MIT OR Apache-2.0 | https://github.com/rust-lang/flate2-rs |
| Rust/Cargo | fnv | 1.0.7 | Apache-2.0 / MIT | https://github.com/servo/rust-fnv |
| Rust/Cargo | foldhash | 0.2.0 | Zlib | https://github.com/orlp/foldhash |
| Rust/Cargo | foreign-types | 0.5.0 | MIT/Apache-2.0 | https://github.com/sfackler/foreign-types |
| Rust/Cargo | foreign-types-macros | 0.2.4 | MIT/Apache-2.0 | https://github.com/sfackler/foreign-types |
| Rust/Cargo | foreign-types-shared | 0.3.1 | MIT/Apache-2.0 | https://github.com/sfackler/foreign-types |
| Rust/Cargo | form_urlencoded | 1.2.2 | MIT OR Apache-2.0 | https://github.com/servo/rust-url |
| Rust/Cargo | futures-channel | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | futures-core | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | futures-executor | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | futures-io | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | futures-macro | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | futures-sink | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | futures-task | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | futures-util | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/futures-rs |
| Rust/Cargo | gdk | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | gdk-pixbuf | 0.18.5 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | gdk-pixbuf-sys | 0.18.0 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | gdk-sys | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | gdkwayland-sys | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | gdkx11 | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | gdkx11-sys | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | generic-array | 0.14.7 | MIT | https://github.com/fizyk20/generic-array.git |
| Rust/Cargo | getrandom | 0.3.4 | MIT OR Apache-2.0 | https://github.com/rust-random/getrandom |
| Rust/Cargo | getrandom | 0.4.3 | MIT OR Apache-2.0 | https://github.com/rust-random/getrandom |
| Rust/Cargo | gio | 0.18.4 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | gio-sys | 0.18.1 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | glib | 0.18.5 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | glib-macros | 0.18.5 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | glib-sys | 0.18.1 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | glob | 0.3.4 | MIT OR Apache-2.0 | https://github.com/rust-lang/glob |
| Rust/Cargo | gobject-sys | 0.18.0 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | gtk | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | gtk-sys | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | gtk3-macros | 0.18.2 | MIT | https://github.com/gtk-rs/gtk3-rs |
| Rust/Cargo | hashbrown | 0.12.3 | MIT OR Apache-2.0 | https://github.com/rust-lang/hashbrown |
| Rust/Cargo | hashbrown | 0.17.1 | MIT OR Apache-2.0 | https://github.com/rust-lang/hashbrown |
| Rust/Cargo | heck | 0.4.1 | MIT OR Apache-2.0 | https://github.com/withoutboats/heck |
| Rust/Cargo | heck | 0.5.0 | MIT OR Apache-2.0 | https://github.com/withoutboats/heck |
| Rust/Cargo | hex | 0.4.3 | MIT OR Apache-2.0 | https://github.com/KokaKiwi/rust-hex |
| Rust/Cargo | html5ever | 0.39.0 | MIT OR Apache-2.0 | https://github.com/servo/html5ever |
| Rust/Cargo | http | 1.5.0 | MIT OR Apache-2.0 | https://github.com/hyperium/http |
| Rust/Cargo | http-body | 1.1.0 | MIT | https://github.com/hyperium/http-body |
| Rust/Cargo | http-body-util | 0.1.5 | MIT | https://github.com/hyperium/http-body |
| Rust/Cargo | httparse | 1.10.1 | MIT OR Apache-2.0 | https://github.com/seanmonstar/httparse |
| Rust/Cargo | hyper | 1.12.0 | MIT | https://github.com/hyperium/hyper |
| Rust/Cargo | hyper-util | 0.1.21 | MIT | https://github.com/hyperium/hyper-util |
| Rust/Cargo | iana-time-zone | 0.1.65 | MIT OR Apache-2.0 | https://github.com/strawlab/iana-time-zone |
| Rust/Cargo | iana-time-zone-haiku | 0.1.2 | MIT OR Apache-2.0 | https://github.com/strawlab/iana-time-zone |
| Rust/Cargo | ico | 0.6.0 | MIT | https://github.com/mdsteele/rust-ico |
| Rust/Cargo | icu_collections | 2.4.0 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | icu_locale_core | 2.4.0 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | icu_normalizer | 2.4.0 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | icu_normalizer_data | 2.4.0 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | icu_properties | 2.4.0 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | icu_properties_data | 2.4.0 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | icu_provider | 2.4.0 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | ident_case | 1.0.1 | MIT/Apache-2.0 | https://github.com/TedDriggs/ident_case |
| Rust/Cargo | idna | 1.1.0 | MIT OR Apache-2.0 | https://github.com/servo/rust-url/ |
| Rust/Cargo | idna_adapter | 1.2.2 | Apache-2.0 OR MIT | https://github.com/hsivonen/idna_adapter |
| Rust/Cargo | indexmap | 1.9.3 | Apache-2.0 OR MIT | https://github.com/bluss/indexmap |
| Rust/Cargo | indexmap | 2.14.2 | Apache-2.0 OR MIT | https://github.com/indexmap-rs/indexmap |
| Rust/Cargo | infer | 0.22.0 | MIT | https://github.com/bojand/infer |
| Rust/Cargo | ipnet | 2.12.2 | MIT OR Apache-2.0 | https://github.com/krisprice/ipnet |
| Rust/Cargo | itoa | 1.0.18 | MIT OR Apache-2.0 | https://github.com/dtolnay/itoa |
| Rust/Cargo | javascriptcore-rs | 1.1.2 | MIT | https://github.com/tauri-apps/javascriptcore-rs |
| Rust/Cargo | javascriptcore-rs-sys | 1.1.1 | MIT | https://github.com/tauri-apps/javascriptcore-rs |
| Rust/Cargo | jiff | 0.2.39 | Unlicense OR MIT | https://github.com/BurntSushi/jiff |
| Rust/Cargo | jiff-core | 0.1.2 | Unlicense OR MIT | https://github.com/BurntSushi/jiff |
| Rust/Cargo | jiff-static | 0.2.39 | Unlicense OR MIT | https://github.com/BurntSushi/jiff |
| Rust/Cargo | jiff-tzdb | 0.1.9 | Unlicense OR MIT | https://github.com/BurntSushi/jiff |
| Rust/Cargo | jiff-tzdb-platform | 0.1.3 | Unlicense OR MIT | https://github.com/BurntSushi/jiff |
| Rust/Cargo | jni | 0.21.1 | MIT/Apache-2.0 | https://github.com/jni-rs/jni-rs |
| Rust/Cargo | jni-sys | 0.3.1 | MIT OR Apache-2.0 | https://github.com/jni-rs/jni-sys |
| Rust/Cargo | jni-sys | 0.4.1 | MIT OR Apache-2.0 | https://github.com/jni-rs/jni-sys |
| Rust/Cargo | jni-sys-macros | 0.4.1 | MIT OR Apache-2.0 | https://github.com/jni-rs/jni-sys |
| Rust/Cargo | js-sys | 0.3.107 | MIT OR Apache-2.0 | https://github.com/wasm-bindgen/wasm-bindgen/tree/master/crates/js-sys |
| Rust/Cargo | json-patch | 4.2.0 | MIT/Apache-2.0 | https://github.com/idubrov/json-patch |
| Rust/Cargo | jsonptr | 0.7.1 | MIT OR Apache-2.0 | https://github.com/chanced/jsonptr |
| Rust/Cargo | keyboard-types | 0.8.3 | MIT OR Apache-2.0 | https://github.com/rust-windowing/keyboard-types |
| Rust/Cargo | libappindicator | 0.9.0 | Apache-2.0 OR MIT | registry+https://github.com/rust-lang/crates.io-index |
| Rust/Cargo | libappindicator-sys | 0.9.0 | Apache-2.0 OR MIT | registry+https://github.com/rust-lang/crates.io-index |
| Rust/Cargo | libc | 0.2.191 | MIT OR Apache-2.0 | https://github.com/rust-lang/libc |
| Rust/Cargo | libdbus-sys | 0.2.7 | Apache-2.0/MIT | https://github.com/diwic/dbus-rs |
| Rust/Cargo | libloading | 0.7.4 | ISC | https://github.com/nagisa/rust_libloading/ |
| Rust/Cargo | libredox | 0.1.25 | MIT | https://gitlab.redox-os.org/redox-os/libredox.git |
| Rust/Cargo | litemap | 0.8.3 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | lock_api | 0.4.14 | MIT OR Apache-2.0 | https://github.com/Amanieu/parking_lot |
| Rust/Cargo | log | 0.4.35 | MIT OR Apache-2.0 | https://github.com/rust-lang/log |
| Rust/Cargo | markup5ever | 0.39.0 | MIT OR Apache-2.0 | https://github.com/servo/html5ever |
| Rust/Cargo | memchr | 2.8.3 | Unlicense OR MIT | https://github.com/BurntSushi/memchr |
| Rust/Cargo | memoffset | 0.9.1 | MIT | https://github.com/Gilnaa/memoffset |
| Rust/Cargo | mime | 0.3.17 | MIT OR Apache-2.0 | https://github.com/hyperium/mime |
| Rust/Cargo | miniz_oxide | 0.8.9 | MIT OR Zlib OR Apache-2.0 | https://github.com/Frommi/miniz_oxide/tree/master/miniz_oxide |
| Rust/Cargo | miniz_oxide | 0.9.1 | MIT OR Zlib OR Apache-2.0 | https://github.com/Frommi/miniz_oxide/tree/master/miniz_oxide |
| Rust/Cargo | mio | 1.2.4 | MIT | https://github.com/tokio-rs/mio |
| Rust/Cargo | muda | 0.20.0 | Apache-2.0 OR MIT | https://github.com/tauri-apps/muda |
| Rust/Cargo | ndk | 0.9.0 | MIT OR Apache-2.0 | https://github.com/rust-mobile/ndk |
| Rust/Cargo | ndk-context | 0.1.1 | MIT OR Apache-2.0 | https://github.com/rust-windowing/android-ndk-rs |
| Rust/Cargo | ndk-sys | 0.6.0+11769913 | MIT OR Apache-2.0 | https://github.com/rust-mobile/ndk |
| Rust/Cargo | new_debug_unreachable | 1.0.6 | MIT | https://github.com/mbrubeck/rust-debug-unreachable |
| Rust/Cargo | num-conv | 0.2.2 | MIT OR Apache-2.0 | https://github.com/jhpratt/num-conv |
| Rust/Cargo | num-traits | 0.2.19 | MIT OR Apache-2.0 | https://github.com/rust-num/num-traits |
| Rust/Cargo | num_enum | 0.7.6 | BSD-3-Clause OR MIT OR Apache-2.0 | https://github.com/illicitonion/num_enum |
| Rust/Cargo | num_enum_derive | 0.7.6 | BSD-3-Clause OR MIT OR Apache-2.0 | https://github.com/illicitonion/num_enum |
| Rust/Cargo | objc2 | 0.6.5 | MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-app-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-cloud-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-core-data | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-core-foundation | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-core-graphics | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-core-image | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-core-location | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-core-text | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-encode | 4.1.0 | MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-exception-helper | 0.1.1 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-foundation | 0.3.2 | MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-io-surface | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-quartz-core | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-ui-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-user-notifications | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | objc2-web-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT | https://github.com/madsmtm/objc2 |
| Rust/Cargo | once_cell | 1.21.4 | MIT OR Apache-2.0 | https://github.com/matklad/once_cell |
| Rust/Cargo | option-ext | 0.2.0 | MPL-2.0 | https://github.com/soc/option-ext.git |
| Rust/Cargo | pango | 0.18.3 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | pango-sys | 0.18.0 | MIT | https://github.com/gtk-rs/gtk-rs-core |
| Rust/Cargo | parking_lot | 0.12.5 | MIT OR Apache-2.0 | https://github.com/Amanieu/parking_lot |
| Rust/Cargo | parking_lot_core | 0.9.12 | MIT OR Apache-2.0 | https://github.com/Amanieu/parking_lot |
| Rust/Cargo | percent-encoding | 2.3.2 | MIT OR Apache-2.0 | https://github.com/servo/rust-url/ |
| Rust/Cargo | phf | 0.13.1 | MIT | https://github.com/rust-phf/rust-phf |
| Rust/Cargo | phf_codegen | 0.13.1 | MIT | https://github.com/rust-phf/rust-phf |
| Rust/Cargo | phf_generator | 0.13.1 | MIT | https://github.com/rust-phf/rust-phf |
| Rust/Cargo | phf_macros | 0.13.1 | MIT | https://github.com/rust-phf/rust-phf |
| Rust/Cargo | phf_shared | 0.13.1 | MIT | https://github.com/rust-phf/rust-phf |
| Rust/Cargo | pin-project-lite | 0.2.17 | Apache-2.0 OR MIT | https://github.com/taiki-e/pin-project-lite |
| Rust/Cargo | pkg-config | 0.3.34 | MIT OR Apache-2.0 | https://github.com/rust-lang/pkg-config-rs |
| Rust/Cargo | plist | 1.10.3 | MIT | https://github.com/ebarnard/rust-plist/ |
| Rust/Cargo | png | 0.18.1 | MIT OR Apache-2.0 | https://github.com/image-rs/image-png |
| Rust/Cargo | portable-atomic | 1.15.0 | Apache-2.0 OR MIT | https://github.com/taiki-e/portable-atomic |
| Rust/Cargo | portable-atomic-util | 0.2.8 | Apache-2.0 OR MIT | https://github.com/taiki-e/portable-atomic-util |
| Rust/Cargo | potential_utf | 0.1.6 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | powerfmt | 0.2.1 | MIT OR Apache-2.0 | https://github.com/jhpratt/powerfmt |
| Rust/Cargo | precomputed-hash | 0.1.1 | MIT | https://github.com/emilio/precomputed-hash |
| Rust/Cargo | proc-macro-crate | 1.3.1 | MIT OR Apache-2.0 | https://github.com/bkchr/proc-macro-crate |
| Rust/Cargo | proc-macro-crate | 2.0.2 | MIT OR Apache-2.0 | https://github.com/bkchr/proc-macro-crate |
| Rust/Cargo | proc-macro-crate | 3.5.0 | MIT OR Apache-2.0 | https://github.com/bkchr/proc-macro-crate |
| Rust/Cargo | proc-macro-error | 1.0.4 | MIT OR Apache-2.0 | https://gitlab.com/CreepySkeleton/proc-macro-error |
| Rust/Cargo | proc-macro-error-attr | 1.0.4 | MIT OR Apache-2.0 | https://gitlab.com/CreepySkeleton/proc-macro-error |
| Rust/Cargo | proc-macro2 | 1.0.107 | MIT OR Apache-2.0 | https://github.com/dtolnay/proc-macro2 |
| Rust/Cargo | quick-xml | 0.42.0 | MIT | https://github.com/tafia/quick-xml |
| Rust/Cargo | quote | 1.0.47 | MIT OR Apache-2.0 | https://github.com/dtolnay/quote |
| Rust/Cargo | r-efi | 5.3.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later | https://github.com/r-efi/r-efi |
| Rust/Cargo | r-efi | 6.0.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later | https://github.com/r-efi/r-efi |
| Rust/Cargo | raw-window-handle | 0.6.2 | MIT OR Apache-2.0 OR Zlib | https://github.com/rust-windowing/raw-window-handle |
| Rust/Cargo | redox_syscall | 0.5.18 | MIT | https://gitlab.redox-os.org/redox-os/syscall |
| Rust/Cargo | redox_users | 0.5.3 | MIT | https://gitlab.redox-os.org/redox-os/users |
| Rust/Cargo | ref-cast | 1.0.27 | MIT OR Apache-2.0 | https://github.com/dtolnay/ref-cast |
| Rust/Cargo | ref-cast-impl | 1.0.27 | MIT OR Apache-2.0 | https://github.com/dtolnay/ref-cast |
| Rust/Cargo | regex | 1.13.1 | MIT OR Apache-2.0 | https://github.com/rust-lang/regex |
| Rust/Cargo | regex-automata | 0.4.18 | MIT OR Apache-2.0 | https://github.com/rust-lang/regex |
| Rust/Cargo | regex-syntax | 0.8.11 | MIT OR Apache-2.0 | https://github.com/rust-lang/regex |
| Rust/Cargo | reqwest | 0.13.5 | MIT OR Apache-2.0 | https://github.com/seanmonstar/reqwest |
| Rust/Cargo | rfd | 0.17.2 | MIT | https://github.com/PolyMeilex/rfd |
| Rust/Cargo | rustc-hash | 2.1.3 | Apache-2.0 OR MIT | https://github.com/rust-lang/rustc-hash |
| Rust/Cargo | rustc_version | 0.4.1 | MIT OR Apache-2.0 | https://github.com/djc/rustc-version-rs |
| Rust/Cargo | rustversion | 1.0.23 | MIT OR Apache-2.0 | https://github.com/dtolnay/rustversion |
| Rust/Cargo | same-file | 1.0.6 | Unlicense/MIT | https://github.com/BurntSushi/same-file |
| Rust/Cargo | schemars | 0.8.22 | MIT | https://github.com/GREsau/schemars |
| Rust/Cargo | schemars | 0.9.0 | MIT | https://github.com/GREsau/schemars |
| Rust/Cargo | schemars | 1.2.3 | MIT | https://github.com/GREsau/schemars |
| Rust/Cargo | schemars_derive | 0.8.22 | MIT | https://github.com/GREsau/schemars |
| Rust/Cargo | scopeguard | 1.2.0 | MIT OR Apache-2.0 | https://github.com/bluss/scopeguard |
| Rust/Cargo | selectors | 0.38.0 | MPL-2.0 | https://github.com/servo/stylo |
| Rust/Cargo | semver | 1.0.28 | MIT OR Apache-2.0 | https://github.com/dtolnay/semver |
| Rust/Cargo | serde | 1.0.229 | MIT OR Apache-2.0 | https://github.com/serde-rs/serde |
| Rust/Cargo | serde-untagged | 0.1.9 | MIT OR Apache-2.0 | https://github.com/dtolnay/serde-untagged |
| Rust/Cargo | serde_core | 1.0.229 | MIT OR Apache-2.0 | https://github.com/serde-rs/serde |
| Rust/Cargo | serde_derive | 1.0.229 | MIT OR Apache-2.0 | https://github.com/serde-rs/serde |
| Rust/Cargo | serde_derive_internals | 0.29.1 | MIT OR Apache-2.0 | https://github.com/serde-rs/serde |
| Rust/Cargo | serde_json | 1.0.154 | MIT OR Apache-2.0 | https://github.com/serde-rs/json |
| Rust/Cargo | serde_repr | 0.1.21 | MIT OR Apache-2.0 | https://github.com/dtolnay/serde-repr |
| Rust/Cargo | serde_spanned | 0.6.9 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | serde_spanned | 1.1.2 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | serde_with | 3.24.0 | MIT OR Apache-2.0 | https://github.com/jonasbb/serde_with/ |
| Rust/Cargo | serde_with_macros | 3.24.0 | MIT OR Apache-2.0 | https://github.com/jonasbb/serde_with/ |
| Rust/Cargo | serialize-to-javascript | 0.1.2 | MIT OR Apache-2.0 | https://github.com/chippers/serialize-to-javascript |
| Rust/Cargo | serialize-to-javascript-impl | 0.1.2 | MIT OR Apache-2.0 | https://github.com/chippers/serialize-to-javascript |
| Rust/Cargo | servo_arc | 0.4.3 | MIT OR Apache-2.0 | https://github.com/servo/stylo |
| Rust/Cargo | sha2 | 0.10.9 | MIT OR Apache-2.0 | https://github.com/RustCrypto/hashes |
| Rust/Cargo | shlex | 2.0.1 | MIT OR Apache-2.0 | https://github.com/comex/rust-shlex |
| Rust/Cargo | simd-adler32 | 0.3.10 | MIT | https://github.com/mcountryman/simd-adler32 |
| Rust/Cargo | siphasher | 1.0.4 | MIT OR Apache-2.0 | https://github.com/jedisct1/rust-siphash |
| Rust/Cargo | slab | 0.4.12 | MIT | https://github.com/tokio-rs/slab |
| Rust/Cargo | smallvec | 1.16.4 | MIT OR Apache-2.0 | https://github.com/servo/rust-smallvec |
| Rust/Cargo | socket2 | 0.6.5 | MIT OR Apache-2.0 | https://github.com/rust-lang/socket2 |
| Rust/Cargo | softbuffer | 0.4.8 | MIT OR Apache-2.0 | https://github.com/rust-windowing/softbuffer |
| Rust/Cargo | soup3 | 0.5.0 | MIT | https://gitlab.gnome.org/World/Rust/soup3-rs |
| Rust/Cargo | soup3-sys | 0.5.0 | MIT | https://gitlab.gnome.org/World/Rust/soup3-rs |
| Rust/Cargo | stable_deref_trait | 1.2.1 | MIT OR Apache-2.0 | https://github.com/storyyeller/stable_deref_trait |
| Rust/Cargo | static_vcruntime | 3.0.0 | MIT OR Apache-2.0 OR Zlib | https://github.com/ChrisDenton/static_vcruntime |
| Rust/Cargo | string_cache | 0.9.0 | MIT OR Apache-2.0 | https://github.com/servo/string-cache |
| Rust/Cargo | string_cache_codegen | 0.6.1 | MIT OR Apache-2.0 | https://github.com/servo/string-cache |
| Rust/Cargo | strsim | 0.11.1 | MIT | https://github.com/rapidfuzz/strsim-rs |
| Rust/Cargo | swift-rs | 1.0.9 | MIT OR Apache-2.0 | https://github.com/Brendonovich/swift-rs |
| Rust/Cargo | syn | 1.0.109 | MIT OR Apache-2.0 | https://github.com/dtolnay/syn |
| Rust/Cargo | syn | 2.0.119 | MIT OR Apache-2.0 | https://github.com/dtolnay/syn |
| Rust/Cargo | syn | 3.0.9 | MIT OR Apache-2.0 | https://github.com/dtolnay/syn |
| Rust/Cargo | sync_wrapper | 1.0.2 | Apache-2.0 | https://github.com/Actyx/sync_wrapper |
| Rust/Cargo | synstructure | 0.14.0 | MIT | https://github.com/mystor/synstructure |
| Rust/Cargo | system-deps | 6.2.2 | MIT OR Apache-2.0 | https://github.com/gdesmott/system-deps |
| Rust/Cargo | tao | 0.37.2 | Apache-2.0 | https://github.com/tauri-apps/tao |
| Rust/Cargo | tao-macros | 0.1.4 | MIT OR Apache-2.0 | https://github.com/tauri-apps/tao |
| Rust/Cargo | target-lexicon | 0.12.16 | Apache-2.0 WITH LLVM-exception | https://github.com/bytecodealliance/target-lexicon |
| Rust/Cargo | tauri | 2.12.5 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-build | 2.7.4 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-codegen | 2.7.4 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-macros | 2.7.4 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-plugin | 2.7.2 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-plugin-dialog | 2.8.4 | Apache-2.0 OR MIT | https://github.com/tauri-apps/plugins-workspace |
| Rust/Cargo | tauri-plugin-fs | 2.7.1 | Apache-2.0 OR MIT | https://github.com/tauri-apps/plugins-workspace |
| Rust/Cargo | tauri-runtime | 2.12.2 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-runtime-wry | 2.12.3 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-utils | 2.10.2 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri |
| Rust/Cargo | tauri-winres | 0.3.7 | MIT | https://github.com/tauri-apps/winres |
| Rust/Cargo | tendril | 0.5.1 | MIT OR Apache-2.0 | https://github.com/servo/html5ever |
| Rust/Cargo | thiserror | 1.0.69 | MIT OR Apache-2.0 | https://github.com/dtolnay/thiserror |
| Rust/Cargo | thiserror | 2.0.21 | MIT OR Apache-2.0 | https://github.com/dtolnay/thiserror |
| Rust/Cargo | thiserror-impl | 1.0.69 | MIT OR Apache-2.0 | https://github.com/dtolnay/thiserror |
| Rust/Cargo | thiserror-impl | 2.0.21 | MIT OR Apache-2.0 | https://github.com/dtolnay/thiserror |
| Rust/Cargo | time | 0.3.55 | MIT OR Apache-2.0 | https://github.com/time-rs/time |
| Rust/Cargo | time-core | 0.1.9 | MIT OR Apache-2.0 | https://github.com/time-rs/time |
| Rust/Cargo | time-macros | 0.2.32 | MIT OR Apache-2.0 | https://github.com/time-rs/time |
| Rust/Cargo | tinystr | 0.8.5 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | tinyvec | 1.13.3 | Zlib OR Apache-2.0 OR MIT | https://github.com/Lokathor/tinyvec |
| Rust/Cargo | tokio | 1.54.1 | MIT | https://github.com/tokio-rs/tokio |
| Rust/Cargo | tokio-util | 0.7.20 | MIT | https://github.com/tokio-rs/tokio |
| Rust/Cargo | toml | 0.8.2 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml | 1.1.8+spec-1.1.0 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml_datetime | 0.6.3 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml_datetime | 1.1.2+spec-1.1.0 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml_edit | 0.19.15 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml_edit | 0.20.2 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml_edit | 0.25.17+spec-1.1.0 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml_parser | 1.1.5+spec-1.1.0 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | toml_writer | 1.1.3+spec-1.1.0 | MIT OR Apache-2.0 | https://github.com/toml-rs/toml |
| Rust/Cargo | tower | 0.5.3 | MIT | https://github.com/tower-rs/tower |
| Rust/Cargo | tower-http | 0.6.11 | MIT | https://github.com/tower-rs/tower-http |
| Rust/Cargo | tower-layer | 0.3.3 | MIT | https://github.com/tower-rs/tower |
| Rust/Cargo | tower-service | 0.3.3 | MIT | https://github.com/tower-rs/tower |
| Rust/Cargo | tracing | 0.1.44 | MIT | https://github.com/tokio-rs/tracing |
| Rust/Cargo | tracing-core | 0.1.36 | MIT | https://github.com/tokio-rs/tracing |
| Rust/Cargo | tray-icon | 0.25.1 | MIT OR Apache-2.0 | https://github.com/tauri-apps/tray-icon |
| Rust/Cargo | try-lock | 0.2.5 | MIT | https://github.com/seanmonstar/try-lock |
| Rust/Cargo | typeid | 1.0.3 | MIT OR Apache-2.0 | https://github.com/dtolnay/typeid |
| Rust/Cargo | typenum | 1.20.1 | MIT OR Apache-2.0 | https://github.com/paholg/typenum |
| Rust/Cargo | unicode-ident | 1.0.27 | (MIT OR Apache-2.0) AND Unicode-3.0 | https://github.com/dtolnay/unicode-ident |
| Rust/Cargo | unicode-segmentation | 1.13.3 | MIT OR Apache-2.0 | https://github.com/unicode-rs/unicode-segmentation |
| Rust/Cargo | url | 2.5.8 | MIT OR Apache-2.0 | https://github.com/servo/rust-url |
| Rust/Cargo | urlpattern | 0.6.0 | MIT | https://github.com/denoland/rust-urlpattern |
| Rust/Cargo | utf8_iter | 1.1.0 | Apache-2.0 OR MIT | https://github.com/hsivonen/utf8_iter |
| Rust/Cargo | utf_types | 1.0.0 | Apache-2.0 OR MIT | https://github.com/hsivonen/utf_types |
| Rust/Cargo | uuid | 1.29.0 | Apache-2.0 OR MIT | https://github.com/uuid-rs/uuid |
| Rust/Cargo | version-compare | 0.2.1 | MIT | https://gitlab.com/timvisee/version-compare |
| Rust/Cargo | version_check | 0.9.5 | MIT/Apache-2.0 | https://github.com/SergioBenitez/version_check |
| Rust/Cargo | vswhom | 0.1.0 | MIT | https://github.com/nabijaczleweli/vswhom.rs |
| Rust/Cargo | vswhom-sys | 0.1.3 | MIT | https://github.com/nabijaczleweli/vswhom-sys.rs |
| Rust/Cargo | walkdir | 2.5.0 | Unlicense/MIT | https://github.com/BurntSushi/walkdir |
| Rust/Cargo | want | 0.3.2 | MIT | https://github.com/seanmonstar/want |
| Rust/Cargo | wasi | 0.11.1+wasi-snapshot-preview1 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | https://github.com/bytecodealliance/wasi |
| Rust/Cargo | wasip2 | 1.0.4+wasi-0.2.12 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | https://github.com/bytecodealliance/wasi-rs |
| Rust/Cargo | wasm-bindgen | 0.2.130 | MIT OR Apache-2.0 | https://github.com/wasm-bindgen/wasm-bindgen |
| Rust/Cargo | wasm-bindgen-futures | 0.4.80 | MIT OR Apache-2.0 | https://github.com/wasm-bindgen/wasm-bindgen/tree/master/crates/futures |
| Rust/Cargo | wasm-bindgen-macro | 0.2.130 | MIT OR Apache-2.0 | https://github.com/wasm-bindgen/wasm-bindgen/tree/master/crates/macro |
| Rust/Cargo | wasm-bindgen-macro-support | 0.2.130 | MIT OR Apache-2.0 | https://github.com/wasm-bindgen/wasm-bindgen/tree/main/crates/macro-support |
| Rust/Cargo | wasm-bindgen-shared | 0.2.130 | MIT OR Apache-2.0 | https://github.com/wasm-bindgen/wasm-bindgen/tree/master/crates/shared |
| Rust/Cargo | wasm-streams | 0.5.0 | MIT OR Apache-2.0 | https://github.com/MattiasBuelens/wasm-streams/ |
| Rust/Cargo | web-sys | 0.3.107 | MIT OR Apache-2.0 | https://github.com/wasm-bindgen/wasm-bindgen/tree/master/crates/web-sys |
| Rust/Cargo | web-time | 1.1.0 | MIT OR Apache-2.0 | https://github.com/daxpedda/web-time |
| Rust/Cargo | web_atoms | 0.2.6 | MIT OR Apache-2.0 | https://github.com/servo/html5ever |
| Rust/Cargo | webkit2gtk | 2.0.2 | MIT | https://github.com/tauri-apps/webkit2gtk-rs |
| Rust/Cargo | webkit2gtk-sys | 2.0.2 | MIT | https://github.com/tauri-apps/webkit2gtk-rs |
| Rust/Cargo | webview2-com | 0.39.1 | MIT | https://github.com/wravery/webview2-rs |
| Rust/Cargo | webview2-com-macros | 0.8.1 | MIT | https://github.com/wravery/webview2-rs |
| Rust/Cargo | webview2-com-sys | 0.39.1 | MIT | https://github.com/wravery/webview2-rs |
| Rust/Cargo | winapi | 0.3.9 | MIT/Apache-2.0 | https://github.com/retep998/winapi-rs |
| Rust/Cargo | winapi-i686-pc-windows-gnu | 0.4.0 | MIT/Apache-2.0 | https://github.com/retep998/winapi-rs |
| Rust/Cargo | winapi-util | 0.1.11 | Unlicense OR MIT | https://github.com/BurntSushi/winapi-util |
| Rust/Cargo | winapi-x86_64-pc-windows-gnu | 0.4.0 | MIT/Apache-2.0 | https://github.com/retep998/winapi-rs |
| Rust/Cargo | window-vibrancy | 0.8.1 | Apache-2.0 OR MIT | https://github.com/tauri-apps/tauri-plugin-vibrancy |
| Rust/Cargo | windows | 0.62.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-collections | 0.3.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-core | 0.62.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-future | 0.3.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-implement | 0.60.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-interface | 0.59.3 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-link | 0.2.1 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-numerics | 0.3.1 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-result | 0.4.1 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-strings | 0.5.1 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-sys | 0.45.0 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-sys | 0.61.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-targets | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-threading | 0.2.1 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows-version | 0.1.7 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows_aarch64_gnullvm | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows_aarch64_msvc | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows_i686_gnu | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows_i686_msvc | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows_x86_64_gnu | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows_x86_64_gnullvm | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | windows_x86_64_msvc | 0.42.2 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| Rust/Cargo | winnow | 0.5.40 | MIT | https://github.com/winnow-rs/winnow |
| Rust/Cargo | winnow | 1.0.4 | MIT | https://github.com/winnow-rs/winnow |
| Rust/Cargo | winreg | 0.56.0 | MIT | https://github.com/gentoo90/winreg-rs |
| Rust/Cargo | wit-bindgen | 0.57.1 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | https://github.com/bytecodealliance/wit-bindgen |
| Rust/Cargo | writeable | 0.6.4 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | wry | 0.57.1 | Apache-2.0 OR MIT | https://github.com/tauri-apps/wry |
| Rust/Cargo | x11 | 2.21.0 | MIT | https://github.com/AltF02/x11-rs.git |
| Rust/Cargo | x11-dl | 2.21.0 | MIT | https://github.com/AltF02/x11-rs.git |
| Rust/Cargo | xaa-desktop | 0.1.1 | NO LICENSE METADATA | — |
| Rust/Cargo | yoke | 0.8.4 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | yoke-derive | 0.8.4 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | zerofrom | 0.1.8 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | zerofrom-derive | 0.1.8 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | zerotrie | 0.2.5 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | zerovec | 0.11.9 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | zerovec-derive | 0.11.7 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| Rust/Cargo | zlib-rs | 0.6.8 | Zlib | https://github.com/trifectatechfoundation/zlib-rs |
| Rust/Cargo | zmij | 1.0.23 | MIT | https://github.com/dtolnay/zmij |

## Regeneration

Run the three collection commands listed under Scope and collection after dependency or lockfile changes, inspect all unknown/conflicting licenses and advisories, then regenerate this package table from their JSON outputs. Do not treat a clean metadata scan as approval to ship until full texts, project licensing, and the installer notice bundle are reviewed.
