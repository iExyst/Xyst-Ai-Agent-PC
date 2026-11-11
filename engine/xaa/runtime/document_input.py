"""Bounded text extraction for user-selected document attachments."""

from __future__ import annotations

import re
from binascii import crc32
from io import BytesIO
from math import ceil, isfinite
from posixpath import join as join_posix
from posixpath import normpath
from struct import pack
from typing import Any, NamedTuple, cast
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile, ZipInfo
from zlib import compress

import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_CHARS = 48_000
MAX_PDF_PAGES = 50
MAX_PDF_OCR_PAGES = 4
MAX_PDF_OCR_PAGE_BYTES = 2 * 1024 * 1024
MAX_PDF_OCR_TOTAL_BYTES = 6 * 1024 * 1024
MAX_PDF_OCR_DIMENSION = 1600
MAX_PDF_OCR_PIXELS = 2_500_000
PDF_OCR_RENDER_SCALE = 2


class _DocxListLevel(NamedTuple):
    number_format: str
    marker_text: str
    start: int
    restart_level: int | None


MAX_OOXML_ENTRIES = 1024
MAX_OOXML_PART_BYTES = 4 * 1024 * 1024
MAX_DOCX_RELATED_PARTS = 50
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_XLSX_SHEETS = 50
MAX_XLSX_CELLS = 10_000
MAX_XLSX_SHARED_STRINGS = 50_000
MAX_XLSX_XML_BYTES = 8 * 1024 * 1024
MAX_XLSX_FORMULA_CHARS = 2048
MAX_XLSX_FORMAT_CHARS = 128
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def extract_document_text(content: bytes, media_type: str) -> str:
    """Extract bounded text from PDF, DOCX, XLSX, UTF-8 text or Markdown."""
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
    if media_type == DOCX_MEDIA_TYPE:
        return _extract_docx_text(content)
    if media_type == XLSX_MEDIA_TYPE:
        return _extract_xlsx_text(content)
    if media_type != "application/pdf" or not content.startswith(b"%PDF-"):
        raise ValueError("Attach a PDF, DOCX, XLSX, UTF-8 text file or Markdown document.")
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


def render_pdf_scanned_pages(content: bytes) -> list[tuple[int, bytes]]:
    """Render bounded textless PDF pages as PNGs for an explicitly selected vision model."""
    if not content or len(content) > MAX_DOCUMENT_BYTES or not content.startswith(b"%PDF-"):
        raise ValueError("Scanned-page rendering requires a PDF between 1 byte and 4 MiB.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page extraction limit.")
        scanned = [
            index
            for index, page in enumerate(reader.pages)
            if not (page.extract_text() or "").strip()
        ]
        if len(scanned) > MAX_PDF_OCR_PAGES:
            raise ValueError(
                f"PDF has more than {MAX_PDF_OCR_PAGES} scanned pages; split it into smaller files."
            )
        if not scanned:
            return []
        document: Any = pdfium.PdfDocument(content)
        try:
            rendered: list[tuple[int, bytes]] = []
            total_bytes = 0
            for index in scanned:
                page: Any = document[index]
                page_width, page_height = float(page.get_width()), float(page.get_height())
                scale = min(
                    PDF_OCR_RENDER_SCALE,
                    MAX_PDF_OCR_DIMENSION / max(page_width, page_height),
                )
                width = ceil(page_width * scale)
                height = ceil(page_height * scale)
                if (
                    not isfinite(width)
                    or not isfinite(height)
                    or width < 1
                    or height < 1
                    or width > MAX_PDF_OCR_DIMENSION
                    or height > MAX_PDF_OCR_DIMENSION
                    or width * height > MAX_PDF_OCR_PIXELS
                ):
                    raise ValueError(
                        "A scanned PDF page exceeds the bounded OCR render dimensions."
                    )
                bitmap: Any = page.render(scale=scale)
                try:
                    data = _bitmap_png(bitmap)
                finally:
                    bitmap.close()
                total_bytes += len(data)
                if len(data) > MAX_PDF_OCR_PAGE_BYTES or total_bytes > MAX_PDF_OCR_TOTAL_BYTES:
                    raise ValueError("Scanned PDF page images exceed the bounded OCR input size.")
                rendered.append((index + 1, data))
            return rendered
        finally:
            document.close()
    except ValueError:
        raise
    except (
        PdfReadError,
        pdfium.PdfiumError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
        OSError,
        RuntimeError,
    ):
        raise ValueError("PDF scanned pages could not be safely rendered.") from None


def _bitmap_png(bitmap: pdfium.PdfBitmap) -> bytes:
    """Encode PDFium's bounded BGR bitmap without an image-library dependency."""
    width, height, stride = (
        cast(int, bitmap.width),
        cast(int, bitmap.height),
        cast(int, bitmap.stride),
    )
    source = bytes(cast(Any, bitmap.buffer))
    if width < 1 or height < 1 or stride < width * 3 or len(source) < stride * height:
        raise ValueError("PDF renderer returned an invalid page bitmap.")
    pixels = bytearray(height * (1 + width * 3))
    for y in range(height):
        source_row = y * stride
        target_row = y * (1 + width * 3)
        for x in range(width):
            source_pixel = source_row + x * 3
            target_pixel = target_row + 1 + x * 3
            pixels[target_pixel] = source[source_pixel + 2]
            pixels[target_pixel + 1] = source[source_pixel + 1]
            pixels[target_pixel + 2] = source[source_pixel]
    header = b"\x89PNG\r\n\x1a\n"

    def chunk(name: bytes, payload: bytes) -> bytes:
        body = name + payload
        return pack(">I", len(payload)) + body + pack(">I", crc32(body) & 0xFFFFFFFF)

    return (
        header
        + chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compress(pixels, level=6))
        + chunk(b"IEND", b"")
    )


def _extract_docx_text(content: bytes) -> str:
    """Read bounded WordprocessingML body/header/footer text without disk extraction."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_OOXML_ENTRIES:
                raise ValueError("DOCX contains too many ZIP entries.")
            content_types = [item for item in entries if item.filename == "[Content_Types].xml"]
            documents = [item for item in entries if item.filename == "word/document.xml"]
            relationship_parts = [
                item for item in entries if item.filename == "word/_rels/document.xml.rels"
            ]
            numbering_parts = [item for item in entries if item.filename == "word/numbering.xml"]
            styles_parts = [item for item in entries if item.filename == "word/styles.xml"]
            if len(content_types) != 1 or len(documents) != 1:
                raise ValueError(
                    "DOCX package is missing its unique content types or document part."
                )
            if len(relationship_parts) > 1:
                raise ValueError("DOCX package contains duplicate document relationships.")
            if len(numbering_parts) > 1:
                raise ValueError("DOCX package contains duplicate numbering definitions.")
            if len(styles_parts) > 1:
                raise ValueError("DOCX package contains duplicate style definitions.")
            content_type_info, document_info = content_types[0], documents[0]
            relationship_info = relationship_parts[0] if relationship_parts else None
            numbering_info = numbering_parts[0] if numbering_parts else None
            styles_info = styles_parts[0] if styles_parts else None
            if any(
                info.flag_bits & 1
                for info in (
                    content_type_info,
                    document_info,
                    relationship_info,
                    numbering_info,
                    styles_info,
                )
                if info is not None
            ):
                raise ValueError("Encrypted DOCX files are not supported.")
            if (
                content_type_info.file_size > 256 * 1024
                or document_info.file_size > MAX_OOXML_PART_BYTES
                or (
                    relationship_info is not None
                    and relationship_info.file_size > MAX_OOXML_PART_BYTES
                )
                or (numbering_info is not None and numbering_info.file_size > MAX_OOXML_PART_BYTES)
                or (styles_info is not None and styles_info.file_size > MAX_OOXML_PART_BYTES)
                or sum(
                    info.file_size
                    for info in (
                        content_type_info,
                        document_info,
                        relationship_info,
                        numbering_info,
                        styles_info,
                    )
                    if info is not None
                )
                > MAX_DOCX_XML_BYTES
            ):
                raise ValueError("DOCX document XML exceeds its extraction limit.")
            declared = archive.read(content_type_info)
            xml = archive.read(document_info)
            relationship_xml = archive.read(relationship_info) if relationship_info else None
            numbering_xml = archive.read(numbering_info) if numbering_info else None
            styles_xml = archive.read(styles_info) if styles_info else None
    except ValueError:
        raise
    except (BadZipFile, RuntimeError, OSError, EOFError):
        raise ValueError("DOCX could not be safely parsed for text extraction.") from None
    if b"<!DOCTYPE" in declared.upper() or b"<!ENTITY" in declared.upper():
        raise ValueError("DOCX XML declarations are not supported.")
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("DOCX XML declarations are not supported.")
    if relationship_xml is not None and (
        b"<!DOCTYPE" in relationship_xml.upper() or b"<!ENTITY" in relationship_xml.upper()
    ):
        raise ValueError("DOCX relationship XML declarations are not supported.")
    if numbering_xml is not None and (
        b"<!DOCTYPE" in numbering_xml.upper() or b"<!ENTITY" in numbering_xml.upper()
    ):
        raise ValueError("DOCX numbering XML declarations are not supported.")
    if styles_xml is not None and (
        b"<!DOCTYPE" in styles_xml.upper() or b"<!ENTITY" in styles_xml.upper()
    ):
        raise ValueError("DOCX style XML declarations are not supported.")
    try:
        types_root = ElementTree.fromstring(declared)
        root = ElementTree.fromstring(xml)
        relationships_root = (
            ElementTree.fromstring(relationship_xml) if relationship_xml is not None else None
        )
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
    style_numbering, numbering_style_ids, table_style_numbering = _parse_docx_style_numbering(
        styles_xml, namespace
    )
    list_markers = _parse_docx_numbering(numbering_xml, namespace, numbering_style_ids)
    list_counters: dict[int, dict[int, int]] = {}

    def paragraph_text(paragraph: ElementTree.Element, table_number_id: int | None = None) -> str:
        fragments: list[str] = []
        for node in paragraph.iter():
            if node.tag == namespace + "t":
                fragments.append(node.text or "")
            elif node.tag == namespace + "tab":
                fragments.append("\t")
            elif node.tag in {namespace + "br", namespace + "cr"}:
                fragments.append("\n")
        value = "".join(fragments).strip()
        properties = [item for item in paragraph if item.tag == namespace + "pPr"]
        if len(properties) > 1:
            raise ValueError("DOCX paragraph contains duplicate property groups.")
        property_group = properties[0] if properties else None
        style_properties = (
            [item for item in property_group if item.tag == namespace + "pStyle"]
            if property_group is not None
            else []
        )
        if len(style_properties) > 1:
            raise ValueError("DOCX paragraph contains duplicate style markers.")
        prefix: list[str] = []
        style_id: str | None = None
        effective_number_id: int | None = table_number_id
        effective_level_id: int | None = None
        if style_properties:
            style_id = style_properties[0].attrib.get(namespace + "val", "")
            if not style_id or len(style_id) > 128 or not style_id.isprintable():
                raise ValueError("DOCX paragraph contains an invalid style identifier.")
            normalized_style = style_id.replace(" ", "").casefold()
            if normalized_style.startswith("heading"):
                prefix.append(f"Heading: {style_id}")
            style_number_id, style_level_id = style_numbering.get(style_id, (None, None))
            if style_number_id is not None:
                effective_number_id, effective_level_id = style_number_id, style_level_id
        numbering_properties = (
            [item for item in property_group if item.tag == namespace + "numPr"]
            if property_group is not None
            else []
        )
        if len(numbering_properties) > 1:
            raise ValueError("DOCX paragraph contains duplicate numbering markers.")
        if numbering_properties:
            numbering = numbering_properties[0]
            ids = [item for item in numbering if item.tag == namespace + "numId"]
            levels = [item for item in numbering if item.tag == namespace + "ilvl"]
            if len(ids) > 1 or len(levels) > 1:
                raise ValueError("DOCX paragraph contains duplicate list metadata.")
            if ids:
                raw_id = ids[0].attrib.get(namespace + "val", "")
                if not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 10:
                    raise ValueError("DOCX paragraph contains invalid list metadata.")
                number_id = int(raw_id)
                if number_id == 0:
                    effective_number_id = 0
                    effective_level_id = None
                else:
                    if (
                        style_id is None
                        or number_id != style_numbering.get(style_id, (None, None))[0]
                    ):
                        effective_level_id = None
                    effective_number_id = number_id
            if levels:
                raw_level = levels[0].attrib.get(namespace + "val", "")
                if (
                    not raw_level.isascii()
                    or not raw_level.isdecimal()
                    or len(raw_level) > 2
                    or int(raw_level) > 8
                ):
                    raise ValueError("DOCX paragraph contains invalid list metadata.")
                effective_level_id = int(raw_level)
        if effective_number_id is not None and effective_number_id != 0:
            number_id = effective_number_id
            level_id = effective_level_id if effective_level_id is not None else 0
            marker = list_markers.get((number_id, level_id))
            if marker is None:
                prefix.append(f"List item, level {level_id + 1}")
            else:
                number_format, marker_text, start, restart_level = marker
                prefix.append(f"List {number_format}, level {level_id + 1}, marker {marker_text}")
                _next_docx_list_number(
                    list_counters.setdefault(number_id, {}),
                    list_markers,
                    number_id,
                    level_id,
                    start,
                    restart_level,
                )
                rendered_marker = _render_docx_list_marker(
                    marker_text, list_counters[number_id], list_markers, number_id
                )
                if rendered_marker is not None:
                    prefix.append(f"numbered marker {rendered_marker}")
        if prefix and value:
            value = f"[{'; '.join(prefix)}] {value}"
        return value

    def content_blocks(
        container: ElementTree.Element, table_number_id: int | None = None
    ) -> list[str]:
        def table_cell_text(cell: ElementTree.Element, inherited_number_id: int | None) -> str:
            value = " / ".join(content_blocks(cell, inherited_number_id))
            properties = [item for item in cell if item.tag == namespace + "tcPr"]
            if len(properties) > 1:
                raise ValueError("DOCX table cell contains duplicate property groups.")
            if not properties:
                return value
            property_group = properties[0]
            metadata: list[str] = []
            spans = [item for item in property_group if item.tag == namespace + "gridSpan"]
            if len(spans) > 1:
                raise ValueError("DOCX table cell contains duplicate grid spans.")
            if spans:
                raw_span = spans[0].attrib.get(namespace + "val", "")
                if not raw_span.isascii() or not raw_span.isdecimal() or len(raw_span) > 10:
                    raise ValueError("DOCX table cell contains an invalid grid span.")
                grid_span = int(raw_span)
                if not 1 <= grid_span <= 2_147_483_647:
                    raise ValueError("DOCX table cell contains an invalid grid span.")
                metadata.append(f"grid span: {grid_span}")
            for element_name, label in (("hMerge", "horizontal"), ("vMerge", "vertical")):
                merges = [item for item in property_group if item.tag == namespace + element_name]
                if len(merges) > 1:
                    raise ValueError(f"DOCX table cell contains duplicate {element_name} markers.")
                if merges:
                    state = merges[0].attrib.get(namespace + "val", "continue")
                    if state not in {"restart", "continue"}:
                        raise ValueError(
                            f"DOCX table cell contains an invalid {element_name} state."
                        )
                    metadata.append(f"{label} merge: {state}")
            marker = f" [{'; '.join(metadata)}]" if metadata else ""
            return f"{value}{marker}" if value else marker.strip()

        blocks: list[str] = []
        for item in container:
            if item.tag == namespace + "p":
                value = paragraph_text(item, table_number_id)
                if value:
                    blocks.append(value)
            elif item.tag == namespace + "tbl":
                table_properties = [child for child in item if child.tag == namespace + "tblPr"]
                if len(table_properties) > 1:
                    raise ValueError("DOCX table contains duplicate property groups.")
                table_property_group = table_properties[0] if table_properties else None
                table_style_id: str | None = None
                table_rules: dict[str, int] = {}
                if table_property_group is not None:
                    style_markers = [
                        child
                        for child in table_property_group
                        if child.tag == namespace + "tblStyle"
                    ]
                    if len(style_markers) > 1:
                        raise ValueError("DOCX table contains duplicate style markers.")
                    if style_markers:
                        table_style_id = style_markers[0].attrib.get(namespace + "val", "")
                        if (
                            not table_style_id
                            or len(table_style_id) > 128
                            or not table_style_id.isprintable()
                        ):
                            raise ValueError("DOCX table contains an invalid style identifier.")
                        if table_style_id not in table_style_numbering:
                            raise ValueError("DOCX table references a missing table style.")
                        table_rules = table_style_numbering.get(table_style_id, {})

                rows = [child for child in item if child.tag == namespace + "tr"]
                if table_rules.keys() & {
                    "firstRow",
                    "lastRow",
                    "firstCol",
                    "lastCol",
                    "band1Horz",
                    "band2Horz",
                    "band1Vert",
                    "band2Vert",
                    "nwCell",
                    "neCell",
                    "swCell",
                    "seCell",
                } and any(
                    node.tag in {namespace + "cnfStyle", namespace + "tblPrEx"}
                    for row in rows
                    for node in row.iter()
                ):
                    raise ValueError(
                        "DOCX table conditional numbering with row/cell exceptions is unsupported."
                    )

                look_elements = (
                    [child for child in table_property_group if child.tag == namespace + "tblLook"]
                    if table_property_group is not None
                    else []
                )
                if len(look_elements) > 1:
                    raise ValueError("DOCX table contains duplicate conditional style settings.")
                look_attributes = {
                    "firstRow": "firstRow",
                    "lastRow": "lastRow",
                    "firstColumn": "firstCol",
                    "lastColumn": "lastCol",
                    "noHBand": "noHBand",
                    "noVBand": "noVBand",
                }

                def on_off(value: str, label: str) -> bool:
                    normalized = value.casefold()
                    if normalized in {"1", "true", "on", ""}:
                        return True
                    if normalized in {"0", "false", "off"}:
                        return False
                    raise ValueError(f"DOCX table contains an invalid {label} setting.")

                if look_elements:
                    look = look_elements[0]
                    specified = [
                        name for name in look_attributes if namespace + name in look.attrib
                    ]
                    if specified:
                        enabled = {
                            target: on_off(look.attrib[namespace + source], source)
                            for source, target in look_attributes.items()
                            if namespace + source in look.attrib
                        }
                    else:
                        raw_mask = look.attrib.get(namespace + "val", "0000")
                        if (
                            not raw_mask
                            or len(raw_mask) > 4
                            or not all(char in "0123456789abcdefABCDEF" for char in raw_mask)
                        ):
                            raise ValueError("DOCX table contains an invalid style bitmask.")
                        mask = int(raw_mask, 16)
                        enabled = {
                            "firstRow": bool(mask & 0x0020),
                            "lastRow": bool(mask & 0x0040),
                            "firstCol": bool(mask & 0x0080),
                            "lastCol": bool(mask & 0x0100),
                            "noHBand": bool(mask & 0x0200),
                            "noVBand": bool(mask & 0x0400),
                        }
                else:
                    # Word's default look mask enables first row/column and row banding.
                    mask = 0x04A0
                    enabled = {
                        "firstRow": bool(mask & 0x0020),
                        "lastRow": bool(mask & 0x0040),
                        "firstCol": bool(mask & 0x0080),
                        "lastCol": bool(mask & 0x0100),
                        "noHBand": bool(mask & 0x0200),
                        "noVBand": bool(mask & 0x0400),
                    }

                row_band_size = table_rules.get("__rowBandSize", 1)
                column_band_size = table_rules.get("__columnBandSize", 1)
                if table_property_group is not None:
                    for element_name, target in (
                        ("tblStyleRowBandSize", "row_band_size"),
                        ("tblStyleColBandSize", "column_band_size"),
                    ):
                        values = [
                            child
                            for child in table_property_group
                            if child.tag == namespace + element_name
                        ]
                        if len(values) > 1:
                            raise ValueError(
                                f"DOCX table contains duplicate {element_name} values."
                            )
                        if values:
                            raw_size = values[0].attrib.get(namespace + "val", "")
                            if (
                                not raw_size.isascii()
                                or not raw_size.isdecimal()
                                or len(raw_size) > 1
                            ):
                                raise ValueError(
                                    f"DOCX table contains an invalid {element_name} value."
                                )
                            size = int(raw_size)
                            if size > 3:
                                raise ValueError(
                                    f"DOCX table contains an invalid {element_name} value."
                                )
                            if target == "row_band_size":
                                row_band_size = size
                            else:
                                column_band_size = size

                grid_properties = [child for child in item if child.tag == namespace + "tblGrid"]
                if len(grid_properties) > 1:
                    raise ValueError("DOCX table contains duplicate grid definitions.")
                grid_column_count = (
                    sum(child.tag == namespace + "gridCol" for child in grid_properties[0])
                    if grid_properties
                    else 0
                )

                def cell_span(cell: ElementTree.Element) -> int:
                    property_groups = [child for child in cell if child.tag == namespace + "tcPr"]
                    if len(property_groups) > 1:
                        raise ValueError("DOCX table cell contains duplicate property groups.")
                    values = (
                        [
                            child
                            for child in property_groups[0]
                            if child.tag == namespace + "gridSpan"
                        ]
                        if property_groups
                        else []
                    )
                    if len(values) > 1:
                        raise ValueError("DOCX table cell contains duplicate grid spans.")
                    if not values:
                        return 1
                    raw_span = values[0].attrib.get(namespace + "val", "")
                    if not raw_span.isascii() or not raw_span.isdecimal() or len(raw_span) > 10:
                        raise ValueError("DOCX table cell contains an invalid grid span.")
                    span = int(raw_span)
                    if not 1 <= span <= 2_147_483_647:
                        raise ValueError("DOCX table cell contains an invalid grid span.")
                    return span

                def grid_offset(row: ElementTree.Element, name: str) -> int:
                    row_properties = [child for child in row if child.tag == namespace + "trPr"]
                    if len(row_properties) > 1:
                        raise ValueError("DOCX table row contains duplicate property groups.")
                    values = (
                        [child for child in row_properties[0] if child.tag == namespace + name]
                        if row_properties
                        else []
                    )
                    if len(values) > 1:
                        raise ValueError(f"DOCX table row contains duplicate {name} values.")
                    if not values:
                        return 0
                    raw_offset = values[0].attrib.get(namespace + "val", "")
                    if (
                        not raw_offset.isascii()
                        or not raw_offset.isdecimal()
                        or len(raw_offset) > 10
                    ):
                        raise ValueError(f"DOCX table row contains an invalid {name} value.")
                    offset = int(raw_offset)
                    if offset > 2_147_483_647:
                        raise ValueError(f"DOCX table row contains an invalid {name} value.")
                    return offset

                logical_row_widths = [
                    grid_offset(row, "gridBefore")
                    + sum(cell_span(cell) for cell in row if cell.tag == namespace + "tc")
                    + grid_offset(row, "gridAfter")
                    for row in rows
                ]
                column_count = max([grid_column_count, *logical_row_widths], default=0)
                active_table_number_id = table_number_id
                active_table_number_id = table_rules.get("wholeTable", active_table_number_id)
                for row_index, row in enumerate(rows):
                    cells: list[str] = []
                    column_index = grid_offset(row, "gridBefore")
                    for cell in (child for child in row if child.tag == namespace + "tc"):
                        span = cell_span(cell)
                        final_column = column_index + span - 1
                        cell_number_id = active_table_number_id
                        conditions: list[str] = []
                        first_row = enabled.get("firstRow", False) and row_index == 0
                        last_row = enabled.get("lastRow", False) and row_index == len(rows) - 1
                        first_column = enabled.get("firstCol", False) and column_index == 0
                        last_column = (
                            enabled.get("lastCol", False)
                            and column_count > 0
                            and final_column == column_count - 1
                        )
                        if (
                            not enabled.get("noHBand", False)
                            and row_band_size > 0
                            and not first_row
                            and not last_row
                        ):
                            band_index = row_index - int(enabled.get("firstRow", False))
                            band = (band_index // row_band_size) % 2
                            conditions.append("band1Horz" if band == 0 else "band2Horz")
                        if (
                            not enabled.get("noVBand", False)
                            and column_band_size > 0
                            and not first_column
                            and not last_column
                        ):
                            band_index = column_index - int(enabled.get("firstCol", False))
                            band = (band_index // column_band_size) % 2
                            conditions.append("band1Vert" if band == 0 else "band2Vert")
                        if first_column:
                            conditions.append("firstCol")
                        if last_column:
                            conditions.append("lastCol")
                        if first_row:
                            conditions.append("firstRow")
                        if last_row:
                            conditions.append("lastRow")
                        if first_row and first_column:
                            conditions.append("nwCell")
                        if first_row and last_column:
                            conditions.append("neCell")
                        if last_row and first_column:
                            conditions.append("swCell")
                        if last_row and last_column:
                            conditions.append("seCell")
                        for condition in conditions:
                            cell_number_id = table_rules.get(condition, cell_number_id)
                        cells.append(table_cell_text(cell, cell_number_id))
                        column_index += span
                    if any(cells):
                        blocks.append(" | ".join(cells))
            else:
                blocks.extend(content_blocks(item, table_number_id))
        return blocks

    related_parts = _docx_related_parts(
        content,
        root,
        relationships_root,
        namespace,
        len(declared)
        + len(xml)
        + (len(relationship_xml) if relationship_xml else 0)
        + (len(numbering_xml) if numbering_xml else 0),
    )

    pieces = [
        f"{label}\n{block}"
        for label, related_root in related_parts
        for block in content_blocks(related_root)
    ]
    pieces.extend(content_blocks(root))
    text = "\n".join(pieces)
    if not text:
        raise ValueError("DOCX contains no extractable text.")
    return _bound(text)


def _parse_docx_style_numbering(
    xml: bytes | None, namespace: str
) -> tuple[
    dict[str, tuple[int | None, int | None]],
    dict[str, int],
    dict[str, dict[str, int]],
]:
    """Resolve bounded paragraph, numbering, and table style numbering."""
    if xml is None:
        return {}, {}, {}
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        raise ValueError("DOCX style definitions could not be safely parsed.") from None
    if root.tag != namespace + "styles":
        raise ValueError("DOCX style definitions have an invalid root element.")

    raw_styles: dict[str, tuple[str | None, int | None, int | None]] = {}
    all_style_ids: set[str] = set()
    numbering_style_ids: dict[str, int] = {}
    raw_table_styles: dict[str, tuple[str | None, dict[str, int]]] = {}

    def decimal_attribute(value: str, label: str, maximum: int = 10) -> int:
        if not value.isascii() or not value.isdecimal() or len(value) > maximum:
            raise ValueError(f"DOCX style definitions contain an invalid {label}.")
        return int(value)

    def list_level(value: str) -> int:
        level = decimal_attribute(value, "list level", 2)
        if level > 8:
            raise ValueError("DOCX style definitions contain an invalid list level.")
        return level

    for style in root:
        if style.tag != namespace + "style":
            continue
        style_id = style.attrib.get(namespace + "styleId", "")
        if not style_id or len(style_id) > 128 or not style_id.isprintable():
            raise ValueError("DOCX style definitions contain an invalid style ID.")
        if style_id in all_style_ids:
            raise ValueError("DOCX style definitions contain duplicate style IDs.")
        all_style_ids.add(style_id)
        style_type = style.attrib.get(namespace + "type", "paragraph")
        if style_type not in {"", "paragraph", "numbering", "table"}:
            continue

        parents = [item for item in style if item.tag == namespace + "basedOn"]
        if style_type in {"", "paragraph", "table"} and len(parents) > 1:
            raise ValueError("DOCX paragraph style contains duplicate basedOn references.")
        parent_id: str | None = None
        if parents and style_type in {"", "paragraph", "table"}:
            parent_id = parents[0].attrib.get(namespace + "val", "")
            if not parent_id or len(parent_id) > 128 or not parent_id.isprintable():
                raise ValueError("DOCX paragraph style contains an invalid basedOn reference.")

        properties = (
            [item for item in style if item.tag == namespace + "pPr"]
            if style_type in {"", "paragraph", "numbering"}
            else []
        )
        if len(properties) > 1:
            raise ValueError("DOCX paragraph style contains duplicate property groups.")
        number_id: int | None = None
        if properties:
            numberings = [item for item in properties[0] if item.tag == namespace + "numPr"]
            if len(numberings) > 1:
                raise ValueError("DOCX paragraph style contains duplicate numbering markers.")
            if numberings:
                ids = [item for item in numberings[0] if item.tag == namespace + "numId"]
                levels = [item for item in numberings[0] if item.tag == namespace + "ilvl"]
                if len(ids) > 1 or len(levels) > 1:
                    raise ValueError("DOCX paragraph style contains duplicate list metadata.")
                if ids:
                    number_id = decimal_attribute(
                        ids[0].attrib.get(namespace + "val", ""), "list ID"
                    )
                if levels:
                    # The standard ignores ilvl when numPr is declared on a paragraph style.
                    list_level(levels[0].attrib.get(namespace + "val", ""))
        if style_type == "numbering":
            if number_id is not None:
                numbering_style_ids[style_id] = number_id
        elif style_type == "table":
            table_numbering: dict[str, int] = {}
            style_table_properties = [item for item in style if item.tag == namespace + "tblPr"]
            if len(style_table_properties) > 1:
                raise ValueError("DOCX table style contains duplicate table properties.")
            if style_table_properties:
                for element_name, target in (
                    ("tblStyleRowBandSize", "__rowBandSize"),
                    ("tblStyleColBandSize", "__columnBandSize"),
                ):
                    values = [
                        item
                        for item in style_table_properties[0]
                        if item.tag == namespace + element_name
                    ]
                    if len(values) > 1:
                        raise ValueError(
                            f"DOCX table style contains duplicate {element_name} values."
                        )
                    if values:
                        raw_size = values[0].attrib.get(namespace + "val", "")
                        size = decimal_attribute(raw_size, element_name, 1)
                        if size > 3:
                            raise ValueError(
                                f"DOCX table style contains an invalid {element_name} value."
                            )
                        table_numbering[target] = size
            conditional_groups = [item for item in style if item.tag == namespace + "tblStylePr"]
            for conditional in conditional_groups:
                condition = conditional.attrib.get(namespace + "type", "")
                if condition not in {
                    "wholeTable",
                    "firstRow",
                    "lastRow",
                    "firstCol",
                    "lastCol",
                    "band1Horz",
                    "band2Horz",
                    "band1Vert",
                    "band2Vert",
                    "nwCell",
                    "neCell",
                    "swCell",
                    "seCell",
                }:
                    raise ValueError("DOCX table style contains an invalid conditional type.")
                if condition in table_numbering:
                    raise ValueError("DOCX table style contains duplicate conditional types.")
                conditional_properties = [
                    item for item in conditional if item.tag == namespace + "pPr"
                ]
                if len(conditional_properties) > 1:
                    raise ValueError("DOCX table style contains duplicate paragraph properties.")
                if not conditional_properties:
                    continue
                conditional_numberings = [
                    item for item in conditional_properties[0] if item.tag == namespace + "numPr"
                ]
                if len(conditional_numberings) > 1:
                    raise ValueError("DOCX table style contains duplicate numbering markers.")
                if not conditional_numberings:
                    continue
                ids = [
                    item for item in conditional_numberings[0] if item.tag == namespace + "numId"
                ]
                levels = [
                    item for item in conditional_numberings[0] if item.tag == namespace + "ilvl"
                ]
                if len(ids) > 1 or len(levels) > 1:
                    raise ValueError("DOCX table style contains duplicate list metadata.")
                if ids:
                    table_numbering[condition] = decimal_attribute(
                        ids[0].attrib.get(namespace + "val", ""), "list ID"
                    )
                else:
                    raise ValueError(
                        "DOCX conditional table-style numbering requires an explicit list identifier."
                    )
            raw_table_styles[style_id] = parent_id, table_numbering
        else:
            raw_styles[style_id] = parent_id, number_id, None

    resolved: dict[str, tuple[int | None, int | None]] = {}
    active: set[str] = set()

    def resolve(style_id: str, depth: int = 0) -> tuple[int | None, int | None]:
        existing = resolved.get(style_id)
        if existing is not None:
            return existing
        if depth > 256:
            raise ValueError("DOCX paragraph styles contain a basedOn chain that is too deep.")
        definition = raw_styles.get(style_id)
        if definition is None:
            raise ValueError("DOCX paragraph style references a missing basedOn style.")
        if style_id in active:
            raise ValueError("DOCX paragraph styles contain a basedOn cycle.")
        active.add(style_id)
        parent_id, own_number_id, own_level_id = definition
        inherited_number_id: int | None = None
        inherited_level_id: int | None = None
        if parent_id is not None:
            if parent_id in raw_styles:
                inherited_number_id, inherited_level_id = resolve(parent_id, depth + 1)
            elif parent_id not in all_style_ids:
                raise ValueError("DOCX paragraph style references a missing basedOn style.")
        value = (
            own_number_id if own_number_id is not None else inherited_number_id,
            own_level_id if own_level_id is not None else inherited_level_id,
        )
        active.remove(style_id)
        resolved[style_id] = value
        return value

    for style_id in raw_styles:
        resolve(style_id)

    resolved_table_styles: dict[str, dict[str, int]] = {}
    active_table_styles: set[str] = set()

    def resolve_table_style(style_id: str, depth: int = 0) -> dict[str, int]:
        existing = resolved_table_styles.get(style_id)
        if existing is not None:
            return existing
        if depth > 256:
            raise ValueError("DOCX table styles contain a basedOn chain that is too deep.")
        definition = raw_table_styles.get(style_id)
        if definition is None:
            raise ValueError("DOCX table style references a missing basedOn style.")
        if style_id in active_table_styles:
            raise ValueError("DOCX table styles contain a basedOn cycle.")
        active_table_styles.add(style_id)
        parent_id, own_numbering = definition
        value: dict[str, int] = {}
        if parent_id is not None:
            if parent_id in raw_table_styles:
                value.update(resolve_table_style(parent_id, depth + 1))
            elif parent_id not in all_style_ids:
                raise ValueError("DOCX table style references a missing basedOn style.")
        value.update(own_numbering)
        active_table_styles.remove(style_id)
        resolved_table_styles[style_id] = value
        return value

    for style_id in raw_table_styles:
        resolve_table_style(style_id)
    return resolved, numbering_style_ids, resolved_table_styles


def _parse_docx_numbering(
    xml: bytes | None, namespace: str, numbering_style_ids: dict[str, int]
) -> dict[tuple[int, int], _DocxListLevel]:
    """Read bounded list marker definitions and per-level sequence behavior."""
    if xml is None:
        return {}
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        raise ValueError("DOCX numbering definitions could not be safely parsed.") from None
    if root.tag != namespace + "numbering":
        raise ValueError("DOCX numbering definitions have an invalid root element.")

    def decimal_attribute(value: str, label: str, maximum: int = 10) -> int:
        if not value.isascii() or not value.isdecimal() or len(value) > maximum:
            raise ValueError(f"DOCX numbering definitions contain an invalid {label}.")
        return int(value)

    def child_value(element: ElementTree.Element, name: str) -> str | None:
        children = [item for item in element if item.tag == namespace + name]
        if len(children) > 1:
            raise ValueError(f"DOCX numbering level contains duplicate {name} metadata.")
        return children[0].attrib.get(namespace + "val", "") if children else None

    def number_value(value: str, label: str) -> int:
        result = decimal_attribute(value, label)
        if result > 2_147_483_647:
            raise ValueError(f"DOCX numbering definitions contain an invalid {label}.")
        return result

    def restart_value(element: ElementTree.Element, level_id: int) -> int | None:
        raw = child_value(element, "lvlRestart")
        if raw is None:
            return level_id - 1 if level_id > 0 else None
        value = number_value(raw, "restart level")
        if value == 0:
            return None
        if value > level_id:
            return level_id - 1 if level_id > 0 else None
        return value - 1

    abstract_definitions: dict[int, tuple[dict[int, _DocxListLevel], str | None]] = {}
    for abstract in root:
        if abstract.tag != namespace + "abstractNum":
            continue
        abstract_id = decimal_attribute(
            abstract.attrib.get(namespace + "abstractNumId", ""), "abstract ID"
        )
        if abstract_id in abstract_definitions:
            raise ValueError("DOCX numbering definitions contain duplicate abstract IDs.")
        raw_style_links = [item for item in abstract if item.tag == namespace + "numStyleLink"]
        if len(raw_style_links) > 1:
            raise ValueError("DOCX numbering definition contains duplicate numStyleLink markers.")
        style_id: str | None = None
        if raw_style_links:
            style_id = raw_style_links[0].attrib.get(namespace + "val", "")
            if not style_id or len(style_id) > 128 or not style_id.isprintable():
                raise ValueError("DOCX numbering definition contains an invalid numStyleLink.")
        raw_style_links = [item for item in abstract if item.tag == namespace + "styleLink"]
        if len(raw_style_links) > 1:
            raise ValueError("DOCX numbering definition contains duplicate styleLink markers.")
        if raw_style_links:
            linked_style = raw_style_links[0].attrib.get(namespace + "val", "")
            if not linked_style or len(linked_style) > 128 or not linked_style.isprintable():
                raise ValueError("DOCX numbering definition contains an invalid styleLink.")
        levels: dict[int, _DocxListLevel] = {}
        for level in abstract:
            if level.tag != namespace + "lvl":
                continue
            level_id = decimal_attribute(level.attrib.get(namespace + "ilvl", ""), "level", 2)
            if level_id > 8 or level_id in levels:
                raise ValueError("DOCX numbering definitions contain a duplicate or invalid level.")
            number_format = child_value(level, "numFmt") or ""
            marker_text = child_value(level, "lvlText") or ""
            raw_start = child_value(level, "start")
            if (
                not number_format
                or len(number_format) > 64
                or not number_format.isascii()
                or not marker_text
                or len(marker_text) > 128
                or not marker_text.isprintable()
            ):
                raise ValueError("DOCX numbering level contains invalid marker metadata.")
            levels[level_id] = _DocxListLevel(
                number_format,
                marker_text,
                number_value(raw_start, "starting number") if raw_start is not None else 1,
                restart_value(level, level_id),
            )
        abstract_definitions[abstract_id] = levels, style_id

    raw_instances: dict[int, tuple[int, list[ElementTree.Element]]] = {}
    for instance in root:
        if instance.tag != namespace + "num":
            continue
        number_id = decimal_attribute(instance.attrib.get(namespace + "numId", ""), "list ID")
        abstract_ids = [item for item in instance if item.tag == namespace + "abstractNumId"]
        if len(abstract_ids) != 1 or number_id in raw_instances:
            raise ValueError("DOCX numbering instances contain missing or duplicate IDs.")
        abstract_id = decimal_attribute(
            abstract_ids[0].attrib.get(namespace + "val", ""), "abstract reference"
        )
        if abstract_id not in abstract_definitions:
            raise ValueError("DOCX numbering instance references a missing abstract definition.")
        overrides = [item for item in instance if item.tag == namespace + "lvlOverride"]
        raw_instances[number_id] = abstract_id, overrides

    resolved_instances: dict[int, dict[int, _DocxListLevel]] = {}
    active_instances: set[int] = set()
    active_abstracts: set[int] = set()

    def resolve_instance(number_id: int, depth: int = 0) -> dict[int, _DocxListLevel]:
        cached = resolved_instances.get(number_id)
        if cached is not None:
            return cached.copy()
        if depth > 256:
            raise ValueError("DOCX numbering style chain is too deep.")
        if number_id in active_instances:
            raise ValueError("DOCX numbering styles contain a reference cycle.")
        definition = raw_instances.get(number_id)
        if definition is None:
            raise ValueError("DOCX numbering style references a missing numbering instance.")
        active_instances.add(number_id)
        abstract_id, overrides = definition
        effective_levels = resolve_abstract(abstract_id, depth + 1)
        overridden_levels: set[int] = set()
        for override in overrides:
            level_id = decimal_attribute(
                override.attrib.get(namespace + "ilvl", ""), "override level", 2
            )
            if level_id > 8 or level_id in overridden_levels:
                raise ValueError("DOCX numbering override has a duplicate or invalid level.")
            overridden_levels.add(level_id)
            replacements = [item for item in override if item.tag == namespace + "lvl"]
            if len(replacements) > 1:
                raise ValueError("DOCX numbering override contains duplicate levels.")
            if replacements:
                replacement_level = replacements[0]
                inherited = effective_levels.get(level_id)
                number_format = child_value(replacement_level, "numFmt")
                marker_text = child_value(replacement_level, "lvlText")
                raw_start = child_value(replacement_level, "start")
                raw_restart = child_value(replacement_level, "lvlRestart")
                if (
                    number_format is not None
                    or marker_text is not None
                    or raw_start is not None
                    or raw_restart is not None
                ):
                    number_format = (
                        number_format
                        if number_format is not None
                        else inherited.number_format
                        if inherited
                        else ""
                    )
                    marker_text = (
                        marker_text
                        if marker_text is not None
                        else inherited.marker_text
                        if inherited
                        else ""
                    )
                    if (
                        not number_format
                        or len(number_format) > 64
                        or not number_format.isascii()
                        or not marker_text
                        or len(marker_text) > 128
                        or not marker_text.isprintable()
                    ):
                        raise ValueError(
                            "DOCX numbering override contains invalid marker metadata."
                        )
                    effective_levels[level_id] = _DocxListLevel(
                        number_format,
                        marker_text,
                        number_value(raw_start, "starting number")
                        if raw_start is not None
                        else inherited.start
                        if inherited
                        else 1,
                        restart_value(replacement_level, level_id)
                        if raw_restart is not None
                        else inherited.restart_level
                        if inherited
                        else (level_id - 1 if level_id > 0 else None),
                    )
            raw_start_override = child_value(override, "startOverride")
            if raw_start_override is not None:
                inherited = effective_levels.get(level_id)
                if inherited is not None:
                    effective_levels[level_id] = inherited._replace(
                        start=number_value(raw_start_override, "starting number override")
                    )
        active_instances.remove(number_id)
        resolved_instances[number_id] = effective_levels.copy()
        return effective_levels

    def resolve_abstract(abstract_id: int, depth: int) -> dict[int, _DocxListLevel]:
        if depth > 256:
            raise ValueError("DOCX numbering style chain is too deep.")
        if abstract_id in active_abstracts:
            raise ValueError("DOCX numbering styles contain a reference cycle.")
        definition = abstract_definitions.get(abstract_id)
        if definition is None:
            raise ValueError("DOCX numbering style references a missing abstract definition.")
        levels, style_id = definition
        if style_id is None:
            return levels.copy()
        number_id = numbering_style_ids.get(style_id)
        if number_id is None:
            raise ValueError("DOCX numbering definition references a missing numbering style.")
        active_abstracts.add(abstract_id)
        resolved = resolve_instance(number_id, depth + 1)
        active_abstracts.remove(abstract_id)
        return resolved

    instances: dict[tuple[int, int], _DocxListLevel] = {}
    for number_id in raw_instances:
        for level_id, marker in resolve_instance(number_id).items():
            instances[number_id, level_id] = marker
    return instances


def _next_docx_list_number(
    counters: dict[int, int],
    definitions: dict[tuple[int, int], _DocxListLevel],
    number_id: int,
    level_id: int,
    start: int,
    restart_level: int | None,
) -> int:
    for child_level in range(level_id + 1, 9):
        child = definitions.get((number_id, child_level))
        if child is not None and child.restart_level == level_id:
            counters.pop(child_level, None)
    current = counters.get(level_id)
    value = start if current is None else current + 1
    counters[level_id] = value
    return value


def _render_docx_list_marker(
    marker_text: str,
    counters: dict[int, int],
    definitions: dict[tuple[int, int], _DocxListLevel],
    number_id: int,
) -> str | None:
    references = list(re.finditer(r"%([1-9])", marker_text))
    if not references:
        return None
    pieces: list[str] = []
    offset = 0
    for reference in references:
        level_id = int(reference.group(1)) - 1
        number = counters.get(level_id)
        definition = definitions.get((number_id, level_id))
        if number is None or definition is None:
            return None
        rendered_number = _format_docx_list_number(number, definition.number_format)
        if rendered_number is None:
            return None
        pieces.extend((marker_text[offset : reference.start()], rendered_number))
        offset = reference.end()
    pieces.append(marker_text[offset:])
    return "".join(pieces)


def _format_docx_list_number(value: int, number_format: str) -> str | None:
    if number_format == "decimal":
        return str(value)
    if number_format in {"lowerLetter", "upperLetter"}:
        if value < 1:
            return None
        letters: list[str] = []
        remaining = value
        while remaining:
            remaining, digit = divmod(remaining - 1, 26)
            letters.append(chr((ord("a") if number_format == "lowerLetter" else ord("A")) + digit))
        return "".join(reversed(letters))
    if number_format in {"lowerRoman", "upperRoman"}:
        if not 1 <= value <= 3_999:
            return None
        roman: list[str] = []
        for amount, numeral in (
            (1_000, "M"),
            (900, "CM"),
            (500, "D"),
            (400, "CD"),
            (100, "C"),
            (90, "XC"),
            (50, "L"),
            (40, "XL"),
            (10, "X"),
            (9, "IX"),
            (5, "V"),
            (4, "IV"),
            (1, "I"),
        ):
            while value >= amount:
                value -= amount
                roman.append(numeral)
        result = "".join(roman)
        return result.lower() if number_format == "lowerRoman" else result
    return None


def _docx_related_parts(
    content: bytes,
    root: ElementTree.Element,
    relationships_root: ElementTree.Element | None,
    namespace: str,
    initial_xml_bytes: int,
) -> list[tuple[str, ElementTree.Element]]:
    """Read only referenced, bounded header/footer parts from a DOCX package."""
    references = [
        item
        for item in root.iter()
        if item.tag in {namespace + "headerReference", namespace + "footerReference"}
    ]
    if not references:
        return []
    if relationships_root is None:
        raise ValueError("DOCX header/footer relationship is missing.")
    package_namespace = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    if relationships_root.tag != package_namespace + "Relationships":
        raise ValueError("DOCX document relationships have an invalid root element.")
    relations: dict[str, dict[str, str]] = {}
    for relation in relationships_root:
        if relation.tag != package_namespace + "Relationship":
            continue
        identity = relation.attrib.get("Id")
        if not identity or identity in relations:
            raise ValueError("DOCX document relationships contain a missing or duplicate ID.")
        relations[identity] = relation.attrib

    office_relationship_namespace = (
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    )
    visited_parts: dict[str, str] = {}
    total_xml_bytes = initial_xml_bytes
    related_parts: list[tuple[str, ElementTree.Element]] = []
    with ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
        for reference in references:
            identity = reference.attrib.get(office_relationship_namespace + "id")
            relation = relations.get(identity or "")
            if relation is None:
                raise ValueError("DOCX header/footer relationship is missing.")
            is_header = reference.tag == namespace + "headerReference"
            relation_suffix = "/header" if is_header else "/footer"
            if not relation.get("Type", "").endswith(relation_suffix):
                raise ValueError("DOCX header/footer relationship has the wrong type.")
            if relation.get("TargetMode", "Internal") != "Internal":
                raise ValueError("External DOCX headers and footers are not supported.")
            target = relation.get("Target", "")
            if (
                not target
                or target.startswith("/")
                or "\\" in target
                or ":" in target
                or "?" in target
                or "#" in target
            ):
                raise ValueError("DOCX header/footer relationship has an invalid package path.")
            try:
                target = unquote(target, errors="strict")
            except UnicodeDecodeError:
                raise ValueError(
                    "DOCX header/footer relationship has an invalid package path."
                ) from None
            if "\\" in target or ":" in target or "?" in target or "#" in target:
                raise ValueError("DOCX header/footer relationship has an invalid package path.")
            part = normpath(join_posix("word", target))
            if not part.startswith("word/") or ".." in part.split("/"):
                raise ValueError("DOCX header/footer relationship escapes the package.")
            kind = "Header" if is_header else "Footer"
            if part in visited_parts:
                if visited_parts[part] != kind:
                    raise ValueError("DOCX header and footer references cannot share one part.")
                continue
            visited_parts[part] = kind
            if len(visited_parts) > MAX_DOCX_RELATED_PARTS:
                raise ValueError("DOCX contains too many referenced headers and footers.")
            matches = [item for item in entries if item.filename == part]
            if len(matches) != 1:
                raise ValueError("DOCX referenced header/footer part is missing or duplicated.")
            info = matches[0]
            if info.flag_bits & 1 or info.file_size > MAX_OOXML_PART_BYTES:
                raise ValueError("DOCX header/footer XML exceeds its extraction limit.")
            total_xml_bytes += info.file_size
            if total_xml_bytes > MAX_DOCX_XML_BYTES:
                raise ValueError("DOCX XML exceeds its total extraction limit.")
            try:
                part_xml = archive.read(info)
            except (BadZipFile, RuntimeError, OSError, EOFError):
                raise ValueError("DOCX header/footer could not be safely read.") from None
            if b"<!DOCTYPE" in part_xml.upper() or b"<!ENTITY" in part_xml.upper():
                raise ValueError("DOCX header/footer XML declarations are not supported.")
            try:
                part_root = ElementTree.fromstring(part_xml)
            except ElementTree.ParseError:
                raise ValueError("DOCX header/footer could not be safely parsed.") from None
            expected_root = namespace + ("hdr" if is_header else "ftr")
            if part_root.tag != expected_root:
                raise ValueError("DOCX header/footer part has an invalid root element.")
            section_type = reference.attrib.get(namespace + "type", "default")
            if section_type not in {"default", "first", "even"}:
                raise ValueError("DOCX header/footer reference has an invalid section type.")
            related_parts.append((f"[Document {kind}: {section_type}]", part_root))
    return related_parts


def _extract_xlsx_text(content: bytes) -> str:
    """Read bounded worksheet values without extracting archive members to disk."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_OOXML_ENTRIES:
                raise ValueError("XLSX contains too many ZIP entries.")
            content_types_info = _unique_part(entries, "[Content_Types].xml")
            package_relationships_info = _unique_part(entries, "_rels/.rels")
            workbook_info = _unique_part(entries, "xl/workbook.xml")
            relationships_info = _unique_part(entries, "xl/_rels/workbook.xml.rels")
            if any(
                item.flag_bits & 1
                for item in (
                    content_types_info,
                    package_relationships_info,
                    workbook_info,
                    relationships_info,
                )
            ):
                raise ValueError("Encrypted XLSX files are not supported.")
            if (
                content_types_info.file_size > 256 * 1024
                or package_relationships_info.file_size > MAX_OOXML_PART_BYTES
                or workbook_info.file_size > MAX_OOXML_PART_BYTES
                or relationships_info.file_size > MAX_OOXML_PART_BYTES
            ):
                raise ValueError("XLSX metadata XML exceeds its extraction limit.")
            content_types_xml = archive.read(content_types_info)
            package_relationships_xml = archive.read(package_relationships_info)
            workbook_xml = archive.read(workbook_info)
            relationships_xml = archive.read(relationships_info)
            total_xml_bytes = (
                content_types_info.file_size
                + package_relationships_info.file_size
                + workbook_info.file_size
                + relationships_info.file_size
            )
            shared_info = [item for item in entries if item.filename == "xl/sharedStrings.xml"]
            if len(shared_info) > 1:
                raise ValueError("XLSX package contains duplicate shared strings.")
            shared_strings: list[str] = []
            if shared_info:
                info = shared_info[0]
                if info.flag_bits & 1 or info.file_size > MAX_OOXML_PART_BYTES:
                    raise ValueError("XLSX shared strings exceed their extraction limit.")
                total_xml_bytes += info.file_size
                if total_xml_bytes > MAX_XLSX_XML_BYTES:
                    raise ValueError("XLSX XML exceeds its extraction limit.")
                shared_strings = _parse_shared_strings(archive.read(info))

            types_root = _parse_office_xml(content_types_xml, "XLSX content types")
            package_relationships_root = _parse_office_xml(
                package_relationships_xml, "XLSX package relationships"
            )
            workbook_root = _parse_office_xml(workbook_xml, "XLSX workbook")
            relationships_root = _parse_office_xml(relationships_xml, "XLSX relationships")
            content_namespace = "{http://schemas.openxmlformats.org/package/2006/content-types}"
            workbook_type = any(
                item.tag == content_namespace + "Override"
                and item.attrib.get("PartName") == "/xl/workbook.xml"
                and item.attrib.get("ContentType")
                == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
                for item in types_root
            )
            if types_root.tag != content_namespace + "Types" or not workbook_type:
                raise ValueError("ZIP document is not a supported XLSX file.")

            package_namespace = "{http://schemas.openxmlformats.org/package/2006/relationships}"
            root_workbook_relation = any(
                item.tag == package_namespace + "Relationship"
                and item.attrib.get("Type", "").endswith("/officeDocument")
                and item.attrib.get("TargetMode", "Internal") != "External"
                and normpath(item.attrib.get("Target", "").lstrip("/")) == "xl/workbook.xml"
                for item in package_relationships_root
            )
            if not root_workbook_relation:
                raise ValueError("XLSX package relationship to its workbook is missing.")
            relation_targets = {
                item.attrib["Id"]: item.attrib
                for item in relationships_root
                if item.tag == package_namespace + "Relationship" and "Id" in item.attrib
            }
            style_relations = [
                relation
                for relation in relation_targets.values()
                if relation.get("Type", "").endswith("/styles")
            ]
            if len(style_relations) > 1:
                raise ValueError("XLSX contains multiple styles relationships.")
            number_formats: list[str | None] = [None]
            if style_relations:
                style_relation = style_relations[0]
                if style_relation.get("TargetMode", "Internal") == "External":
                    raise ValueError("External XLSX styles are not supported.")
                style_target = style_relation.get("Target", "")
                style_part = (
                    style_target.lstrip("/")
                    if style_target.startswith("/")
                    else normpath(join_posix("xl", style_target))
                )
                if not style_part.startswith("xl/") or ".." in style_part.split("/"):
                    raise ValueError("XLSX styles path escapes the workbook package.")
                style_matches = [item for item in entries if item.filename == style_part]
                if len(style_matches) != 1:
                    raise ValueError("XLSX styles package part is missing or duplicated.")
                style_info = style_matches[0]
                if style_info.flag_bits & 1 or style_info.file_size > MAX_OOXML_PART_BYTES:
                    raise ValueError("XLSX styles XML exceeds its extraction limit.")
                total_xml_bytes += style_info.file_size
                if total_xml_bytes > MAX_XLSX_XML_BYTES:
                    raise ValueError("XLSX XML exceeds its extraction limit.")
                number_formats = _parse_xlsx_number_formats(archive.read(style_info))
            spreadsheet_namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            workbook_rel_namespace = (
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
            )
            sheet_specs = [
                (item.attrib.get("name", "Sheet"), item.attrib.get(workbook_rel_namespace + "id"))
                for item in workbook_root.iter(spreadsheet_namespace + "sheet")
            ]
            if not sheet_specs:
                raise ValueError("XLSX workbook contains no worksheets.")
            if len(sheet_specs) > MAX_XLSX_SHEETS:
                raise ValueError(f"XLSX exceeds the {MAX_XLSX_SHEETS}-worksheet limit.")

            parts = {item.filename: item for item in entries}
            extracted: list[str] = []
            cell_count = 0
            visited: set[str] = set()
            for name, relationship_id in sheet_specs:
                relation = relation_targets.get(relationship_id or "")
                if relation is None or not relation.get("Type", "").endswith("/worksheet"):
                    raise ValueError("XLSX worksheet relationship is missing or unsupported.")
                if relation.get("TargetMode", "Internal") == "External":
                    raise ValueError("External XLSX worksheet links are not supported.")
                target = relation.get("Target", "")
                part = (
                    target.lstrip("/")
                    if target.startswith("/")
                    else normpath(join_posix("xl", target))
                )
                if not part.startswith("xl/") or ".." in part.split("/"):
                    raise ValueError("XLSX worksheet path escapes the workbook package.")
                if part in visited or part not in parts:
                    raise ValueError("XLSX worksheet package part is missing or duplicated.")
                visited.add(part)
                info = parts[part]
                if info.flag_bits & 1 or info.file_size > MAX_OOXML_PART_BYTES:
                    raise ValueError("XLSX worksheet XML exceeds its extraction limit.")
                total_xml_bytes += info.file_size
                if total_xml_bytes > MAX_XLSX_XML_BYTES:
                    raise ValueError("XLSX XML exceeds its extraction limit.")
                sheet_root = _parse_office_xml(archive.read(info), "XLSX worksheet")
                extracted.append(f"[Sheet: {name}]")
                for row in sheet_root.iter(spreadsheet_namespace + "row"):
                    values: list[str] = []
                    for cell in row:
                        if cell.tag != spreadsheet_namespace + "c":
                            continue
                        cell_count += 1
                        if cell_count > MAX_XLSX_CELLS:
                            raise ValueError(
                                f"XLSX exceeds the {MAX_XLSX_CELLS}-cell extraction limit."
                            )
                        value = _xlsx_cell_value(cell, shared_strings, spreadsheet_namespace)
                        formula = next(
                            (node for node in cell if node.tag == spreadsheet_namespace + "f"),
                            None,
                        )
                        if formula is not None:
                            expression = (formula.text or "").strip()
                            if expression:
                                if len(expression) > MAX_XLSX_FORMULA_CHARS:
                                    expression = (
                                        expression[:MAX_XLSX_FORMULA_CHARS] + "…[formula truncated]"
                                    )
                                formula_text = f"[formula: ={expression}]"
                            else:
                                formula_kind = formula.attrib.get("t", "shared")
                                shared_index = formula.attrib.get("si")
                                group = f" group {shared_index}" if shared_index is not None else ""
                                formula_text = f"[formula: {formula_kind}{group}; expression not present in cell]"
                            value = f"{value} {formula_text}" if value else formula_text
                        style_value = cell.attrib.get("s")
                        if style_value is not None:
                            try:
                                style_index = int(style_value)
                                if style_index < 0:
                                    raise ValueError
                                number_format = number_formats[style_index]
                            except (ValueError, IndexError):
                                raise ValueError("XLSX cell references an invalid style.") from None
                            if number_format:
                                format_text = number_format
                                if len(format_text) > MAX_XLSX_FORMAT_CHARS:
                                    format_text = (
                                        format_text[:MAX_XLSX_FORMAT_CHARS] + "…[format truncated]"
                                    )
                                suffix = f" [number format: {format_text}]"
                                value = f"{value}{suffix}" if value else suffix.strip()
                        if value:
                            reference = cell.attrib.get("r")
                            values.append(f"{reference}={value}" if reference else value)
                    if values:
                        extracted.append("\t".join(values))
    except ValueError:
        raise
    except (BadZipFile, RuntimeError, OSError, EOFError, KeyError):
        raise ValueError("XLSX could not be safely parsed for text extraction.") from None
    text = "\n".join(extracted).strip()
    if not any(line and not line.startswith("[Sheet:") for line in text.splitlines()):
        raise ValueError("XLSX contains no extractable cell text.")
    return _bound(text)


def _unique_part(entries: list[ZipInfo], name: str) -> ZipInfo:
    matches = [item for item in entries if item.filename == name]
    if len(matches) != 1:
        raise ValueError(f"XLSX package is missing its unique {name} part.")
    return matches[0]


def _parse_office_xml(content: bytes, label: str) -> ElementTree.Element:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError(f"{label} XML declarations are not supported.")
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError:
        raise ValueError(f"{label} could not be safely parsed.") from None


def _parse_shared_strings(content: bytes) -> list[str]:
    root = _parse_office_xml(content, "XLSX shared strings")
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    if root.tag != namespace + "sst":
        raise ValueError("XLSX shared strings part has an invalid root element.")
    items = list(root.iter(namespace + "si"))
    if len(items) > MAX_XLSX_SHARED_STRINGS:
        raise ValueError(f"XLSX exceeds the {MAX_XLSX_SHARED_STRINGS}-string extraction limit.")
    return ["".join(node.text or "" for node in item.iter(namespace + "t")) for item in items]


def _parse_xlsx_number_formats(content: bytes) -> list[str | None]:
    root = _parse_office_xml(content, "XLSX styles")
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    if root.tag != namespace + "styleSheet":
        raise ValueError("XLSX styles part has an invalid root element.")
    format_sections = [item for item in root if item.tag == namespace + "numFmts"]
    xfs_sections = [item for item in root if item.tag == namespace + "cellXfs"]
    if len(format_sections) > 1 or len(xfs_sections) != 1:
        raise ValueError("XLSX styles part has an invalid number-format table.")
    custom_formats: dict[int, str] = {}
    if format_sections:
        for item in format_sections[0]:
            if item.tag != namespace + "numFmt":
                continue
            try:
                identity = int(item.attrib["numFmtId"])
            except (KeyError, ValueError):
                raise ValueError("XLSX styles contain an invalid custom number format.") from None
            code = item.attrib.get("formatCode")
            if not code or identity in custom_formats:
                raise ValueError("XLSX styles contain a missing or duplicate number format.")
            custom_formats[identity] = code
    if len(xfs_sections[0]) > MAX_XLSX_CELLS:
        raise ValueError("XLSX styles contain too many cell formats.")
    built_in_formats = {
        0: None,
        1: "0",
        2: "0.00",
        3: "#,##0",
        4: "#,##0.00",
        9: "0%",
        10: "0.00%",
        11: "0.00E+00",
        12: "# ?/?",
        13: "# ??/??",
        14: "m/d/yy",
        15: "d-mmm-yy",
        16: "d-mmm",
        17: "mmm-yy",
        18: "h:mm AM/PM",
        19: "h:mm:ss AM/PM",
        20: "h:mm",
        21: "h:mm:ss",
        22: "m/d/yy h:mm",
        37: "#,##0 ;(#,##0)",
        38: "#,##0 ;[Red](#,##0)",
        39: "#,##0.00;(#,##0.00)",
        40: "#,##0.00;[Red](#,##0.00)",
        49: "@",
    }
    result: list[str | None] = []
    for item in xfs_sections[0]:
        if item.tag != namespace + "xf":
            continue
        try:
            format_id = int(item.attrib.get("numFmtId", "0"))
        except ValueError:
            raise ValueError("XLSX styles contain an invalid number-format ID.") from None
        if format_id in custom_formats:
            result.append(custom_formats[format_id])
        else:
            result.append(built_in_formats.get(format_id, f"built-in format #{format_id}"))
    if not result:
        raise ValueError("XLSX styles contain no cell formats.")
    return result


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str], namespace: str) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(namespace + "t"))
    value = next((node.text or "" for node in cell if node.tag == namespace + "v"), "")
    if cell_type == "s":
        try:
            index = int(value)
            if index < 0:
                raise IndexError
            return shared_strings[index]
        except (ValueError, IndexError):
            raise ValueError("XLSX cell references an invalid shared string.") from None
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE" if value == "0" else value
    return value


def _bound(text: str) -> str:
    if len(text) > MAX_DOCUMENT_CHARS:
        return (
            text[:MAX_DOCUMENT_CHARS]
            + "\n[Document text clipped at the 48,000-character extraction limit.]"
        )
    return text
