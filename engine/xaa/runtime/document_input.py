"""Bounded text extraction for user-selected document attachments."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_CHARS = 48_000
MAX_PDF_PAGES = 50


def extract_document_text(content: bytes, media_type: str) -> str:
    """Extract bounded text from a PDF, UTF-8 text file or Markdown document."""
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document must be between 1 byte and 4 MiB.")
    if media_type in {"text/plain", "text/markdown"}:
        try:
            text = content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise ValueError("Text documents must use valid UTF-8 encoding.") from None
        if "\x00" in text:
            raise ValueError("Text documents cannot contain NUL bytes.")
        text = text.strip()
        if not text:
            raise ValueError("Document contains no text.")
        return _bound(text)
    if media_type != "application/pdf" or not content.startswith(b"%PDF-"):
        raise ValueError("Attach a PDF, UTF-8 text file or Markdown document.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page extraction limit.")
        pages: list[str] = []
        remaining = MAX_DOCUMENT_CHARS
        truncated = False
        for index, page in enumerate(reader.pages, start=1):
            extracted = (page.extract_text() or "").strip()
            if not extracted:
                continue
            marker = f"[Page {index}]\n"
            piece = marker + extracted + "\n"
            if len(piece) > remaining:
                pages.append(piece[:remaining])
                truncated = True
                break
            pages.append(piece)
            remaining -= len(piece)
            if remaining <= 0:
                truncated = True
                break
        text = "\n".join(pages).strip()
    except ValueError:
        raise
    except (PdfReadError, TypeError, KeyError, IndexError, RecursionError):
        raise ValueError("PDF could not be safely parsed for text extraction.") from None
    if not text:
        raise ValueError("PDF contains no extractable text; scanned-image OCR is not supported.")
    if truncated:
        text += "\n[Document text clipped at the 48,000-character extraction limit.]"
    return text


def _bound(text: str) -> str:
    if len(text) > MAX_DOCUMENT_CHARS:
        return (
            text[:MAX_DOCUMENT_CHARS]
            + "\n[Document text clipped at the 48,000-character extraction limit.]"
        )
    return text
