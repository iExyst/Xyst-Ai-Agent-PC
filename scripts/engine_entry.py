"""Frozen engine entry point used by the desktop sidecar."""

if __name__ == "__main__":
    import sys

    if sys.argv[1:] and sys.argv[1] == "--cli":
        # The installed sidecar doubles as the ACP CLI executable. Keep this
        # dispatch explicit so normal Tauri launches still start the API.
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from xaa.cli import main
    elif sys.argv[1:] == ["--tool-worker"]:
        from xaa.runtime.tool_worker import main
    elif sys.argv[1:] == ["--snapshot-worker"]:
        from xaa.runtime.snapshot_worker import main
    elif sys.argv[1:] and sys.argv[1] == "--windows-appcontainer-probe":
        from xaa.runtime.windows_local_probe import main

        raise SystemExit(main(sys.argv[2:]))
    elif sys.argv[1:] == ["--tokenizer-worker"]:
        from xaa.providers.tokenizer_worker import main
    elif sys.argv[1:] == ["--pypi-resolver-worker"]:
        from xaa.extensions.pypi_resolver_worker import main
    elif sys.argv[1:] == ["--mcp-appcontainer-shim"]:
        from xaa.runtime.mcp_appcontainer import run_stdio_appcontainer_shim

        raise SystemExit(run_stdio_appcontainer_shim())
    elif sys.argv[1:] == ["--browser-worker"]:
        from xaa.runtime.browser_worker import main
    else:
        from xaa.api.__main__ import main
    main()
