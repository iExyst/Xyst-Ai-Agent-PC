import importlib.util
import json
import sys
import threading
from pathlib import Path

import httpx

scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(scripts_dir))
spec = importlib.util.spec_from_file_location(
    "frozen_windows_appcontainer_command", scripts_dir / "frozen_windows_appcontainer_command.py"
)
assert spec is not None and spec.loader is not None
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)
from frozen_api_restart_diagnostic import APPCONTAINER_COMMAND_MAX_SECONDS, FixtureLMStudio


def test_command_result_diagnostics_capture_bounded_path_safe_probe_tail():
    stderr = (
        b"appcontainer-probe:python-started\n"
        b"appcontainer-probe:private-temp-complete\n"
        b"blocked at C:\\Users\\runner\\AppData\\Local\\Temp\\xaa.tmp\n"
    )
    result = diagnostic._command_result_diagnostics(
        {"elapsed_seconds": 30.1, "supervisor_exit_code": 124, "termination_reason": "timeout"},
        {"stdout": b"", "stderr": stderr},
    )

    assert result["elapsed_seconds"] == 30.1
    assert result["stderr_tail"] == [
        "appcontainer-probe:python-started",
        "appcontainer-probe:private-temp-complete",
        "blocked at <path>",
    ]
    assert "C:\\Users" not in str(result)


def test_appcontainer_fixture_uses_full_saved_command_allowance():
    fixture = FixtureLMStudio(
        "appcontainer_command",
        command_argv=[
            r"C:\Program Files\XAA\xaa-engine.exe",
            "--windows-appcontainer-probe",
            "12345",
        ],
    )
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(
            f"http://127.0.0.1:{fixture.server_address[1]}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Run the approved probe."}],
                "tools": [{"function": {"name": "workspace_run_command"}}],
            },
            timeout=5,
        )
        response.raise_for_status()
        arguments = response.json()["choices"][0]["message"]["tool_calls"][0]["function"][
            "arguments"
        ]
        assert json.loads(arguments)["max_seconds"] == APPCONTAINER_COMMAND_MAX_SECONDS == 30
    finally:
        fixture.shutdown()
        thread.join(timeout=2)
        fixture.server_close()
