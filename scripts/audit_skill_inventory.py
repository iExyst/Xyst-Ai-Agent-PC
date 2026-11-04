"""Reproduce selected pinned skill declarations without executing upstream code."""

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

PIN = "1e208b280deb18f01d015b0acdf3ef3552d08039"
ROOT = Path(".hermes-audit")
OUTPUT = Path("docs/parity/skill-source-inventory.json")
SOURCES = (
    "tools/skills_tool.py",
    "tools/skill_manager_tool.py",
    "tools/skill_manager_guards.py",
    "tools/skill_provenance.py",
    "tools/skill_ledger.py",
)


def inventory() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    for source in SOURCES:
        blob = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{PIN}:{source}"],
            check=True,
            capture_output=True,
        ).stdout
        files.append({"path": source, "git_blob_sha256": hashlib.sha256(blob).hexdigest()})
        tree = ast.parse(blob, filename=source)
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
                continue
            schema = node.value
            for key, value in zip(schema.keys, schema.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "name"
                    and isinstance(value, ast.Constant)
                    and value.value in {"skills_list", "skill_view", "skill_manage"}
                ):
                    tools.append({"name": value.value, "path": source, "line": node.lineno})
                    if value.value == "skill_manage":
                        for call in ast.walk(schema):
                            if (
                                isinstance(call, ast.Call)
                                and isinstance(call.func, ast.Name)
                                and call.func.id == "_op_schema"
                                and call.args
                                and isinstance(call.args[0], ast.Constant)
                                and isinstance(call.args[0].value, str)
                            ):
                                operations.append(
                                    {"action": call.args[0].value, "line": call.lineno}
                                )
    operations.sort(key=lambda operation: operation["line"])
    if len(tools) != 3 or len(operations) != 6:
        raise ValueError("Pinned skill declarations differ; review sources before regeneration.")
    return {
        "reference_commit": PIN,
        "audit_date": "2026-11-04",
        "scope": "selected pinned skill tool schemas and safety/lifecycle source; not every bundled or external skill package",
        "files": files,
        "advertised_tools": tools,
        "manager_operation_shapes": operations,
        "distinct_manager_actions": sorted({operation["action"] for operation in operations}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    encoded = json.dumps(inventory(), indent=2, ensure_ascii=True) + "\n"
    if args.check:
        if OUTPUT.read_text(encoding="utf-8") != encoded:
            raise SystemExit("Skill inventory drifted; inspect pinned sources before regenerating.")
    else:
        OUTPUT.write_text(encoded, encoding="utf-8")
    print("Selected skill audit: 3 tools, 5 manager verbs, 6 operation shapes.")


if __name__ == "__main__":
    main()
