from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from xaa.runtime.document_input import MAX_DOCUMENT_BYTES, extract_document_text


def make_text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    page[NameObject("/Resources")] = resources
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_extracts_pdf_text_with_page_marker():
    result = extract_document_text(make_text_pdf("Visible fixture text"), "application/pdf")
    assert "[Page 1]" in result
    assert "Visible fixture text" in result


@pytest.mark.parametrize("media_type", ["text/plain", "text/markdown"])
def test_extracts_utf8_text_and_markdown(media_type):
    assert extract_document_text(b"\xef\xbb\xbfHello \xe2\x98\x83", media_type) == "Hello ☃"


def test_rejects_invalid_text_and_non_extractable_pdf():
    with pytest.raises(ValueError, match="UTF-8"):
        extract_document_text(b"\xff", "text/plain")
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match="no extractable text"):
        extract_document_text(output.getvalue(), "application/pdf")


def test_rejects_oversized_or_unsupported_documents():
    with pytest.raises(ValueError, match="4 MiB"):
        extract_document_text(b"x" * (MAX_DOCUMENT_BYTES + 1), "text/plain")
    with pytest.raises(ValueError, match="PDF, UTF-8"):
        extract_document_text(b"image bytes", "image/png")
