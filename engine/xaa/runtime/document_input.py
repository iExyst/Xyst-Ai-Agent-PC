"""Bounded text extraction for user-selected document attachments."""

from __future__ import annotations

from binascii import crc32
from io import BytesIO
from math import ceil, isfinite
from posixpath import join as join_posix
from posixpath import normpath
from struct import pack
from typing import Any, cast
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
            if len(content_types) != 1 or len(documents) != 1:
                raise ValueError(
                    "DOCX package is missing its unique content types or document part."
                )
            if len(relationship_parts) > 1:
                raise ValueError("DOCX package contains duplicate document relationships.")
            if len(numbering_parts) > 1:
                raise ValueError("DOCX package contains duplicate numbering definitions.")
            content_type_info, document_info = content_types[0], documents[0]
            relationship_info = relationship_parts[0] if relationship_parts else None
            numbering_info = numbering_parts[0] if numbering_parts else None
            if any(
                info.flag_bits & 1
                for info in (content_type_info, document_info, relationship_info, numbering_info)
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
                or sum(
                    info.file_size
                    for info in (
                        content_type_info,
                        document_info,
                        relationship_info,
                        numbering_info,
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
    list_markers = _parse_docx_numbering(numbering_xml, namespace)

    def paragraph_text(paragraph: ElementTree.Element) -> str:
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
        if not properties:
            return value
        property_group = properties[0]
        style_properties = [item for item in property_group if item.tag == namespace + "pStyle"]
        if len(style_properties) > 1:
            raise ValueError("DOCX paragraph contains duplicate style markers.")
        prefix: list[str] = []
        if style_properties:
            style_id = style_properties[0].attrib.get(namespace + "val", "")
            if not style_id or len(style_id) > 128 or not style_id.isprintable():
                raise ValueError("DOCX paragraph contains an invalid style identifier.")
            normalized_style = style_id.replace(" ", "").casefold()
            if normalized_style.startswith("heading"):
                prefix.append(f"Heading: {style_id}")
        numbering_properties = [item for item in property_group if item.tag == namespace + "numPr"]
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
                raw_level = levels[0].attrib.get(namespace + "val", "0") if levels else "0"
                if (
                    not raw_id.isascii()
                    or not raw_id.isdecimal()
                    or len(raw_id) > 10
                    or not raw_level.isascii()
                    or not raw_level.isdecimal()
                    or len(raw_level) > 2
                    or int(raw_level) > 8
                ):
                    raise ValueError("DOCX paragraph contains invalid list metadata.")
                if raw_id != "0":
                    marker = list_markers.get((int(raw_id), int(raw_level)))
                    if marker is None:
                        prefix.append(f"List item, level {int(raw_level) + 1}")
                    else:
                        number_format, marker_text = marker
                        prefix.append(
                            f"List {number_format}, level {int(raw_level) + 1}, marker {marker_text}"
                        )
        if prefix and value:
            value = f"[{'; '.join(prefix)}] {value}"
        return value

    def content_blocks(container: ElementTree.Element) -> list[str]:
        def table_cell_text(cell: ElementTree.Element) -> str:
            value = " / ".join(content_blocks(cell))
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
                value = paragraph_text(item)
                if value:
                    blocks.append(value)
            elif item.tag == namespace + "tbl":
                for row in item:
                    if row.tag != namespace + "tr":
                        continue
                    cells = [table_cell_text(cell) for cell in row if cell.tag == namespace + "tc"]
                    if any(cells):
                        blocks.append(" | ".join(cells))
            else:
                blocks.extend(content_blocks(item))
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


def _parse_docx_numbering(
    xml: bytes | None, namespace: str
) -> dict[tuple[int, int], tuple[str, str]]:
    """Read bounded list marker formats/templates without calculating sequence values."""
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

    abstract_definitions: dict[int, dict[int, tuple[str, str]]] = {}
    for abstract in root:
        if abstract.tag != namespace + "abstractNum":
            continue
        abstract_id = decimal_attribute(
            abstract.attrib.get(namespace + "abstractNumId", ""), "abstract ID"
        )
        if abstract_id in abstract_definitions:
            raise ValueError("DOCX numbering definitions contain duplicate abstract IDs.")
        levels: dict[int, tuple[str, str]] = {}
        for level in abstract:
            if level.tag != namespace + "lvl":
                continue
            level_id = decimal_attribute(level.attrib.get(namespace + "ilvl", ""), "level", 2)
            if level_id > 8 or level_id in levels:
                raise ValueError("DOCX numbering definitions contain a duplicate or invalid level.")
            formats = [item for item in level if item.tag == namespace + "numFmt"]
            texts = [item for item in level if item.tag == namespace + "lvlText"]
            if len(formats) > 1 or len(texts) > 1:
                raise ValueError("DOCX numbering level contains duplicate marker metadata.")
            number_format = formats[0].attrib.get(namespace + "val", "") if formats else ""
            marker_text = texts[0].attrib.get(namespace + "val", "") if texts else ""
            if (
                not number_format
                or len(number_format) > 64
                or not number_format.isascii()
                or not marker_text
                or len(marker_text) > 128
                or not marker_text.isprintable()
            ):
                raise ValueError("DOCX numbering level contains invalid marker metadata.")
            levels[level_id] = (number_format, marker_text)
        abstract_definitions[abstract_id] = levels

    instances: dict[tuple[int, int], tuple[str, str]] = {}
    seen_instances: set[int] = set()
    for instance in root:
        if instance.tag != namespace + "num":
            continue
        number_id = decimal_attribute(instance.attrib.get(namespace + "numId", ""), "list ID")
        abstract_ids = [item for item in instance if item.tag == namespace + "abstractNumId"]
        if len(abstract_ids) != 1 or number_id in seen_instances:
            raise ValueError("DOCX numbering instances contain missing or duplicate IDs.")
        seen_instances.add(number_id)
        abstract_id = decimal_attribute(
            abstract_ids[0].attrib.get(namespace + "val", ""), "abstract reference"
        )
        base_levels = abstract_definitions.get(abstract_id)
        if base_levels is None:
            raise ValueError("DOCX numbering instance references a missing abstract definition.")
        effective_levels: dict[int, tuple[str, str]] = base_levels.copy()
        for override in (item for item in instance if item.tag == namespace + "lvlOverride"):
            level_id = decimal_attribute(
                override.attrib.get(namespace + "ilvl", ""), "override level", 2
            )
            if level_id > 8:
                raise ValueError("DOCX numbering override has an invalid level.")
            replacements = [item for item in override if item.tag == namespace + "lvl"]
            if len(replacements) > 1:
                raise ValueError("DOCX numbering override contains duplicate levels.")
            if replacements:
                replacement_level = replacements[0]
                formats = [item for item in replacement_level if item.tag == namespace + "numFmt"]
                texts = [item for item in replacement_level if item.tag == namespace + "lvlText"]
                if len(formats) > 1 or len(texts) > 1:
                    raise ValueError("DOCX numbering override contains duplicate marker metadata.")
                if formats and texts:
                    number_format = formats[0].attrib.get(namespace + "val", "")
                    marker_text = texts[0].attrib.get(namespace + "val", "")
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
                    effective_levels[level_id] = (number_format, marker_text)
        for level_id, marker in effective_levels.items():
            instances[number_id, level_id] = marker
    return instances


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
