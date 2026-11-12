import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from xaa.trajectory_compression import (
    CompressionSettings,
    compress_conversations,
    compress_jsonl,
    compress_native_trajectory,
    native_trajectory_turns,
)


def tokens(turns: list[dict[str, Any]]) -> int:
    return sum(len(str(turn.get("value", ""))) for turn in turns)


async def count_tokens(turns: list[dict[str, Any]]) -> int:
    return tokens(turns)


async def fixed_summary(_content: str, _target: int) -> str:
    return "The assistant read the middle section and found marker MID-42."


def sample_turns() -> list[dict[str, Any]]:
    return [
        {"from": "system", "value": "s"},
        {"from": "human", "value": "h"},
        {"from": "gpt", "value": "g"},
        {"from": "gpt", "value": "call"},
        {"from": "tool", "value": "result"},
        {"from": "human", "value": "middle " + "x" * 100},
        {"from": "gpt", "value": "middle result " + "y" * 100},
        {"from": "gpt", "value": "middle tool call"},
        {"from": "tool", "value": "middle tool result"},
        {"from": "human", "value": "tail question"},
        {"from": "gpt", "value": "tail answer"},
    ]


@pytest.mark.asyncio
async def test_compression_preserves_head_tail_and_tool_call_result_group() -> None:
    turns = sample_turns()
    summarized_regions: list[str] = []

    async def summarize(content: str, _target: int) -> str:
        summarized_regions.append(content)
        return "The assistant read the middle section and found marker MID-42."

    compressed, metrics = await compress_conversations(
        turns,
        settings=CompressionSettings(
            target_max_tokens=140, summary_target_tokens=32, protect_last_turns=2
        ),
        count_tokens=count_tokens,
        summarize=summarize,
    )

    assert compressed[:5] == turns[:5]
    assert compressed[-2:] == turns[-2:]
    assert compressed[5] == {
        "from": "human",
        "value": "[CONTEXT SUMMARY]: The assistant read the middle section and found marker MID-42.",
    }
    assert metrics.was_compressed
    assert metrics.turns_in_compressed_region == 4
    assert "middle tool call" in summarized_regions[0]
    assert "middle tool result" in summarized_regions[0]
    assert metrics.compressed_tokens <= 140
    assert metrics.tokens_saved == metrics.original_tokens - metrics.compressed_tokens


@pytest.mark.asyncio
async def test_under_budget_is_unchanged_without_summarization() -> None:
    called = False

    async def summarize(_content: str, _target: int) -> str:
        nonlocal called
        called = True
        return "unexpected"

    turns = [{"from": "human", "value": "small"}]
    result, metrics = await compress_conversations(
        turns,
        settings=CompressionSettings(target_max_tokens=128, summary_target_tokens=32),
        count_tokens=count_tokens,
        summarize=summarize,
    )
    assert result == turns
    assert not metrics.was_compressed
    assert not called


@pytest.mark.asyncio
async def test_compression_refuses_result_that_still_exceeds_budget() -> None:
    async def long_summary(_content: str, _target: int) -> str:
        return "z" * 120

    with pytest.raises(ValueError, match="still exceeds"):
        await compress_conversations(
            sample_turns(),
            settings=CompressionSettings(
                target_max_tokens=140, summary_target_tokens=32, protect_last_turns=2
            ),
            count_tokens=count_tokens,
            summarize=long_summary,
        )


@pytest.mark.asyncio
async def test_jsonl_output_is_atomic_and_does_not_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "compressed.jsonl"
    source.write_text(
        json.dumps({"id": "case-1", "conversations": sample_turns()}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report = await compress_jsonl(
        source,
        output,
        settings=CompressionSettings(
            target_max_tokens=140, summary_target_tokens=32, protect_last_turns=2
        ),
        count_tokens=count_tokens,
        summarize=fixed_summary,
    )
    entry = json.loads(output.read_text(encoding="utf-8"))
    assert report["entries"] == 1
    assert report["compressed"] == 1
    assert entry["id"] == "case-1"
    assert entry["compression_metrics"]["was_compressed"] is True
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        await compress_jsonl(
            source,
            output,
            settings=CompressionSettings(target_max_tokens=140, summary_target_tokens=32),
            count_tokens=count_tokens,
            summarize=fixed_summary,
        )


@pytest.mark.asyncio
async def test_invalid_jsonl_does_not_publish_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "compressed.jsonl"
    source.write_text('{"conversations": []}\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSONL entry"):
        await compress_jsonl(
            source,
            output,
            settings=CompressionSettings(target_max_tokens=128, summary_target_tokens=32),
            count_tokens=count_tokens,
            summarize=fixed_summary,
        )
    assert not output.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_settings_bound_token_limits() -> None:
    with pytest.raises(ValueError, match="smaller than the token budget"):
        CompressionSettings(target_max_tokens=256, summary_target_tokens=256)


@pytest.mark.asyncio
async def test_native_export_becomes_derived_transcript_and_keeps_tool_group(
    tmp_path: Path,
) -> None:
    source = tmp_path / "run.json"
    output = tmp_path / "derived.json"
    trajectory = {
        "format": "xaa-trajectory",
        "format_version": 1,
        "run": {"id": "run-42", "objective": "Inspect the fixture"},
        "events": [
            {
                "event_type": "model.message",
                "payload": {
                    "message": {
                        "role": "assistant",
                        "content": "I will inspect it.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {"name": "workspace_read_file", "arguments": "{}"},
                            }
                        ],
                    }
                },
            },
            {
                "event_type": "tool.completed",
                "payload": {"call_id": "call-1", "internal": False},
            },
            *[
                {
                    "event_type": "model.message",
                    "payload": {
                        "message": {
                            "role": "assistant",
                            "content": f"middle evidence {i} " + "x" * 80,
                        }
                    },
                }
                for i in range(5)
            ],
            {
                "event_type": "model.message",
                "payload": {"message": {"role": "assistant", "content": "Final answer."}},
            },
        ],
    }
    source_bytes = json.dumps(trajectory).encode()
    source.write_bytes(source_bytes)
    turns = native_trajectory_turns(trajectory)
    assert turns[1]["from"] == "human"
    assert turns[2]["from"] == "assistant"
    assert turns[3]["from"] == "tool"
    assert turns[3]["tool_call_id"] == "call-1"
    assert "unavailable in the redacted public export" in turns[3]["value"]

    report = await compress_native_trajectory(
        source,
        output,
        settings=CompressionSettings(
            target_max_tokens=500, summary_target_tokens=32, protect_last_turns=1
        ),
        count_tokens=count_tokens,
        summarize=fixed_summary,
    )
    derived = json.loads(output.read_text(encoding="utf-8"))
    assert derived["format"] == "xaa-compressed-trajectory"
    assert derived["source"]["run_id"] == "run-42"
    assert derived["source"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert derived["conversations"][2:4] == turns[2:4]
    assert derived["compression_metrics"]["was_compressed"] is True
    assert report["derived_format"] == "xaa-compressed-trajectory"
    assert source.read_bytes() == source_bytes
    assert "not a replayable" in derived["limitations"][0]


def test_native_export_rejects_multimodal_flattening() -> None:
    with pytest.raises(TypeError, match="Multimodal"):
        native_trajectory_turns(
            {
                "format": "xaa-trajectory",
                "format_version": 1,
                "run": {},
                "events": [
                    {
                        "event_type": "model.message",
                        "payload": {
                            "message": {"role": "assistant", "content": [{"type": "image"}]}
                        },
                    }
                ],
            }
        )
