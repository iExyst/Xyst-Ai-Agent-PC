"""Frozen engine entry point used by the desktop sidecar."""

if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--tool-worker"]:
        from xaa.runtime.tool_worker import main
    elif sys.argv[1:] == ["--snapshot-worker"]:
        from xaa.runtime.snapshot_worker import main
    elif sys.argv[1:] == ["--tokenizer-worker"]:
        from xaa.providers.tokenizer_worker import main
    else:
        from xaa.api.__main__ import main
    main()
