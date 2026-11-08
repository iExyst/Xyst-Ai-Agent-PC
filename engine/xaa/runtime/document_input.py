"""Bounded text extraction for user-selected document attachments."""

from __future__ import annotations

from io import BytesIO
from posixpath import join as join_posix
from posixpath import normpath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile, ZipInfo

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_CHARS = 48_000
MAX_PDF_PAGES = 50
MAX_OOXML_ENTRIES = 1024
MAX_OOXML_PART_BYTES = 4 * 1024 * 1024
MAX_XLSX_SHEETS = 50
MAX_XLSX_CELLS = 10_000
MAX_XLSX_SHARED_STRINGS = 50_000
MAX_XLSX_XML_BYTES = 8 * 1024 * 1024
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


def _extract_docx_text(content: bytes) -> str:
    """Read only bounded WordprocessingML text; never extract ZIP members to disk."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_OOXML_ENTRIES:
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
                or document_info.file_size > MAX_OOXML_PART_BYTES
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
