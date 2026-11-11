from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from xaa.runtime.document_input import (
    MAX_DOCUMENT_BYTES,
    MAX_XLSX_FORMULA_CHARS,
    _xlsx_datetime_display,
    _xlsx_time_display,
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


def make_docx(
    paragraphs: list[str],
    table: list[list[str]] | None = None,
    *,
    paragraph_xml: list[str] | None = None,
    numbering_xml: str | None = None,
    styles_xml: str | None = None,
    table_style: str | None = None,
    table_look: str | None = None,
    table_paragraph_xml: dict[tuple[int, int], str] | None = None,
    table_cell_properties: dict[tuple[int, int], dict[str, str | None]] | None = None,
    table_row_properties: dict[int, dict[str, str | None]] | None = None,
    table_cell_properties_xml: dict[tuple[int, int], str] | None = None,
    table_row_exceptions: dict[int, str] | None = None,
    header: str | None = None,
    footer: str | None = None,
    additional_header: str | None = None,
    header_target: str = "header1.xml",
    header_mode: str = "Internal",
    header_type: str = "default",
    footer_type: str = "default",
    body_section_properties: str | None = None,
) -> bytes:
    body = (
        "".join(paragraph_xml)
        if paragraph_xml is not None
        else "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    )
    if table is not None:
        rows = []
        for row_index, row in enumerate(table):
            cells = []
            for cell_index, text in enumerate(row):
                properties = (table_cell_properties or {}).get((row_index, cell_index), {})
                property_xml = ""
                if properties:
                    property_xml = (
                        "<w:tcPr>"
                        + "".join(
                            f"<w:{name}" + (f' w:val="{value}"' if value is not None else "") + "/>"
                            for name, value in properties.items()
                        )
                        + "</w:tcPr>"
                    )
                if (
                    table_cell_properties_xml
                    and (row_index, cell_index) in table_cell_properties_xml
                ):
                    raw_properties = table_cell_properties_xml[(row_index, cell_index)]
                    property_xml = (
                        "<w:tcPr>" + raw_properties + "</w:tcPr>"
                        if not property_xml
                        else property_xml.replace("</w:tcPr>", raw_properties + "</w:tcPr>")
                    )
                paragraph = (table_paragraph_xml or {}).get(
                    (row_index, cell_index), f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
                )
                cells.append(f"<w:tc>{property_xml}{paragraph}</w:tc>")
            row_properties = (table_row_properties or {}).get(row_index, {})
            row_property_xml = ""
            if row_properties:
                row_property_xml = (
                    "<w:trPr>"
                    + "".join(
                        f"<w:{name}" + (f' w:val="{value}"' if value is not None else "") + "/>"
                        for name, value in row_properties.items()
                    )
                    + "</w:trPr>"
                )
            exception_xml = (table_row_exceptions or {}).get(row_index, "")
            rows.append("<w:tr>" + exception_xml + row_property_xml + "".join(cells) + "</w:tr>")
        table_property_parts = []
        if table_style is not None:
            table_property_parts.append(f'<w:tblStyle w:val="{table_style}"/>')
        if table_look is not None:
            table_property_parts.append(f"<w:tblLook {table_look}/>")
        table_properties = (
            "<w:tblPr>" + "".join(table_property_parts) + "</w:tblPr>"
            if table_property_parts
            else ""
        )
        body += "<w:tbl>" + table_properties + "".join(rows) + "</w:tbl>"
    header_reference = ""
    footer_reference = ""
    relationships = []
    content_type_overrides = []
    if header is not None:
        header_reference = f'<w:headerReference w:type="{header_type}" r:id="rIdHeader"/>'
        relationships.append(
            '<Relationship Id="rIdHeader" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" '
            f'Target="{header_target}" TargetMode="{header_mode}"/>'
        )
        content_type_overrides.append(
            '<Override PartName="/word/header1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        )
    if additional_header is not None:
        relationships.append(
            '<Relationship Id="rIdHeader2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" '
            'Target="header2.xml"/>'
        )
        content_type_overrides.append(
            '<Override PartName="/word/header2.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        )
    if footer is not None:
        footer_reference = f'<w:footerReference w:type="{footer_type}" r:id="rIdFooter"/>'
        relationships.append(
            '<Relationship Id="rIdFooter" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
            'Target="footer1.xml"/>'
        )
        content_type_overrides.append(
            '<Override PartName="/word/footer1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        )
    if body_section_properties is not None:
        body += body_section_properties
    elif header_reference or footer_reference:
        body += f"<w:sectPr>{header_reference}{footer_reference}</w:sectPr>"
    if styles_xml is not None:
        content_type_overrides.append(
            '<Override PartName="/word/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        )
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{body}</w:body></w:document>"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            + "".join(content_type_overrides)
            + "</Types>",
        )
        archive.writestr("word/document.xml", document)
        if numbering_xml is not None:
            archive.writestr("word/numbering.xml", numbering_xml)
        if styles_xml is not None:
            archive.writestr("word/styles.xml", styles_xml)
        if relationships:
            archive.writestr(
                "word/_rels/document.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(relationships)
                + "</Relationships>",
            )
        if header is not None:
            archive.writestr(
                "word/header1.xml",
                '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:p><w:r><w:t>{header}</w:t></w:r></w:p></w:hdr>",
            )
        if additional_header is not None:
            archive.writestr(
                "word/header2.xml",
                '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:p><w:r><w:t>{additional_header}</w:t></w:r></w:p></w:hdr>",
            )
        if footer is not None:
            archive.writestr(
                "word/footer1.xml",
                '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:p><w:r><w:t>{footer}</w:t></w:r></w:p></w:ftr>",
            )
    return output.getvalue()


def make_numbering_xml(formats_by_id: dict[int, str]) -> str:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    definitions = []
    instances = []
    for abstract_id, (number_id, number_format) in enumerate(formats_by_id.items()):
        definitions.append(
            f'<w:abstractNum w:abstractNumId="{abstract_id}"><w:lvl w:ilvl="0">'
            f'<w:numFmt w:val="{number_format}"/><w:lvlText w:val="%1."/>'
            "</w:lvl></w:abstractNum>"
        )
        instances.append(
            f'<w:num w:numId="{number_id}"><w:abstractNumId w:val="{abstract_id}"/></w:num>'
        )
    return (
        f'<w:numbering xmlns:w="{namespace}">' + "".join(definitions + instances) + "</w:numbering>"
    )


def make_xlsx(
    *,
    target: str = "worksheets/sheet1.xml",
    marker: str = "XLSX-MARKER-42",
    formula: str = "B1*2",
    date_1904: bool = False,
    date_serial: str = "45292",
) -> bytes:
    output = BytesIO()
    workbook_properties = '<workbookPr date1904="1"/>' if date_1904 else ""
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
            + workbook_properties
            + '<sheets><sheet name="Fixture" sheetId="1" r:id="rId1"/></sheets></workbook>',
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
            '<numFmts count="11"><numFmt numFmtId="164" formatCode="$#,##0.00"/>'
            '<numFmt numFmtId="165" formatCode="d-mmm-yy"/>'
            '<numFmt numFmtId="166" formatCode="0.00%"/>'
            '<numFmt numFmtId="167" formatCode="#,##0.00"/>'
            '<numFmt numFmtId="168" formatCode="h:mm"/>'
            '<numFmt numFmtId="169" formatCode="h:mm:ss"/>'
            '<numFmt numFmtId="170" formatCode="h:mm AM/PM"/>'
            '<numFmt numFmtId="171" formatCode="[h]:mm:ss"/>'
            '<numFmt numFmtId="172" formatCode="mm:ss"/>'
            '<numFmt numFmtId="173" formatCode="m/d/yy h:mm AM/PM"/>'
            '<numFmt numFmtId="174" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>'
            '<cellXfs count="13"><xf numFmtId="0"/><xf numFmtId="14"/>'
            '<xf numFmtId="164" applyNumberFormat="1"/><xf numFmtId="165"/>'
            '<xf numFmtId="166"/><xf numFmtId="167"/><xf numFmtId="168"/>'
            '<xf numFmtId="169"/><xf numFmtId="170"/><xf numFmtId="171"/>'
            '<xf numFmtId="172"/><xf numFmtId="173"/><xf numFmtId="174"/>'
            "</cellXfs></styleSheet>",
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
            '<c r="E1"><f>NOW()</f></c><c r="F1" s="1"><v>'
            f"{date_serial}"
            "</v></c>"
            '<c r="G1" s="2"><v>12.5</v></c><c r="H1" s="3"><v>'
            f"{date_serial}"
            '</v></c><c r="I1" s="4"><v>0.125</v></c>'
            '<c r="J1" s="5"><v>1234.5</v></c>'
            '<c r="K1" s="6"><v>0.5</v></c>'
            '<c r="L1" s="7"><v>0.524259259259259259</v></c>'
            '<c r="M1" s="8"><v>0.75</v></c>'
            '<c r="N1" s="9"><v>1.25</v></c>'
            '<c r="O1" s="10"><v>0.000752314814814815</v></c>'
            '<c r="P1" s="11"><v>45292.75</v></c>'
            '<c r="Q1" s="12"><v>45292.524259259259259</v></c>'
            '<c r="R1" s="11"><v>45292.99999999</v></c>'
            "</row></sheetData></worksheet>",
        )
    return output.getvalue()


def test_extracts_pdf_text_with_page_marker():
    result = extract_document_text(make_text_pdf("Visible fixture text"), "application/pdf")
    assert "[Page 1]" in result
    assert "Visible fixture text" in result


def test_extracts_docx_paragraphs_without_unzipping_to_disk():
    result = extract_document_text(make_docx(["First line", "Second line"]), DOCX_TYPE)
    assert result == "First line\nSecond line"


def test_extracts_docx_heading_style_and_list_level_markers():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
                    "<w:r><w:t>Project plan</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/>'
                    '<w:numId w:val="4"/></w:numPr></w:pPr>'
                    "<w:r><w:t>First action</w:t></w:r></w:p>"
                ),
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0">'
                '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/>'
                '<w:lvlText w:val="%2."/></w:lvl></w:abstractNum>'
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num>'
                "</w:numbering>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[Heading: Heading2] Project plan" in result
    assert "[List decimal, level 2, marker %2.; numbered marker 1.] First action" in result


def test_inherits_docx_numbering_from_based_on_paragraph_styles():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Parent action</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:pStyle w:val="NestedAction"/></w:pPr>'
                    "<w:r><w:t>Read the document</w:t></w:r></w:p>"
                ),
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0">'
                '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
                '<w:lvl w:ilvl="1"><w:start w:val="2"/><w:numFmt w:val="upperRoman"/>'
                '<w:lvlText w:val="%1.%2"/></w:lvl></w:abstractNum>'
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
            ),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="paragraph" w:styleId="BaseAction">'
                '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/>'
                "</w:numPr></w:pPr></w:style>"
                '<w:style w:type="paragraph" w:styleId="NestedAction">'
                '<w:basedOn w:val="BaseAction"/><w:pPr><w:numPr>'
                '<w:ilvl w:val="1"/></w:numPr></w:pPr></w:style></w:styles>'
            ),
        ),
        DOCX_TYPE,
    )

    assert "List decimal, level 1, marker %1.; numbered marker 2." in result
    assert "Read the document" in result


def test_inherits_docx_numbering_from_whole_table_style_chain():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Table action", "Direct action"]],
            table_style="ActionTable",
            table_paragraph_xml={
                (0, 1): (
                    '<w:p><w:pPr><w:numPr><w:numId w:val="5"/></w:numPr></w:pPr>'
                    "<w:r><w:t>Direct action</w:t></w:r></w:p>"
                )
            },
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                "</w:lvl></w:abstractNum>"
                '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="upperRoman"/><w:lvlText w:val="%1)"/>'
                "</w:lvl></w:abstractNum>"
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num>'
                '<w:num w:numId="5"><w:abstractNumId w:val="1"/></w:num></w:numbering>'
            ),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="BaseTable">'
                '<w:tblStylePr w:type="wholeTable"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr></w:style>'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:basedOn w:val="BaseTable"/></w:style></w:styles>'
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Table action" in result
    assert "[List upperRoman, level 1, marker %1); numbered marker I)] Direct action" in result


def test_applies_docx_first_row_table_style_numbering_only_to_enabled_row():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Header action"], ["Body action"]],
            table_style="ActionTable",
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                "</w:lvl></w:abstractNum>"
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
            ),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstRow"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Header action" in result
    assert "Body action" in result
    assert "[List decimal, level 1, marker %1.; numbered marker 2.] Body action" not in result


def test_docx_row_conditional_style_mask_overrides_table_position():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Header action"], ["Body action"]],
            table_style="ActionTable",
            table_row_properties={
                0: {"cnfStyle": "000000000000"},
                1: {"cnfStyle": "100000000000"},
            },
            numbering_xml=make_numbering_xml({4: "decimal"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstRow"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Header action" not in result
    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Body action" in result


def test_docx_explicit_conditional_style_flags_take_precedence_over_mask():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Header action"]],
            table_style="ActionTable",
            table_cell_properties_xml={(0, 0): '<w:cnfStyle w:val="100000000000" w:firstRow="0"/>'},
            numbering_xml=make_numbering_xml({4: "decimal"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstRow"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Header action" not in result


@pytest.mark.parametrize(
    ("table_look", "row_look", "expected"),
    [
        (
            (
                'w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" '
                'w:noHBand="1" w:noVBand="1"'
            ),
            'w:firstRow="0"',
            False,
        ),
        (
            (
                'w:firstRow="0" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" '
                'w:noHBand="1" w:noVBand="1"'
            ),
            'w:firstRow="1"',
            True,
        ),
    ],
)
def test_docx_row_table_look_exception_overrides_conditional_numbering(
    table_look: str, row_look: str, expected: bool
):
    result = extract_document_text(
        make_docx(
            [],
            table=[["Header action"], ["Body action"]],
            table_style="ActionTable",
            table_look=table_look,
            table_row_exceptions={0: f"<w:tblPrEx><w:tblLook {row_look}/></w:tblPrEx>"},
            numbering_xml=make_numbering_xml({4: "decimal"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstRow"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    header_has_number = (
        "[List decimal, level 1, marker %1.; numbered marker 1.] Header action" in result
    )
    assert header_has_number is expected


def test_docx_nonconditional_row_property_exception_keeps_table_look():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Header action"]],
            table_style="ActionTable",
            table_look=(
                'w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" '
                'w:noHBand="1" w:noVBand="1"'
            ),
            table_row_exceptions={0: "<w:tblPrEx><w:tblBorders/></w:tblPrEx>"},
            numbering_xml=make_numbering_xml({4: "decimal"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstRow"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Header action" in result


def test_reads_docx_table_look_legacy_bitmask():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Header"], ["Body"]],
            table_style="ActionTable",
            table_look='w:val="0020"',
            numbering_xml=make_numbering_xml({4: "decimal"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstRow"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Header" in result
    assert "[List decimal, level 1, marker %1.; numbered marker 2.] Body" not in result


def test_applies_docx_first_column_table_style_numbering_by_cell_position():
    result = extract_document_text(
        make_docx(
            [],
            table=[["First column", "Other column"]],
            table_style="ActionTable",
            table_look=(
                'w:firstRow="0" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" '
                'w:noHBand="1" w:noVBand="1"'
            ),
            numbering_xml=make_numbering_xml({4: "decimal"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstCol"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] First column" in result
    assert "Other column" in result
    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Other column" not in result


def test_docx_first_row_rule_overrides_first_column_rule_by_precedence():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Corner"]],
            table_style="ActionTable",
            table_look=(
                'w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" '
                'w:noHBand="1" w:noVBand="1"'
            ),
            numbering_xml=make_numbering_xml({4: "decimal", 5: "upperRoman"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblStylePr w:type="firstCol"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                '<w:tblStylePr w:type="firstRow"><w:pPr><w:numPr>'
                '<w:numId w:val="5"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List upperRoman, level 1, marker %1.; numbered marker I.] Corner" in result


def test_applies_docx_row_banding_with_configured_band_size():
    result = extract_document_text(
        make_docx(
            [],
            table=[["Row 1"], ["Row 2"], ["Row 3"], ["Row 4"]],
            table_style="ActionTable",
            table_look=(
                'w:firstRow="0" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" '
                'w:noHBand="0" w:noVBand="1"'
            ),
            numbering_xml=make_numbering_xml({4: "decimal", 5: "upperRoman"}),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="table" w:styleId="ActionTable">'
                '<w:tblPr><w:tblStyleRowBandSize w:val="2"/></w:tblPr>'
                '<w:tblStylePr w:type="band1Horz"><w:pPr><w:numPr>'
                '<w:numId w:val="4"/></w:numPr></w:pPr></w:tblStylePr>'
                '<w:tblStylePr w:type="band2Horz"><w:pPr><w:numPr>'
                '<w:numId w:val="5"/></w:numPr></w:pPr></w:tblStylePr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Row 1" in result
    assert "[List decimal, level 1, marker %1.; numbered marker 2.] Row 2" in result
    assert "[List upperRoman, level 1, marker %1.; numbered marker I.] Row 3" in result
    assert "[List upperRoman, level 1, marker %1.; numbered marker II.] Row 4" in result


def test_rejects_docx_table_style_based_on_cycles():
    with pytest.raises(ValueError, match="table styles contain a basedOn cycle"):
        extract_document_text(
            make_docx(
                [],
                table=[["Cycle"]],
                table_style="First",
                styles_xml=(
                    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:style w:type="table" w:styleId="First">'
                    '<w:basedOn w:val="Second"/></w:style>'
                    '<w:style w:type="table" w:styleId="Second">'
                    '<w:basedOn w:val="First"/></w:style></w:styles>'
                ),
            ),
            DOCX_TYPE,
        )


def test_resolves_docx_num_style_link_to_its_numbering_instance():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Styled list item</w:t></w:r></w:p>"
                )
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0">'
                '<w:numStyleLink w:val="NumberingStyle"/></w:abstractNum>'
                '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                "</w:lvl></w:abstractNum>"
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num>'
                '<w:num w:numId="9"><w:abstractNumId w:val="1"/></w:num></w:numbering>'
            ),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="numbering" w:styleId="NumberingStyle">'
                '<w:pPr><w:numPr><w:numId w:val="9"/></w:numPr></w:pPr>'
                "</w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1.; numbered marker 1.] Styled list item" in result


def test_rejects_cyclic_docx_num_style_link():
    with pytest.raises(ValueError, match="reference cycle"):
        extract_document_text(
            make_docx(
                [],
                paragraph_xml=[
                    (
                        '<w:p><w:pPr><w:numPr><w:numId w:val="4"/></w:numPr></w:pPr>'
                        "<w:r><w:t>Cyclic item</w:t></w:r></w:p>"
                    )
                ],
                numbering_xml=(
                    '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:abstractNum w:abstractNumId="0">'
                    '<w:numStyleLink w:val="CycleStyle"/></w:abstractNum>'
                    '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num>'
                    "</w:numbering>"
                ),
                styles_xml=(
                    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:style w:type="numbering" w:styleId="CycleStyle">'
                    '<w:pPr><w:numPr><w:numId w:val="4"/></w:numPr></w:pPr>'
                    "</w:style></w:styles>"
                ),
            ),
            DOCX_TYPE,
        )


def test_direct_docx_numbering_overrides_paragraph_style_numbering():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:pStyle w:val="Numbered"/><w:numPr>'
                    '<w:ilvl w:val="0"/><w:numId w:val="5"/></w:numPr></w:pPr>'
                    "<w:r><w:t>Direct override</w:t></w:r></w:p>"
                )
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                '<w:start w:val="1"/><w:numFmt w:val="decimal"/>'
                '<w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num>'
                '<w:num w:numId="5"><w:abstractNumId w:val="0"/>'
                '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="7"/>'
                "</w:lvlOverride></w:num></w:numbering>"
            ),
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="paragraph" w:styleId="Numbered"><w:pPr><w:numPr>'
                '<w:ilvl w:val="0"/><w:numId w:val="4"/>'
                "</w:numPr></w:pPr></w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "numbered marker 7." in result
    assert "numbered marker 1." not in result


def test_direct_docx_num_id_zero_disables_style_numbering():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:pStyle w:val="Numbered"/><w:numPr>'
                    '<w:numId w:val="0"/></w:numPr></w:pPr>'
                    "<w:r><w:t>Unnumbered</w:t></w:r></w:p>"
                )
            ],
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="paragraph" w:styleId="Numbered"><w:pPr><w:numPr>'
                '<w:ilvl w:val="0"/><w:numId w:val="4"/>'
                "</w:numPr></w:pPr></w:style></w:styles>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List " not in result
    assert "Unnumbered" in result


def test_rejects_docx_paragraph_style_based_on_cycles():
    with pytest.raises(ValueError, match="basedOn cycle"):
        extract_document_text(
            make_docx(
                ["Text"],
                styles_xml=(
                    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:style w:type="paragraph" w:styleId="First">'
                    '<w:basedOn w:val="Second"/></w:style>'
                    '<w:style w:type="paragraph" w:styleId="Second">'
                    '<w:basedOn w:val="First"/></w:style></w:styles>'
                ),
            ),
            DOCX_TYPE,
        )


def test_ignores_based_on_style_with_a_different_style_type():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:pStyle w:val="ParagraphStyle"/></w:pPr>'
                    "<w:r><w:t>Plain paragraph</w:t></w:r></w:p>"
                )
            ],
            styles_xml=(
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="character" w:styleId="CharacterBase">'
                '<w:pPr><w:numPr><w:numId w:val="4"/></w:numPr></w:pPr></w:style>'
                '<w:style w:type="paragraph" w:styleId="ParagraphStyle">'
                '<w:basedOn w:val="CharacterBase"/></w:style></w:styles>'
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List " not in result
    assert "Plain paragraph" in result


def test_extracts_docx_numbering_override_marker_template():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/>'
                    '<w:numId w:val="4"/></w:numPr></w:pPr>'
                    "<w:r><w:t>Bullet action</w:t></w:r></w:p>"
                )
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                "</w:lvl></w:abstractNum>"
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/>'
                '<w:lvlOverride w:ilvl="0"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>'
                "</w:lvl></w:lvlOverride></w:num></w:numbering>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List bullet, level 1, marker •] Bullet action" in result


def test_inherits_unmodified_marker_field_from_partial_numbering_override():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/>'
                    '<w:numId w:val="4"/></w:numPr></w:pPr>'
                    "<w:r><w:t>Item</w:t></w:r></w:p>"
                )
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                "</w:lvl></w:abstractNum>"
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/>'
                '<w:lvlOverride w:ilvl="0"><w:lvl w:ilvl="0">'
                '<w:lvlText w:val="%1)"/></w:lvl></w:lvlOverride>'
                "</w:num></w:numbering>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "[List decimal, level 1, marker %1); numbered marker 1)] Item" in result


def test_computes_docx_list_numbers_with_start_override_and_level_restart():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Parent one</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Child one</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Child two</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Parent two</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Child restarted</w:t></w:r></w:p>"
                ),
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0">'
                '<w:lvl w:ilvl="0"><w:start w:val="4"/><w:numFmt w:val="decimal"/>'
                '<w:lvlText w:val="%1."/></w:lvl>'
                '<w:lvl w:ilvl="1"><w:start w:val="2"/><w:numFmt w:val="lowerLetter"/>'
                '<w:lvlText w:val="%1.%2)"/></w:lvl></w:abstractNum>'
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/>'
                '<w:lvlOverride w:ilvl="1"><w:startOverride w:val="5"/>'
                "</w:lvlOverride></w:num></w:numbering>"
            ),
        ),
        DOCX_TYPE,
    )

    assert "numbered marker 4." in result
    assert "numbered marker 4.e)" in result
    assert "numbered marker 4.f)" in result
    assert "numbered marker 5." in result
    assert "numbered marker 5.e)" in result


@pytest.mark.parametrize(
    ("number_format", "start", "expected"),
    [
        ("lowerLetter", 27, "aa."),
        ("upperLetter", 28, "AB."),
        ("lowerRoman", 9, "ix."),
        ("upperRoman", 4, "IV."),
    ],
)
def test_renders_supported_docx_list_number_formats(number_format, start, expected):
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Item</w:t></w:r></w:p>"
                )
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                f'<w:start w:val="{start}"/><w:numFmt w:val="{number_format}"/>'
                '<w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
            ),
        ),
        DOCX_TYPE,
    )

    assert f"numbered marker {expected}" in result


def test_docx_level_with_never_restart_keeps_child_sequence_after_parent():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Child one</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Parent</w:t></w:r></w:p>"
                ),
                (
                    '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="4"/>'
                    "</w:numPr></w:pPr><w:r><w:t>Child two</w:t></w:r></w:p>"
                ),
            ],
            numbering_xml=(
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0">'
                '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
                '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/><w:lvlText w:val="%2."/>'
                '<w:lvlRestart w:val="0"/></w:lvl></w:abstractNum>'
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
            ),
        ),
        DOCX_TYPE,
    )

    assert "numbered marker 1.] Child one" in result
    assert "numbered marker 2.] Child two" in result


@pytest.mark.parametrize(
    ("numbering_xml", "error"),
    [
        ("<wrong/>", "invalid root"),
        (
            (
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"/><w:abstractNum w:abstractNumId="0"/>'
                "</w:numbering>"
            ),
            "duplicate abstract IDs",
        ),
        (
            (
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:num w:numId="4"><w:abstractNumId w:val="8"/></w:num></w:numbering>'
            ),
            "missing abstract definition",
        ),
        (
            (
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                "</w:lvl></w:abstractNum>"
                '<w:num w:numId="4"><w:abstractNumId w:val="0"/>'
                '<w:lvlOverride w:ilvl="0"/><w:lvlOverride w:ilvl="0"/>'
                "</w:num></w:numbering>"
            ),
            "duplicate or invalid level",
        ),
    ],
)
def test_rejects_invalid_docx_numbering_definitions(numbering_xml, error):
    with pytest.raises(ValueError, match=error):
        extract_document_text(make_docx(["Text"], numbering_xml=numbering_xml), DOCX_TYPE)


@pytest.mark.parametrize(
    "properties",
    [
        '<w:numPr><w:ilvl w:val="9"/><w:numId w:val="1"/></w:numPr>',
        '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="x"/></w:numPr>',
        '<w:numPr><w:numId w:val="1"/><w:numId w:val="2"/></w:numPr>',
    ],
)
def test_rejects_malformed_docx_paragraph_list_metadata(properties):
    with pytest.raises(ValueError, match="list metadata"):
        extract_document_text(
            make_docx(
                [],
                paragraph_xml=[
                    f"<w:p><w:pPr>{properties}</w:pPr><w:r><w:t>List entry</w:t></w:r></w:p>"
                ],
            ),
            DOCX_TYPE,
        )


def test_extracts_docx_table_rows_and_preserves_empty_cell_positions():
    result = extract_document_text(
        make_docx(["Inventory"], [["Item", "Count"], ["apples", "3"], ["", "4"]]),
        DOCX_TYPE,
    )
    assert result == "Inventory\nItem | Count\napples | 3\n | 4"


def test_extracts_docx_grid_spans_and_legacy_horizontal_and_vertical_merges():
    result = extract_document_text(
        make_docx(
            [],
            [
                ["Wide", "Legacy start", "Legacy continuation", "Vertical start"],
                ["Next", "", "", "Vertical continuation"],
            ],
            table_cell_properties={
                (0, 0): {"gridSpan": "2"},
                (0, 1): {"hMerge": "restart"},
                (0, 2): {"hMerge": None},
                (0, 3): {"vMerge": "restart"},
                (1, 3): {"vMerge": None},
            },
        ),
        DOCX_TYPE,
    )
    assert "Wide [grid span: 2]" in result
    assert "Legacy start [horizontal merge: restart]" in result
    assert "Legacy continuation [horizontal merge: continue]" in result
    assert "Vertical start [vertical merge: restart]" in result
    assert "Vertical continuation [vertical merge: continue]" in result


def test_rejects_docx_horizontal_merge_continuation_without_adjacent_group():
    with pytest.raises(ValueError, match="horizontal merge continuation is not adjacent"):
        extract_document_text(
            make_docx(
                [],
                [["Not merged", "Orphan continuation"]],
                table_cell_properties={(0, 1): {"hMerge": None}},
            ),
            DOCX_TYPE,
        )


def test_rejects_misaligned_docx_vertical_merge_grid_columns():
    with pytest.raises(ValueError, match="vertical merge continuation does not align"):
        extract_document_text(
            make_docx(
                [],
                [["Merged start", "Tail"], ["Narrow continuation", "Tail 2"]],
                table_cell_properties={
                    (0, 0): {"gridSpan": "2", "vMerge": "restart"},
                    (1, 0): {"vMerge": None},
                },
            ),
            DOCX_TYPE,
        )


@pytest.mark.parametrize(
    ("property_name", "value", "error"),
    [("gridSpan", "0", "invalid grid span"), ("vMerge", "unknown", "invalid vMerge")],
)
def test_rejects_malformed_docx_table_merge_metadata(property_name, value, error):
    with pytest.raises(ValueError, match=error):
        extract_document_text(
            make_docx(
                [],
                [["cell"]],
                table_cell_properties={(0, 0): {property_name: value}},
            ),
            DOCX_TYPE,
        )


def test_extracts_docx_linked_headers_and_footers():
    result = extract_document_text(
        make_docx(["Body text"], header="Confidential", footer="Page 1"), DOCX_TYPE
    )
    assert "[Document Header: default]\nConfidential" in result
    assert "[Document Footer: default]\nPage 1" in result
    assert "Body text" in result


def test_labels_first_and_even_docx_header_footer_variants():
    result = extract_document_text(
        make_docx(
            ["Body text"],
            header="First page",
            footer="Even page",
            header_type="first",
            footer_type="even",
        ),
        DOCX_TYPE,
    )
    assert "[Document Header: first]\nFirst page" in result
    assert "[Document Footer: even]\nEven page" in result


def test_inherits_docx_header_and_footer_references_across_sections():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    "<w:p><w:pPr><w:sectPr>"
                    '<w:headerReference w:type="default" r:id="rIdHeader"/>'
                    '<w:footerReference w:type="default" r:id="rIdFooter"/>'
                    "</w:sectPr></w:pPr><w:r><w:t>Section one</w:t></w:r></w:p>"
                ),
                "<w:p><w:r><w:t>Section two</w:t></w:r></w:p>",
            ],
            header="Shared header",
            footer="Shared footer",
            body_section_properties="<w:sectPr/>",
        ),
        DOCX_TYPE,
    )

    assert "[Sections 1, 2] [Document Header: default]\nShared header" in result
    assert "[Sections 1, 2] [Document Footer: default]\nShared footer" in result


def test_docx_section_header_override_is_inherited_by_following_section():
    result = extract_document_text(
        make_docx(
            [],
            paragraph_xml=[
                (
                    "<w:p><w:pPr><w:sectPr>"
                    '<w:headerReference w:type="default" r:id="rIdHeader"/>'
                    "</w:sectPr></w:pPr><w:r><w:t>Section one</w:t></w:r></w:p>"
                ),
                (
                    "<w:p><w:pPr><w:sectPr>"
                    '<w:headerReference w:type="default" r:id="rIdHeader2"/>'
                    "</w:sectPr></w:pPr><w:r><w:t>Section two</w:t></w:r></w:p>"
                ),
            ],
            header="First header",
            additional_header="Replacement header",
            body_section_properties="<w:sectPr/>",
        ),
        DOCX_TYPE,
    )

    assert "[Sections 1] [Document Header: default]\nFirst header" in result
    assert "[Sections 2, 3] [Document Header: default]\nReplacement header" in result


@pytest.mark.parametrize(
    ("header_target", "header_mode", "error"),
    [
        ("../../outside.xml", "Internal", "escapes the package"),
        ("%2e%2e/%2e%2e/outside.xml", "Internal", "escapes the package"),
        ("https://example.com/header.xml", "External", "External DOCX headers"),
    ],
)
def test_rejects_unsafe_docx_header_targets(header_target, header_mode, error):
    with pytest.raises(ValueError, match=error):
        extract_document_text(
            make_docx(
                ["Body text"],
                header="untrusted",
                header_target=header_target,
                header_mode=header_mode,
            ),
            DOCX_TYPE,
        )


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
    assert "F1=45292 [display: 1/1/24] [number format: m/d/yy]" in result
    assert "G1=12.5 [display: $12.50] [number format: $#,##0.00]" in result
    assert "H1=45292 [display: 1-Jan-24] [number format: d-mmm-yy]" in result
    assert "I1=0.125 [display: 12.50%] [number format: 0.00%]" in result
    assert "J1=1234.5 [display: 1,234.50] [number format: #,##0.00]" in result
    assert "K1=0.5 [display: 12:00] [number format: h:mm]" in result
    assert "L1=0.524259259259259259 [display: 12:34:56] [number format: h:mm:ss]" in result
    assert "M1=0.75 [display: 6:00 PM] [number format: h:mm AM/PM]" in result
    assert "N1=1.25 [display: 30:00:00] [number format: [h]:mm:ss]" in result
    assert "O1=0.000752314814814815 [display: 01:05] [number format: mm:ss]" in result
    assert "P1=45292.75 [display: 1/1/24 6:00 PM] [number format: m/d/yy h:mm AM/PM]" in result
    assert "Q1=45292.524259259259259 [display: 2024-01-01 12:34:56]" in result
    assert "R1=45292.99999999 [display: 1/2/24 12:00 AM]" in result


def test_xlsx_renders_1900_compatibility_day_and_1904_date_system():
    leap_compatibility = extract_document_text(make_xlsx(date_serial="60"), XLSX_TYPE)
    date_1904 = extract_document_text(make_xlsx(date_1904=True, date_serial="0"), XLSX_TYPE)

    assert "F1=60 [display: 2/29/00] [number format: m/d/yy]" in leap_compatibility
    assert "F1=0 [display: 1/1/04] [number format: m/d/yy]" in date_1904


@pytest.mark.parametrize(
    ("value", "format_code"),
    [
        ("-0.5", "h:mm"),
        ("NaN", "h:mm"),
        ("1.0", "m/d/yy h:mm"),
        ("0.5", "h::mm"),
        ("1000000001", "[h]:mm:ss"),
    ],
)
def test_xlsx_time_display_rejects_values_and_formats_outside_supported_subset(
    value: str, format_code: str
):
    assert _xlsx_time_display(value, format_code) is None


@pytest.mark.parametrize(
    ("value", "format_code", "expected"),
    [
        ("1.25", "[m]:ss", "1800:00"),
        ("1.25", "[s]", "108000"),
        ("0", "h:mm AM/PM", "12:00 AM"),
        ("0.5", "hh:mm A/P", "12:00 P"),
        ("0.99999999", "h:mm", "0:00"),
    ],
)
def test_xlsx_time_display_renders_elapsed_units_clock_markers_and_rollover(
    value: str, format_code: str, expected: str
):
    assert _xlsx_time_display(value, format_code) == expected


def test_xlsx_datetime_display_does_not_mix_dates_with_elapsed_totals():
    assert _xlsx_datetime_display("45292.5", "m/d/yy [h]:mm", date_1904=False) is None


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
