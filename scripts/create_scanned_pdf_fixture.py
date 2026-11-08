"""Create a text-as-pixels PDF fixture for native OCR acceptance."""

from __future__ import annotations

import sys
from pathlib import Path

GLYPHS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "N": ("10001", "11001", "10101", "10101", "10011", "10001", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
}


def scanned_text_pdf(marker: str) -> bytes:
    if not marker or any(character not in GLYPHS for character in marker):
        raise ValueError("Fixture marker contains unsupported glyphs.")
    cell, advance, left, top = 5.0, 30.0, 30.0, 25.0
    page_height = 300
    content = ["q 1 1 1 rg 0 0 720 300 re f 0 0 0 rg"]
    split_at = marker.index("-") + 17
    lines = (marker,) if split_at >= len(marker) else (marker[:split_at], marker[split_at:])
    for line_index, line in enumerate(lines):
        for character_index, character in enumerate(line):
            for row, pattern in enumerate(GLYPHS[character]):
                for column, lit in enumerate(pattern):
                    if lit == "1":
                        x = left + character_index * advance + column * cell
                        y = page_height - top - line_index * 70 - (row + 1) * cell
                        content.append(f"{x:g} {y:g} {cell:g} {cell:g} re f")
    content.append("Q")
    stream = "\n".join(content).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 720 300] /Resources << >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    parts = [b"%PDF-1.4\n%ASCII fixture\n"]
    offsets = [0]
    position = len(parts[0])
    for index, value in enumerate(objects, start=1):
        offsets.append(position)
        encoded = str(index).encode("ascii") + b" 0 obj\n" + value + b"\nendobj\n"
        parts.append(encoded)
        position += len(encoded)
    xref = position
    lines = [f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii")]
    lines.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    lines.append(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return b"".join([*parts, *lines])


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: create_scanned_pdf_fixture.py OUTPUT MARKER")
    Path(sys.argv[1]).write_bytes(scanned_text_pdf(sys.argv[2]))


if __name__ == "__main__":
    main()
