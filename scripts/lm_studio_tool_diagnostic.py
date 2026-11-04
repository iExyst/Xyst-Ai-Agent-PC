"""Run LM Studio's harmless tool-call round trip against an installed model."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from pydantic import BaseModel, ConfigDict
from xaa.providers.lm_studio import LMStudioProvider


class AddArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: int
    right: int


TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "add_numbers",
        "description": "Add two integers and return their sum.",
        "parameters": {
            "type": "object",
            "properties": {"left": {"type": "integer"}, "right": {"type": "integer"}},
            "required": ["left", "right"],
            "additionalProperties": False,
        },
    },
}


async def main() -> None:
    provider = LMStudioProvider(os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    model = os.getenv("LM_STUDIO_MODEL", "qwen3-0.6b")
    instance_id: str | None = None
    try:
        await provider.health()
        inventory = await provider.models()
        selected = next((entry for entry in inventory if entry.id == model), None)
        if selected is None:
            raise RuntimeError(
                f"Model {model!r} is not installed; diagnostic will not download it."
            )
        if not selected.capabilities.trained_for_tool_use:
            print("Notice: LM Studio metadata does not mark this model as trained for tool use.")
        instance_id = await provider.load_model(model)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "Call add_numbers with left=40 and right=2. Do not calculate the answer yourself.",
            },
            {"role": "user", "content": "Use the add_numbers tool now."},
        ]
        first = await provider.chat(model=model, messages=messages, tools=[TOOL])
        choice = first["choices"][0]
        assistant_message = choice["message"]
        calls = assistant_message.get("tool_calls", [])
        if len(calls) != 1:
            raise RuntimeError(f"Expected exactly one structured tool call; got {len(calls)}.")
        call = calls[0]
        function = call.get("function", {})
        if function.get("name") != "add_numbers":
            raise RuntimeError(f"Unexpected tool name: {function.get('name')!r}.")
        raw_args = function.get("arguments")
        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        args = AddArguments.model_validate(parsed_args)
        result = args.left + args.right
        messages.extend(
            [
                assistant_message,
                {"role": "tool", "tool_call_id": call["id"], "content": str(result)},
            ]
        )
        final = await provider.chat(model=model, messages=messages)
        answer = final["choices"][0]["message"].get("content", "")
        if "42" not in answer:
            raise RuntimeError(f"Model did not correctly report the tool result: {answer!r}")
        print(
            json.dumps(
                {
                    "model": model,
                    "tool": function["name"],
                    "arguments": args.model_dump(),
                    "result": result,
                    "final": answer,
                },
                indent=2,
            )
        )
    finally:
        if instance_id is not None:
            await provider.unload_model(instance_id)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
