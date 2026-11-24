"""Safe user-facing filenames for retained upload artifacts."""

from __future__ import annotations

import unicodedata

_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/plain": "txt",
    "text/markdown": "md",
    "audio/wav": "wav",
}


def safe_upload_filename(filename: str | None, media_type: str) -> str:
    """Keep a display basename while fixing its extension and removing unsafe characters."""
    extension = _EXTENSIONS.get(media_type, "bin")
    fallback = f"xaa-attachment.{extension}"
    if not isinstance(filename, str) or not filename:
        return fallback

    basename = unicodedata.normalize("NFC", filename.replace("\\", "/").rsplit("/", 1)[-1])
    cleaned = "".join(
        "_" if char in '<>:"|?*' else char
        for char in basename
        if unicodedata.category(char)[0] != "C" and char not in "/\\"
    ).strip(" .")
    if not cleaned:
        return fallback

    stem, separator, suffix = cleaned.rpartition(".")
    if separator and suffix:
        cleaned = f"{stem or cleaned}.{extension}"
    else:
        cleaned = f"{cleaned}.{extension}"

    stem, _, suffix = cleaned.rpartition(".")
    if stem.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        stem = f"_{stem}"
    max_stem_length = 180 - len(suffix) - 1
    stem = stem[:max_stem_length].rstrip(" .") or "xaa-attachment"
    return f"{stem}.{suffix}"
