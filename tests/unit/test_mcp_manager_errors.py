from types import SimpleNamespace

from xaa.runtime.mcp_manager import MCPManager


def test_stdio_launch_error_reports_os_code_without_leaking_path() -> None:
    path = r"C:\Users\fixture\private\secret-server.exe"
    error = OSError(13, "Permission denied", path)
    code = getattr(error, "winerror", None) or error.errno

    message = MCPManager._safe_error(
        SimpleNamespace(credential_store=None),
        error,
        SimpleNamespace(secret_env=[]),
    )

    assert f"error {code}" in message
    assert "Could not start the MCP server" in message
    assert path not in message
