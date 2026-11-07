# Dependency issues

## OpenAPI generator development dependency

A 2026-11-03 audit reported GHSA-g3hv-75wg-m249 through `openapi-typescript@7.13.0` → `@redocly/openapi-core@1.34.23` → `minimatch@5.1.9`. An override to `@redocly/openapi-core@2.73.0` made that audit clean but broke schema generation with `ERR_PACKAGE_PATH_NOT_EXPORTED` for `./lib/ref-utils.js`, so it was reverted.

Re-audit on 2026-11-07: `npm audit --json` reports zero vulnerabilities for the current `package-lock.json`; the same package versions remain locked. The previous advisory no longer appears in the current scanner result. Do not reinstate the incompatible override. Evidence is `.acceptance/dependency-inventory-971fa5b/npm-audit.json`. Re-run `npm audit` after dependency changes.
Tokenizer service dependency: `lmstudio==1.6.0b1` is pinned as a prerelease because stable 1.5.0 lacks the documented API-token parameter. Real fixture parity and synthetic authentication/process-cancellation checks pass; source/frozen evidence is in `../architecture/tokenizer-service.md`. Track stable SDK availability and clean-machine packaging. A provisional license-text draft and metadata inventory are in [the release inventory](dependency-inventory.md) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md); confirm completeness before packaging.
