from __future__ import annotations

import asyncio

import pytest
from xaa.providers import model_estimator
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo


def test_parse_estimate_reads_memory_units_and_guardrail():
    estimate = model_estimator._parse_estimate(
        "fixture/model",
        8192,
        "Estimated GPU Memory: 2.5 GB\nEstimated Total Memory: 3 GiB\n"
        "This model may be loaded based on your resource guardrails.",
    )

    assert estimate is not None
    assert estimate.status == "estimated"
    assert estimate.estimated_gpu_memory_bytes == 2_500_000_000
    assert estimate.estimated_total_memory_bytes == 3 * 1024**3
    assert estimate.guardrail == "may_load"
    assert estimate.context_length == 8192


def test_parse_estimate_warns_when_cli_reports_memory_pressure():
    estimate = model_estimator._parse_estimate(
        "fixture/model", None, "Estimated Total Memory: 1 TB\nLikely to run out of memory"
    )

    assert estimate is not None
    assert estimate.estimated_total_memory_bytes == 1000**4
    assert estimate.guardrail == "warning"


def test_parse_estimate_rejects_unrecognized_output():
    assert model_estimator._parse_estimate("fixture/model", None, "done") is None


@pytest.mark.asyncio
async def test_estimator_reports_unavailable_without_lms(monkeypatch):
    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: None)

    result = await model_estimator.estimate_model_resources(
        "fixture/model", host="http://127.0.0.1:1234"
    )

    assert result.status == "unavailable"
    assert "did not start a model" in result.message


@pytest.mark.asyncio
async def test_estimator_uses_estimate_only_without_loading(monkeypatch):
    calls: list[tuple[str, ...]] = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"Estimated GPU Memory: 1.25 GB\nEstimated Total Memory: 2 GB", b""

    async def create_subprocess_exec(*args, **kwargs):
        calls.append(args)
        assert kwargs["stdin"] == model_estimator.subprocess.DEVNULL
        return Process()

    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: "lms")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    result = await model_estimator.estimate_model_resources(
        "fixture/model", host="http://127.0.0.1:1234", context_length=8192
    )

    assert calls == [
        (
            "lms",
            "load",
            "--estimate-only",
            "fixture/model",
            "--host",
            "http://127.0.0.1:1234",
            "--context-length",
            "8192",
        )
    ]
    assert result.status == "estimated"
    assert result.estimated_gpu_memory_bytes == 1_250_000_000


@pytest.mark.asyncio
async def test_estimator_timeout_kills_cli(monkeypatch):
    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.sleep(1)

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()

    async def create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: "lms")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    result = await model_estimator.estimate_model_resources(
        "fixture/model", host="http://127.0.0.1:1234", timeout_seconds=0.001
    )

    assert result.status == "failed"
    assert process.killed


@pytest.mark.asyncio
async def test_estimator_timeout_tolerates_process_exit_racing_with_kill(monkeypatch):
    class Process:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(1)

        def kill(self):
            raise ProcessLookupError("process exited just before the kill")

        async def wait(self):
            self.returncode = 0
            return self.returncode

    process = Process()

    async def create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: "lms")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    result = await model_estimator.estimate_model_resources(
        "fixture/model", host="http://127.0.0.1:1234", timeout_seconds=0.001
    )

    assert result.status == "failed"
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_estimator_cancellation_stops_the_cli(monkeypatch):
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()

    async def create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: "lms")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    estimate = asyncio.create_task(
        model_estimator.estimate_model_resources("fixture/model", host="http://127.0.0.1:1234")
    )
    await started.wait()
    estimate.cancel()

    with pytest.raises(asyncio.CancelledError):
        await estimate
    assert process.killed


@pytest.mark.asyncio
async def test_provider_estimate_requires_discovered_model_and_respects_context(monkeypatch):
    calls: list[tuple[str, str, int | None]] = []

    class Provider(LMStudioProvider):
        async def models(self):
            return [ModelInfo(id="fixture/model", context_length=16384)]

    async def estimate(model: str, *, host: str, context_length: int | None = None):
        calls.append((model, host, context_length))
        return model_estimator.ModelResourceEstimate(
            model=model, context_length=context_length, status="unavailable", message="fixture"
        )

    monkeypatch.setattr("xaa.providers.lm_studio.estimate_model_resources", estimate)
    provider = Provider(base_url="http://127.0.0.1:1234")
    try:
        with pytest.raises(ValueError, match="currently discovered"):
            await provider.estimate_model_load("missing/model")
        with pytest.raises(ValueError, match="maximum"):
            await provider.estimate_model_load("fixture/model", context_length=32768)
        result = await provider.estimate_model_load("fixture/model", context_length=8192)
    finally:
        await provider.close()

    assert result.status == "unavailable"
    assert calls == [("fixture/model", "http://127.0.0.1:1234", 8192)]
