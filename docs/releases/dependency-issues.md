# Dependency issues

## OpenAPI generator development dependency

2026-11-03: openapi-typescript 7.13.0 requires @redocly/openapi-core 1.34.23, which depends on minimatch 5.1.9. npm audit reports GHSA-g3hv-75wg-m249 (glob pattern denial of service), propagating three moderate findings through this chain. The generator currently consumes the repository's local API schema during development/CI.

Tested an override to @redocly/openapi-core 2.73.0: npm audit became clean, but generation failed with ERR_PACKAGE_PATH_NOT_EXPORTED for ./lib/ref-utils.js. The incompatible override was reverted. Do not apply npm's suggested major downgrade or override without verifying required schema behavior and imports. Release resolution remains pending a compatible upstream fix or an audited generator replacement supporting the full API contract.

Tokenizer service dependency: `lmstudio==1.6.0b1` is pinned as a prerelease because stable 1.5.0 lacks the documented API-token parameter. Real fixture parity and synthetic authentication/process-cancellation checks pass; source/frozen evidence is in `../architecture/tokenizer-service.md`. Track stable SDK availability, license notices and clean-machine packaging before release. This does not resolve the pre-existing npm development dependency findings.
