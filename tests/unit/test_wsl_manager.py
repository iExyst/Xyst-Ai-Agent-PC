import base64
import copy

import pytest
from xaa.runtime import sandbox_profile, wsl_manager
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("execution_id", "../../other.service"),
        ("argv", "python -c unsafe"),
        ("argv", []),
        ("argv", ["python", 1]),
        ("argv", ["python", "bad\0argument"]),
        ("argv", [""]),
        ("argv", ["python", "x" * 16385]),
        ("max_seconds", True),
        ("max_seconds", 31),
        ("max_seconds", 0),
        ("snapshot_b64", "invalid base64"),
        ("network", "host"),
    ],
)
def test_invalid_requests_cannot_reach_execution(key, value):
    request = {
        "action": "run",
        "execution_id": "a" * 32,
        "argv": ["/usr/bin/python3"],
        "max_seconds": 5,
        "snapshot_b64": base64.b64encode(WorkspaceSnapshot((), ()).to_bytes()).decode(),
    }
    request = copy.deepcopy(request)
    request[key] = value

    def forbidden():
        pytest.fail("Malformed input reached the runtime before validation.")

    with pytest.raises((ValueError, TypeError)):
        wsl_manager.run(
            request, {"WorkspaceSnapshot": WorkspaceSnapshot}, {"syscall_filter": forbidden}
        )


def test_ambiguous_or_unbounded_envelopes_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        wsl_manager.decode_request(b'{"action":"run","action":"stop"}')
    with pytest.raises(TypeError):
        wsl_manager.decode_request(b"[]")
    with pytest.raises(ValueError, match="transport"):
        wsl_manager.decode_request(b" " * (wsl_manager.MAX_REQUEST_BYTES + 1))


def test_missing_syscall_runtime_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(sandbox_profile.ctypes.util, "find_library", lambda _: None)
    with pytest.raises(RuntimeError, match="libseccomp is required"):
        sandbox_profile.syscall_filter()
