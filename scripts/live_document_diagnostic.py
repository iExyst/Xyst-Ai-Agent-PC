"""Verify real LM Studio reading of a persisted, extracted PDF attachment."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.document_input import extract_document_text
from xaa.storage.runs import RunStore


def make_text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(output)


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    marker = f"DOC-{uuid4().hex.upper()}"
    pdf = make_text_pdf(f"Document verification marker: {marker}")
    pdf_path = root / "verification.pdf"
    pdf_path.write_bytes(pdf)
    extracted = extract_document_text(pdf, "application/pdf")
    store = RunStore(root / "document.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance: str | None = None
    report: dict[str, object] = {
        "passed": False,
        "model": model,
        "fixture": str(pdf_path),
        "fixture_bytes": len(pdf),
        "fixture_sha256": hashlib.sha256(pdf).hexdigest(),
        "extracted_text": extracted,
        "marker": marker,
        "evidence": str(root),
    }
    try:
        instance = await provider.load_model(model, context_length=16384)
        run = store.create_run(
            objective="Read the attached PDF and report the exact value after ‘Document verification marker:’. Reply with the value only.",
            acceptance_criteria=["Return the exact marker contained in the attached PDF."],
            workspace=None,
            mode="observe",
            model=model,
            max_iterations=2,
            max_seconds=180,
            max_output_tokens=512,
            document_bytes=pdf,
            document_media_type="application/pdf",
            document_text=extracted,
        )
        await Coordinator(store, provider).execute(run["id"])
        saved = store.get_run(run["id"])
        history = store.conversation_messages(run["id"])
        answer = history[-1].get("content", "") if history else ""
        calls = [
            call["function"]["name"]
            for message in history
            for call in message.get("tool_calls", [])
        ]
        restored = store.document_text(run["id"])
        artifacts = store.list_artifacts(run["id"])
        provenances = sorted(item["provenance"] for item in artifacts)
        passed = (
            saved is not None
            and saved["state"] == "verifying"
            and marker in answer
            and not calls
            and restored == extracted
            and provenances == ["user.input_document", "user.input_document_text"]
            and not store.unresolved_tool_calls(run["id"])
            and pdf_path.read_bytes() == pdf
        )
        report.update(
            passed=passed,
            run_id=run["id"],
            run_state=saved["state"] if saved else None,
            answer=answer,
            tool_calls=calls,
            restored_extracted_text_matches=restored == extracted,
            artifact_provenances=provenances,
            source_pdf_unchanged=pdf_path.read_bytes() == pdf,
            unresolved_tool_calls=store.unresolved_tool_calls(run["id"]),
        )
        if not passed:
            raise RuntimeError("Real-model PDF reading or artifact-integrity check failed.")
    except Exception as exc:
        report["error"] = type(exc).__name__
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
