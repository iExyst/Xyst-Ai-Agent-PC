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
    sdk: Any, model: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> dict[str, int]:
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
    parser.add_argument(
        "--model",
        default="qwen/qwen3.5-9b",
        help="Installed LM Studio model identifier to compare (default: qwen/qwen3.5-9b)",
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=16384,
        help="Context size for the temporary loaded instance (default: 16384)",
    )
    parser.add_argument(
        "--count-timeout",
        type=float,
        default=5.0,
        help="Maximum seconds for each owned-worker token count (default: 5; maximum: 60)",
    )
    args = parser.parse_args()
    if args.context_length < 512:
        parser.error("--context-length must be at least 512")
    if not 0 < args.count_timeout <= 60:
        parser.error("--count-timeout must be greater than 0 and at most 60")
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    provider = LMStudioProvider()
    model = args.model
    instance = None
    sdk_module: Any | None = None
    sdk_client: Any | None = None
    sdk_model: Any | None = None
    report: dict[str, Any] = {
        "probe_complete": False,
        "all_counts_match": False,
        "sdk_version": importlib.metadata.version("lmstudio"),
        "model": model,
        "counter": "engine_owned_worker" if args.engine else "direct_sdk_probe",
        "count_timeout_s": args.count_timeout if args.engine else None,
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
        instance = await provider.load_model(model, context_length=args.context_length)
        if not args.engine:
            sdk_module = importlib.import_module("lmstudio")
            direct_client: Any = sdk_module.Client("127.0.0.1:1234")
            sdk_client = direct_client
            direct_client.__enter__()
            matches = [
                item for item in direct_client.llm.list_loaded() if item.identifier == instance
            ]
            if len(matches) != 1:
                raise RuntimeError("SDK could not identify the exact loaded HTTP instance.")
            sdk_model = matches[0]
        for name, messages, definitions in cases:
            count_error_type = None
            counted: dict[str, int | None]
            try:
                if args.engine:
                    counted_model = await provider.count_prompt(
                        PromptCountRequest.model_validate(
                            {"model": instance, "messages": messages, "tools": definitions}
                        ),
                        timeout=args.count_timeout,
                    )
                    counted = {
                        "sdk_prompt_tokens": counted_model.prompt_tokens,
                        "sdk_context_length": counted_model.context_length,
                    }
                else:
                    assert sdk_module is not None and sdk_model is not None
                    direct_count = await asyncio.to_thread(
                        sdk_count, sdk_module, sdk_model, messages, definitions
                    )
                    counted = {
                        "sdk_prompt_tokens": direct_count["sdk_prompt_tokens"],
                        "sdk_context_length": direct_count["sdk_context_length"],
                    }
            except Exception as exc:  # noqa: BLE001 - retain a type-only compatibility finding
                count_error_type = type(exc).__name__
                counted = {"sdk_prompt_tokens": None, "sdk_context_length": None}
            http_error_type = None
            actual = None
            try:
                response = await provider.chat(
                    model=model, messages=messages, tools=definitions or None, max_output_tokens=1
                )
                actual = response["usage"]["prompt_tokens"]
            except Exception as exc:  # noqa: BLE001 - retain a type-only compatibility finding
                http_error_type = type(exc).__name__
            report["cases"].append(
                {
                    "case": name,
                    **counted,
                    "http_prompt_tokens": actual,
                    "match": (
                        counted["sdk_prompt_tokens"] is not None
                        and actual is not None
                        and counted["sdk_prompt_tokens"] == actual
                    ),
                    **({"count_error_type": count_error_type} if count_error_type else {}),
                    **({"http_error_type": http_error_type} if http_error_type else {}),
                }
            )
        report["probe_complete"] = True
        report["all_counts_match"] = all(row["match"] is True for row in report["cases"])
    finally:
        try:
            if sdk_client is not None:
                sdk_client.close()
        finally:
            try:
                if instance is not None:
                    await provider.unload_model(instance)
            finally:
                await provider.close()
                (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps({**report, "evidence": str(root)}, indent=2))
    if report["probe_complete"] and not report["all_counts_match"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
