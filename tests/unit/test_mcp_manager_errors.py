from types import SimpleNamespace

from xaa.runtime.mcp_appcontainer import (
    MCPAppContainerError,
    startup_error_frame,
    startup_error_message,
)
from xaa.runtime.mcp_manager import MCPManager
from xaa.runtime.mcp_stdio import MCPStdioResourceLimitError


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


def test_stdio_launch_error_without_os_code_keeps_safe_exception_class() -> None:
    message = MCPManager._safe_error(
        SimpleNamespace(credential_store=None),
        OSError("Native launch failure"),
        SimpleNamespace(secret_env=[]),
    )

    assert " (OSError)." in message
    assert "Native launch failure" not in message


def test_timeout_error_is_reported_as_initialization_timeout() -> None:
    message = MCPManager._safe_error(
        SimpleNamespace(credential_store=None),
        TimeoutError(),
        SimpleNamespace(secret_env=[]),
    )

    assert message == "The MCP server did not complete initialization or sign-in in time."


def test_appcontainer_startup_frame_preserves_only_os_code() -> None:
    path = r"D:\runner\private\python.exe"
    try:
        try:
            raise OSError(5, "Access denied", path)
        except OSError as cause:
            raise MCPAppContainerError("Windows could not grant access.") from cause
    except MCPAppContainerError as error:
        frame = startup_error_frame(error)

    message = startup_error_message(frame)

    assert message == "Windows could not start the network-isolated MCP server (Windows error 5)."
    assert path not in message


def test_mcp_manager_preserves_sanitized_resource_limit_diagnostic() -> None:
    error = MCPStdioResourceLimitError(
        "Windows could not start the network-isolated MCP server (Windows error 5)."
    )

    message = MCPManager._safe_error(
        SimpleNamespace(credential_store=None), error, SimpleNamespace(secret_env=[])
    )

    assert message == str(error)
