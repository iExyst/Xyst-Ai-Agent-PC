"""Compare LM Studio SDK formatted-token counts with actual HTTP prompt usage.

Run with: uv run --with lmstudio==1.5.0 python scripts/tokenizer_parity_diagnostic.py
This is an optional development probe, not an engine SDK dependency.
"""

import argparse
import asyncio
import importlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.providers.tokenization import PromptCountRequest


def sdk_count(
    instance: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> dict[str, int]:
    sdk: Any = importlib.import_module("lmstudio")
    chat = sdk.Chat()
    for message in messages:
        role = message["role"]
        if role == "system":
            chat.add_system_prompt(message["content"])
        elif role == "user":
            chat.add_user_message(message["content"])
        elif role == "tool":
            chat.add_tool_result(
                {
                    "type": "toolCallResult",
                    "content": message["content"],
                    "toolCallId": message["tool_call_id"],
                }
            )
        elif role == "assistant":
            calls = [
                {
                    "type": "function",
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": json.loads(call["function"]["arguments"]),
                }
                for call in message.get("tool_calls", [])
            ]
            chat.add_assistant_response(
                message.get("content") or "",
                [{"type": "toolCallRequest", "toolCallRequest": call} for call in calls],
            )
        else:
            raise ValueError("Probe only supports text/tool message roles.")
    with sdk.Client("127.0.0.1:1234") as client:
        matches = [model for model in client.llm.list_loaded() if model.identifier == instance]
        if len(matches) != 1:
            raise RuntimeError("SDK could not identify the exact loaded HTTP instance.")
        model = matches[0]
        formatted = model.apply_prompt_template(chat, {"toolDefinitions": tools})
        return {
            "sdk_prompt_tokens": len(model.tokenize(formatted)),
            "sdk_context_length": model.get_context_length(),
        }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--engine", action="store_true", help="Use the owned engine tokenizer worker"
    )
    args = parser.parse_args()
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    provider = LMStudioProvider()
    model = "qwen/qwen3.5-9b"
    instance = None
    report: dict[str, Any] = {
        "probe_complete": False,
        "all_counts_match": False,
        "sdk_version": importlib.metadata.version("lmstudio"),
        "model": model,
        "counter": "engine_owned_worker" if args.engine else "direct_sdk_probe",
        "scope": "single installed model; fixture text/tool prompts; not aggregate budget enforcement",
        "cases": [],
    }
    tools = [
        {
            "type": "function",
            "function": {
                "name": "xaa_echo",
                "description": "Return a diagnostic string unchanged.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    user = {"role": "user", "content": "Call xaa_echo with value fixture."}
    call = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "fixture-call",
                "type": "function",
                "function": {"name": "xaa_echo", "arguments": '{"value":"fixture"}'},
            }
        ],
    }
    cases: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = [
        ("plain", [{"role": "user", "content": "Say hello."}], []),
        ("tools", [user], tools),
        (
            "tool_result",
            [user, call, {"role": "tool", "tool_call_id": "fixture-call", "content": "fixture"}],
            tools,
        ),
        (
            "history",
            [
                {"role": "system", "content": "Use concise replies."},
                {"role": "user", "content": "Hello."},
                {"role": "assistant", "content": "Hi."},
                {"role": "user", "content": "Now say hello, 世界."},
            ],
            [],
        ),
    ]
    try:
        await provider.models()
        instance = await provider.load_model(model, context_length=16384)
        for name, messages, definitions in cases:
            if args.engine:
                counted_model = await provider.count_prompt(
                    PromptCountRequest.model_validate(
                        {"model": instance, "messages": messages, "tools": definitions}
                    )
                )
                counted = {
                    "sdk_prompt_tokens": counted_model.prompt_tokens,
                    "sdk_context_length": counted_model.context_length,
                }
            else:
                counted = await asyncio.to_thread(sdk_count, instance, messages, definitions)
            response = await provider.chat(
                model=model, messages=messages, tools=definitions or None, max_output_tokens=1
            )
            actual = response["usage"]["prompt_tokens"]
            report["cases"].append(
                {
                    "case": name,
                    **counted,
                    "http_prompt_tokens": actual,
                    "match": counted["sdk_prompt_tokens"] == actual,
                }
            )
        report["probe_complete"] = True
        report["all_counts_match"] = all(row["match"] for row in report["cases"])
    finally:
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
