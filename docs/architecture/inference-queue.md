# Local inference queue

`LMStudioProvider` owns one FIFO queue for the configured local server. Inference, exact prompt counting, model load/unload, credential changes, and endpoint changes use the same serialized slot. This prevents provider-owned lifecycle changes from overlapping a request and lets run cancellation release its queue reservation.

The queue bounds both the number of waiters and their estimated working set. By default it admits at most 16 waiting operations and reserves at most 2,000,000 estimated tokens across the active and waiting operations. Each request is weighted by the UTF-8 byte length of its compact model, prompt, and tool JSON plus its output allowance. When no output cap is supplied, the selected instance context length is used; if it is unknown, the estimate uses 8,192 tokens. This deliberately conservative estimate limits provider-side queued payloads; it does not predict actual tokenization or GPU memory use.

Waiting operations have a 180-second queue deadline. Prompt counting uses its own five-second allowance, including time spent waiting for the slot. Queue saturation and deadline expiry return explicit provider errors before the corresponding request reaches LM Studio. Cancellation while waiting removes the waiter and its reservation; cancellation during active streamed inference closes the HTTP stream and releases the slot.

Live acceptance is model-specific. `.acceptance/09011911-7986-4b3f-bd7f-7b15100d0be8/cancellation.json` verifies a real Qwen2.5 0.5B streamed request was cancelled after its first chunk and a successor returned `READY`. `.acceptance/0718d17e-19db-44cf-9b38-6edb134a7a40/timeout.json` verifies a real Qwen3.5 9B request received server response headers, timed out waiting for a response body with a 100 ms read timeout, released all queue reservations, and was followed by a `READY` successor. The timeout trial received no chunks before expiry; it proves response-read timeout handling after headers, not recovery from every server-side generation state.

On Windows PowerShell, run the timeout scenario after confirming the model is already installed:

```powershell
$env:LM_STUDIO_MODEL = "qwen/qwen3.5-9b"
uv run python scripts/live_timeout_diagnostic.py
```

The diagnostic does not download models and unloads only the instance it starts.

This is a process-local single-server scheduler, not a GPU memory manager. It has no priority classes or model-size/VRAM telemetry, and external clients that call LM Studio directly bypass it. The configured weight budget and server serialization do not establish a hard aggregate context or memory ceiling. Multi-server routing, hardware-aware capacity discovery, and broad model-by-model cancellation/timeout acceptance remain open.
