"""Run the Phase 3 workspace-edit slice with a scripted provider and real WSL sandbox."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import AdmissionCallback, DiagnosticRequest, LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.execution_readiness import ExecutionReadiness, ReadinessRequest
from xaa.runtime.project_validation import ProjectValidationRequest, summary, validate_project
from xaa.storage.runs import RunStore


def tool_call(identity: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": identity,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, separators=(",", ":")),
                            },
                        }
                    ],
                }
            }
        ]
    }


class FixtureProvider(LMStudioProvider):
    """Script the normal coordinator protocol without making model requests."""

    async def diagnose_tools(self, model: str, *, request: DiagnosticRequest | None = None) -> bool:
        return True

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        max_output_tokens: int | None = None,
        on_admission: AdmissionCallback | None = None,
        require_exact_context: bool = False,
    ) -> dict[str, object]:
        latest = messages[-1]
        if latest["role"] == "user":
            return tool_call("read-1", "workspace_read_file", {"path": "answer.py"})

        if latest["role"] != "tool":
            raise AssertionError(f"Unexpected coordinator prompt role: {latest['role']!r}")
        content = latest.get("content")
        if not isinstance(content, str):
            raise TypeError("The coordinator tool result must be serialized text.")
        result = json.loads(content)
        call_id = latest["tool_call_id"]
        if call_id == "read-1":
            if "return 41" not in result.get("text", ""):
                raise AssertionError("The fixture defect was not present in the workspace read.")
            return tool_call(
                "edit-1",
                "workspace_write_file",
                {
                    "path": "answer.py",
                    "content": "def answer():\n    return 42\n",
                    "expected_sha256": result["sha256"],
                },
            )
        if call_id == "edit-1":
            if result.get("bytes_written") != len(b"def answer():\n    return 42\n"):
                raise AssertionError("The coordinator did not persist the expected source edit.")
            command = (
                "from runpy import run_path; "
                "run_path('/workspace/tests/test_answer.py'); "
                "print('1 passed')"
            )
            return tool_call(
                "check-1",
                "workspace_run_command",
                {
                    "argv": ["/usr/bin/python3", "-I", "-c", command],
                    "max_seconds": 15,
                },
            )
        if call_id == "check-1":
            if result.get("succeeded") is not True or result.get("exit_code") != 0:
                raise AssertionError("The isolated workspace validation did not pass.")
            if "1 passed" not in result.get("stdout_excerpt", ""):
                raise AssertionError("The isolated validation output is missing its pass marker.")
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Fixed `answer.py`; the restricted project check passed (1 passed).",
                        }
                    }
                ]
            }
        raise AssertionError(f"Unexpected scripted tool result: {call_id!r}")


async def diagnose(root: Path, distribution: str) -> dict[str, object]:
    readiness = await ExecutionReadiness().check(ReadinessRequest(distribution=distribution))
    if readiness.state != "ready" or not readiness.profile_sha256:
        raise RuntimeError(
            f"Selected WSL sandbox is not ready: {readiness.error_code or readiness.state}."
        )

    project = root / "project"
    project.mkdir(parents=True, exist_ok=False)
    (project / "tests").mkdir()
    original = b"def answer():\n    return 41\n"
    (project / "answer.py").write_bytes(original)
    (project / "tests" / "test_answer.py").write_text(
        "from runpy import run_path\n"
        "answer = run_path('/workspace/answer.py')['answer']\n"
        "assert answer() == 42\n",
        encoding="utf-8",
    )

    store = RunStore(root / "data" / "XAA" / "xaa.sqlite3")
    grant = {
        "backend": "wsl-bubblewrap-v1",
        "distribution": distribution,
        "profile_sha256": readiness.profile_sha256,
        "max_seconds": 20,
    }
    run = store.create_run(
        objective="Diagnose the answer.py defect, correct it, and run the project check.",
        acceptance_criteria=["answer() returns 42 and the isolated check passes"],
        workspace=str(project),
        mode="workspace",
        model="fixture-no-inference",
        command_grant=grant,
        max_iterations=8,
        max_seconds=120,
    )
    provider = FixtureProvider()
    try:
        await Coordinator(store, provider).execute(run["id"])
    finally:
        await provider.close()

    current = store.get_run(run["id"])
    if current is None or current["state"] != "verifying":
        raise AssertionError("The edited workspace did not reach independent final review.")
    edited = (project / "answer.py").read_bytes()
    if edited != b"def answer():\n    return 42\n":
        raise AssertionError("The coordinator did not leave the reviewed source candidate.")
    model_check_result = store.public_tool_result(run["id"], "check-1")
    if not isinstance(model_check_result, str):
        raise TypeError("The coordinator validation result must be serialized text.")
    model_check = json.loads(model_check_result)
    if model_check.get("succeeded") is not True or "1 passed" not in model_check.get(
        "stdout_excerpt", ""
    ):
        raise AssertionError("The coordinator did not retain its passing restricted check.")

    validation_command = (
        "from runpy import run_path; run_path('/workspace/tests/test_answer.py'); print('1 passed')"
    )
    validation = await validate_project(
        store,
        run["id"],
        ProjectValidationRequest(
            argv=["/usr/bin/python3", "-I", "-c", validation_command],
            max_seconds=15,
            request_id=uuid4().hex,
            expected_revision=current["revision"],
        ),
    )
    validation_owner = store.acquire_lease(
        run["id"], "workspace-vertical-slice-summary", seconds=30
    )
    if validation_owner is None:
        raise RuntimeError("Could not acquire the final evidence summary lease.")
    try:
        evidence = await summary(store, run["id"], validation_owner)
    finally:
        store.release_lease(run["id"], validation_owner)
    if (
        validation.get("succeeded") is not True
        or validation.get("exit_code") != 0
        or evidence.required is not True
        or not evidence.evidence
        or evidence.evidence[-1].succeeded is not True
        or evidence.evidence[-1].matches_current_project is not True
        or evidence.unresolved_call_ids
    ):
        raise AssertionError("Fresh final-project validation evidence is incomplete or stale.")

    return {
        "passed": True,
        "run_id": run["id"],
        "state": current["state"],
        "distribution": distribution,
        "workspace": str(project),
        "initial_sha256": hashlib.sha256(original).hexdigest(),
        "final_sha256": hashlib.sha256(edited).hexdigest(),
        "changed_files": ["answer.py"],
        "coordinator_validation": model_check,
        "final_project_validation": validation,
        "final_project_validation_matches_current_tree": evidence.evidence[
            -1
        ].matches_current_project,
        "unresolved_calls": evidence.unresolved_call_ids,
        "model_inference_requested": False,
        "network_access_in_command_sandbox": "denied",
        "workspace_changes_from_commands": "discarded",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    root = args.root or Path.cwd() / ".acceptance" / f"workspace-vertical-slice-{uuid4().hex}"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    try:
        report = asyncio.run(diagnose(root, args.distribution))
        report_path = root / "report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps({"passed": True, "report": str(report_path), "run_id": report["run_id"]}))
        return 0
    except Exception as error:  # noqa: BLE001 - diagnostic preserves a bounded failure record
        report = {
            "passed": False,
            "distribution": args.distribution,
            "model_inference_requested": False,
            "failure_type": type(error).__name__,
            "failure": str(error)[:1000],
        }
        report_path = root / "report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            json.dumps({"passed": False, "report": str(report_path), "failure": report["failure"]})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
