"""Bounded text extraction for user-selected document attachments."""

from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_CHARS = 48_000
MAX_PDF_PAGES = 50
MAX_DOCX_ENTRIES = 1024
MAX_DOCX_XML_BYTES = 4 * 1024 * 1024


def extract_document_text(content: bytes, media_type: str) -> str:
    """Extract bounded text from a PDF, DOCX, UTF-8 text file or Markdown document."""
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
    if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _extract_docx_text(content)
    if media_type != "application/pdf" or not content.startswith(b"%PDF-"):
        raise ValueError("Attach a PDF, DOCX, UTF-8 text file or Markdown document.")
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


def _extract_docx_text(content: bytes) -> str:
    """Read only bounded WordprocessingML text; never extract ZIP members to disk."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                raise ValueError("DOCX contains too many ZIP entries.")
            content_types = [item for item in entries if item.filename == "[Content_Types].xml"]
            documents = [item for item in entries if item.filename == "word/document.xml"]
            if len(content_types) != 1 or len(documents) != 1:
                raise ValueError(
                    "DOCX package is missing its unique content types or document part."
                )
            content_type_info, document_info = content_types[0], documents[0]
            if content_type_info.flag_bits & 1 or document_info.flag_bits & 1:
                raise ValueError("Encrypted DOCX files are not supported.")
            if (
                content_type_info.file_size > 256 * 1024
                or document_info.file_size > MAX_DOCX_XML_BYTES
            ):
                raise ValueError("DOCX document XML exceeds its extraction limit.")
            declared = archive.read(content_type_info)
            xml = archive.read(document_info)
    except ValueError:
        raise
    except (BadZipFile, RuntimeError, OSError, EOFError):
        raise ValueError("DOCX could not be safely parsed for text extraction.") from None
    if b"<!DOCTYPE" in declared.upper() or b"<!ENTITY" in declared.upper():
        raise ValueError("DOCX XML declarations are not supported.")
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("DOCX XML declarations are not supported.")
    try:
        types_root = ElementTree.fromstring(declared)
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        raise ValueError("DOCX could not be safely parsed for text extraction.") from None
    content_namespace = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    has_word_document_type = any(
        item.tag == content_namespace + "Override"
        and item.attrib.get("PartName") == "/word/document.xml"
        and item.attrib.get("ContentType")
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        for item in types_root
    )
    if types_root.tag != content_namespace + "Types" or not has_word_document_type:
        raise ValueError("ZIP document is not a supported DOCX file.")
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        fragments: list[str] = []
        for node in paragraph.iter():
            if node.tag == namespace + "t":
                fragments.append(node.text or "")
            elif node.tag == namespace + "tab":
                fragments.append("\t")
            elif node.tag in {namespace + "br", namespace + "cr"}:
                fragments.append("\n")
        value = "".join(fragments).strip()
        if value:
            paragraphs.append(value)
    text = "\n".join(paragraphs)
    if not text:
        raise ValueError("DOCX contains no extractable text.")
    return _bound(text)


def _bound(text: str) -> str:
    if len(text) > MAX_DOCUMENT_CHARS:
        return (
            text[:MAX_DOCUMENT_CHARS]
            + "\n[Document text clipped at the 48,000-character extraction limit.]"
        )
    return text
