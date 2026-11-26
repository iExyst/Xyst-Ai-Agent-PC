from __future__ import annotations

import asyncio
import time

import pytest
from xaa.providers import model_estimator
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo, ProviderError


def _stream(data: bytes = b"", *, closed: bool = True) -> asyncio.StreamReader:
    stream = asyncio.StreamReader()
    if data:
        stream.feed_data(data)
    if closed:
        stream.feed_eof()
    return stream


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


def test_parse_estimate_rejects_unrepresentable_memory_value():
    output = f"Estimated Total Memory: {'9' * 1000} B"

    assert model_estimator._parse_estimate("fixture/model", None, output) is None


def test_parse_estimate_preserves_large_representable_integer_precision():
    estimate = model_estimator._parse_estimate(
        "fixture/model", None, "Estimated Total Memory: 9223372036854775807 B"
    )

    assert estimate is not None
    assert estimate.estimated_total_memory_bytes == 2**63 - 1


@pytest.mark.skipif(model_estimator.os.name != "nt", reason="Windows LM Studio install path")
def test_find_lms_from_default_windows_install_when_path_is_restricted(monkeypatch, tmp_path):
    binary = tmp_path / ".lmstudio" / "bin" / "lms.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture")
    monkeypatch.setattr(model_estimator.shutil, "which", lambda _candidate: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert model_estimator._find_lms_executable() == str(binary)


@pytest.mark.asyncio
async def test_estimator_reports_unavailable_without_lms(monkeypatch):
    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: None)

    result = await model_estimator.estimate_model_resources(
        "fixture/model", host="http://127.0.0.1:1234"
    )

    assert result.status == "unavailable"
    assert "did not start a model" in result.message


@pytest.mark.asyncio
async def test_estimator_rejects_non_loopback_endpoint(monkeypatch):
    async def unexpected_process(*args, **kwargs):
        raise AssertionError("invalid endpoint must not launch a process")

    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: "lms")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_process)

    result = await model_estimator.estimate_model_resources(
        "fixture/model", host="http://example.com:1234"
    )

    assert result.status == "failed"
    assert "valid HTTP server address" in result.message


@pytest.mark.asyncio
async def test_estimator_uses_estimate_only_without_loading(monkeypatch):
    calls: list[tuple[str, ...]] = []

    class Process:
        def __init__(self):
            self.returncode = 0
            self.stdout = _stream()
            self.stderr = _stream(b"Estimated GPU Memory: 1.25 GB\nEstimated Total Memory: 2 GB")

        async def wait(self):
            return self.returncode

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
            "127.0.0.1",
            "--port",
            "1234",
            "--context-length",
            "8192",
        )
    ]
    assert result.status == "estimated"
    assert result.estimated_gpu_memory_bytes == 1_250_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
async def test_estimator_stops_cli_when_output_exceeds_the_stream_limit(monkeypatch, stream_name):
    class Process:
        def __init__(self):
            self.returncode = None
            self.killed = False
            oversized = _stream(
                b"x" * (model_estimator.MAX_ESTIMATE_STREAM_BYTES + 1), closed=False
            )
            empty = _stream(closed=False)
            self.stdout = oversized if stream_name == "stdout" else empty
            self.stderr = oversized if stream_name == "stderr" else empty
            self.exited = asyncio.Event()

        def kill(self):
            self.killed = True
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.exited.set()

        async def wait(self):
            await self.exited.wait()
            return self.returncode

    process = Process()

    async def create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(model_estimator, "_find_lms_executable", lambda: "lms")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    result = await model_estimator.estimate_model_resources(
        "fixture/model", host="http://127.0.0.1:1234"
    )

    assert result.status == "failed"
    assert "16 KiB output limit" in result.message
    assert process.killed


@pytest.mark.asyncio
async def test_estimator_timeout_kills_cli(monkeypatch):
    class Process:
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.stdout = _stream(closed=False)
            self.stderr = _stream(closed=False)
            self.exited = asyncio.Event()

        async def wait(self):
            await self.exited.wait()
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.exited.set()

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
        def __init__(self):
            self.returncode = None
            self.stdout = _stream(closed=False)
            self.stderr = _stream(closed=False)

        def kill(self):
            raise ProcessLookupError("process exited just before the kill")

        async def wait(self):
            self.returncode = 0
            self.stdout.feed_eof()
            self.stderr.feed_eof()
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
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.stdout = _stream(closed=False)
            self.stderr = _stream(closed=False)
            self.exited = asyncio.Event()

        async def wait(self):
            started.set()
            await self.exited.wait()
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.exited.set()

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
        cached = await provider.estimate_model_load("fixture/model", context_length=8192)
    finally:
        await provider.close()

    assert result.status == "unavailable"
    assert cached == result
    assert calls == [("fixture/model", "http://127.0.0.1:1234", 8192)]


@pytest.mark.asyncio
async def test_resource_warning_prevents_model_load(monkeypatch):
    loaded: list[str] = []

    class Provider(LMStudioProvider):
        async def models(self):
            return [ModelInfo(id="fixture/model", loaded=False, context_length=8192)]

        async def _load_model(self, model: str, *, context_length: int | None = None):
            loaded.append(model)
            return "instance"

    async def estimate(model: str, *, host: str, context_length: int | None = None):
        return model_estimator.ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="estimated",
            guardrail="warning",
        )

    monkeypatch.setattr("xaa.providers.lm_studio.estimate_model_resources", estimate)
    provider = Provider()
    try:
        with pytest.raises(ProviderError, match="resource estimate warns"):
            await provider.load_model("fixture/model", context_length=4096)
    finally:
        await provider.close()

    assert loaded == []


@pytest.mark.asyncio
async def test_explicit_model_load_refreshes_a_cached_resource_guardrail(monkeypatch):
    estimates: list[str] = []
    loaded: list[str] = []

    class Provider(LMStudioProvider):
        async def models(self):
            return [ModelInfo(id="fixture/model", loaded=False, context_length=8192)]

        async def _load_model(self, model: str, *, context_length: int | None = None):
            loaded.append(model)
            return "instance"

    async def estimate(model: str, *, host: str, context_length: int | None = None):
        estimates.append(model)
        return model_estimator.ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="estimated",
            guardrail="may_load" if len(estimates) == 1 else "warning",
        )

    monkeypatch.setattr("xaa.providers.lm_studio.estimate_model_resources", estimate)
    provider = Provider()
    try:
        first = await provider.estimate_model_load("fixture/model", context_length=4096)
        assert first.guardrail == "may_load"
        with pytest.raises(ProviderError, match="resource estimate warns"):
            await provider.load_model("fixture/model", context_length=4096)
    finally:
        await provider.close()

    assert estimates == ["fixture/model", "fixture/model"]
    assert loaded == []


@pytest.mark.asyncio
async def test_resource_warning_prevents_chat_and_loaded_model_skips_estimate(monkeypatch):
    estimates: list[str] = []
    requests: list[str] = []

    class Provider(LMStudioProvider):
        is_loaded = False

        async def models(self):
            return [ModelInfo(id="fixture/model", loaded=self.is_loaded, context_length=8192)]

        async def _chat(self, *, model: str, **kwargs):
            requests.append(model)
            return {"choices": []}

    async def estimate(model: str, *, host: str, context_length: int | None = None):
        estimates.append(model)
        return model_estimator.ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="estimated",
            guardrail="warning",
        )

    monkeypatch.setattr("xaa.providers.lm_studio.estimate_model_resources", estimate)
    provider = Provider()
    provider._model_catalog = (
        time.monotonic(),
        {"fixture/model": ModelInfo(id="fixture/model", loaded=False, context_length=8192)},
    )
    try:
        with pytest.raises(ProviderError, match="resource estimate warns"):
            await provider.chat(model="fixture/model", messages=[])
        provider.is_loaded = True
        provider._model_catalog = (
            time.monotonic(),
            {"fixture/model": ModelInfo(id="fixture/model", loaded=True, context_length=8192)},
        )
        await provider.chat(model="fixture/model", messages=[])
    finally:
        await provider.close()

    assert estimates == ["fixture/model"]
    assert requests == ["fixture/model"]


@pytest.mark.asyncio
async def test_chat_refreshes_stale_catalog_and_resource_estimate(monkeypatch):
    models_calls = 0
    estimates: list[str] = []
    requests: list[str] = []

    class Provider(LMStudioProvider):
        async def models(self):
            nonlocal models_calls
            models_calls += 1
            return [ModelInfo(id="fixture/model", loaded=False, context_length=8192)]

        async def _chat(self, *, model: str, **kwargs):
            requests.append(model)
            return {"choices": []}

    async def estimate(model: str, *, host: str, context_length: int | None = None):
        estimates.append(model)
        return model_estimator.ModelResourceEstimate(
            model=model,
            context_length=context_length,
            status="estimated",
            guardrail="may_load" if len(estimates) == 1 else "warning",
        )

    monkeypatch.setattr("xaa.providers.lm_studio.estimate_model_resources", estimate)
    provider = Provider()
    try:
        first = await provider.estimate_model_load("fixture/model", context_length=4096)
        assert first.guardrail == "may_load"
        provider._model_catalog = (
            time.monotonic() - 31,
            {"fixture/model": ModelInfo(id="fixture/model", loaded=False, context_length=8192)},
        )

        with pytest.raises(ProviderError, match="resource estimate warns"):
            await provider.chat(model="fixture/model", messages=[])
    finally:
        await provider.close()

    assert models_calls == 2
    assert estimates == ["fixture/model", "fixture/model"]
    assert requests == []


@pytest.mark.asyncio
async def test_chat_with_no_catalog_keeps_lm_studio_server_side_admission(monkeypatch):
    requests: list[str] = []

    class Provider(LMStudioProvider):
        async def models(self):
            raise AssertionError("first-use chat should not force model discovery")

        async def _chat(self, *, model: str, **kwargs):
            requests.append(model)
            return {"choices": []}

    async def estimate(model: str, *, host: str, context_length: int | None = None):
        raise AssertionError("resource estimator should not run without a cached catalog")

    monkeypatch.setattr("xaa.providers.lm_studio.estimate_model_resources", estimate)
    provider = Provider()
    try:
        await provider.chat(model="fixture/model", messages=[])
    finally:
        await provider.close()

    assert requests == ["fixture/model"]


@pytest.mark.asyncio
async def test_chat_uses_lm_studio_fallback_when_stale_catalog_refresh_fails(monkeypatch):
    requests: list[str] = []

    class Provider(LMStudioProvider):
        async def models(self):
            raise ProviderError("fixture catalog failure")

        async def _chat(self, *, model: str, **kwargs):
            requests.append(model)
            return {"choices": []}

    async def estimate(model: str, *, host: str, context_length: int | None = None):
        raise AssertionError("resource estimator should not run without a fresh catalog")

    monkeypatch.setattr("xaa.providers.lm_studio.estimate_model_resources", estimate)
    provider = Provider()
    provider._model_catalog = (
        time.monotonic() - 31,
        {"fixture/model": ModelInfo(id="fixture/model", loaded=False, context_length=8192)},
    )
    try:
        await provider.chat(model="fixture/model", messages=[])
    finally:
        await provider.close()

    assert requests == ["fixture/model"]
