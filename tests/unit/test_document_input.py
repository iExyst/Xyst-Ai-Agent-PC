from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from xaa.runtime.document_input import MAX_DOCUMENT_BYTES, extract_document_text

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def test_extracts_pdf_text_with_page_marker():
    result = extract_document_text(make_text_pdf("Visible fixture text"), "application/pdf")
    assert "[Page 1]" in result
    assert "Visible fixture text" in result


def test_extracts_docx_paragraphs_without_unzipping_to_disk():
    result = extract_document_text(make_docx(["First line", "Second line"]), DOCX_TYPE)
    assert result == "First line\nSecond line"


def test_rejects_malformed_and_textless_docx():
    with pytest.raises(ValueError, match="DOCX could not be safely parsed"):
        extract_document_text(b"PK-not-a-zip", DOCX_TYPE)
    with pytest.raises(ValueError, match="no extractable text"):
        extract_document_text(make_docx(["", ""]), DOCX_TYPE)


def test_docx_text_obeys_shared_character_limit():
    result = extract_document_text(make_docx(["a" * 48_100]), DOCX_TYPE)
    assert result.startswith("a" * 48_000)
    assert "clipped at the 48,000-character extraction limit" in result


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
    with pytest.raises(ValueError, match="PDF, DOCX, UTF-8"):
        extract_document_text(b"image bytes", "image/png")
