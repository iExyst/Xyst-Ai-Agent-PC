from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from xaa.runtime.document_input import (
    MAX_DOCUMENT_BYTES,
    MAX_XLSX_FORMULA_CHARS,
    extract_document_text,
    render_pdf_scanned_pages,
)

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def make_xlsx(
    *,
    target: str = "worksheets/sheet1.xml",
    marker: str = "XLSX-MARKER-42",
    formula: str = "B1*2",
) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId0" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Fixture" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="{target}"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/styles.xml",
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="$#,##0.00"/></numFmts>'
            '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/>'
            '<xf numFmtId="164" applyNumberFormat="1"/></cellXfs></styleSheet>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>{marker}</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
            '<c r="B1"><v>42</v></c><c r="C1" t="inlineStr"><is><t>inline-value</t>'
            f'</is></c><c r="D1"><f>{formula}</f><v>84</v></c>'
            '<c r="E1"><f>NOW()</f></c><c r="F1" s="1"><v>45292</v></c>'
            '<c r="G1" s="2"><v>12.5</v></c></row></sheetData></worksheet>',
        )
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


def test_extracts_xlsx_shared_inline_numeric_and_formula_cells_without_evaluating():
    result = extract_document_text(make_xlsx(), XLSX_TYPE)
    assert "[Sheet: Fixture]" in result
    assert "A1=XLSX-MARKER-42" in result
    assert "B1=42" in result
    assert "C1=inline-value" in result
    assert "D1=84 [formula: =B1*2]" in result
    assert "E1=[formula: =NOW()]" in result
    assert "F1=45292 [number format: m/d/yy]" in result
    assert "G1=12.5 [number format: $#,##0.00]" in result


def test_xlsx_formula_text_is_capped_per_cell():
    formula = "A" * (MAX_XLSX_FORMULA_CHARS + 1)
    result = extract_document_text(make_xlsx(formula=formula), XLSX_TYPE)
    assert f"[formula: ={formula[:MAX_XLSX_FORMULA_CHARS]}…[formula truncated]]" in result


def test_rejects_xlsx_sheet_relationship_escape():
    with pytest.raises(ValueError, match="path escapes"):
        extract_document_text(make_xlsx(target="../../outside.xml"), XLSX_TYPE)


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


def test_renders_textless_pdf_page_as_bounded_png_for_vision_ocr():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    rendered = render_pdf_scanned_pages(output.getvalue())
    assert len(rendered) == 1
    assert rendered[0][0] == 1
    assert rendered[0][1].startswith(b"\x89PNG\r\n\x1a\n")
    assert len(rendered[0][1]) <= 2 * 1024 * 1024


def test_pdf_with_embedded_text_does_not_create_ocr_previews():
    assert render_pdf_scanned_pages(make_text_pdf("Visible text")) == []


def test_rejects_oversized_or_unsupported_documents():
    with pytest.raises(ValueError, match="4 MiB"):
        extract_document_text(b"x" * (MAX_DOCUMENT_BYTES + 1), "text/plain")
    with pytest.raises(ValueError, match="PDF, DOCX, XLSX, UTF-8"):
        extract_document_text(b"image bytes", "image/png")
