import importlib.util
import sys
from pathlib import Path

scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(scripts_dir))
spec = importlib.util.spec_from_file_location(
    "frozen_windows_appcontainer_command", scripts_dir / "frozen_windows_appcontainer_command.py"
)
assert spec is not None and spec.loader is not None
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


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
