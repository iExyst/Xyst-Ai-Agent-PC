# Local inference queue

`LMStudioProvider` owns one FIFO queue for the configured local server. Inference, exact prompt counting, model load/unload, credential changes, and endpoint changes use the same serialized slot. This prevents provider-owned lifecycle changes from overlapping a request and lets run cancellation release its queue reservation.

The queue bounds both the number of waiters and their estimated working set. By default it admits at most 16 waiting operations and reserves at most 2,000,000 estimated tokens across the active and waiting operations. Each request is weighted by the UTF-8 byte length of its compact model, prompt, and tool JSON plus its output allowance. When no output cap is supplied, the selected instance context length is used; if it is unknown, the estimate uses 8,192 tokens. This deliberately conservative estimate limits provider-side queued payloads; it does not predict actual tokenization or GPU memory use.

Waiting operations have a 180-second queue deadline. Standalone prompt counting defaults to a 20-second allowance, including time spent waiting for the slot; callers can set a different timeout. Queue saturation and deadline expiry return explicit provider errors before the corresponding request reaches LM Studio. Cancellation while waiting removes the waiter and its reservation; cancellation during active streamed inference closes the HTTP stream and releases the slot.

Live acceptance is model-specific. `.acceptance/09011911-7986-4b3f-bd7f-7b15100d0be8/cancellation.json` verifies a real Qwen2.5 0.5B streamed request was cancelled after its first chunk and a successor returned `READY`. `.acceptance/0718d17e-19db-44cf-9b38-6edb134a7a40/timeout.json` verifies a real Qwen3.5 9B request received server response headers, timed out waiting for a response body with a 100 ms read timeout, released all queue reservations, and was followed by a `READY` successor. The timeout trial received no chunks before expiry; it proves response-read timeout handling after headers, not recovery from every server-side generation state.

On Windows PowerShell, run the timeout scenario after confirming the model is already installed:

```powershell
$env:LM_STUDIO_MODEL = "qwen/qwen3.5-9b"
uv run python scripts/live_timeout_diagnostic.py
```

The diagnostic does not download models and unloads only the instance it starts.

Model setup can ask LM Studio's `lms load --estimate-only` mode for GPU and total memory estimates for a discovered model and optional context length. This does not load or download the model. Inside the serialized queue slot, explicit model loads refresh model discovery and the resource estimate. Chat/stream requests refresh an existing model catalog when it is at least 30 seconds old; after that refresh they also bypass any cached estimate. A missing catalog retains the existing server-side behavior without adding a discovery request to first-use chat. When the estimate explicitly reports a resource-guardrail warning, X.A.A. rejects the load or request before calling LM Studio. Already-loaded instances bypass the estimate. If stale-catalog discovery fails, or the estimate is missing, failed, or unknown, X.A.A. keeps LM Studio's existing server-side behavior. Estimates for an already-fresh catalog are cached for 60 seconds by model and context and capped at 128 entries.

This is a process-local single-server scheduler, not a GPU memory manager. The estimate gate honors LM Studio's own warning and refreshes existing stale discovery before inference, but it does not sum allocations or guarantee hard aggregate VRAM capacity. The model setup view compares estimate-only GPU and total memory needs with Windows system-wide dedicated-memory counters, NVIDIA driver utilization and VRAM readings when available, and host-available RAM snapshots. Those readings are advisory and are not queue reservations; LM Studio may offload between devices and system memory. There are no priority classes, and external clients that call LM Studio directly bypass the queue. Multi-server routing, independent hardware-capacity accounting, and broad model-by-model cancellation/timeout acceptance remain open.
