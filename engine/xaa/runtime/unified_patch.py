"""Strict, bounded single-file unified diffs without fuzzy matching or subprocesses."""

import re

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?")


def apply_unified_patch(source: str, patch: str, path: str) -> str:
    if len(patch.encode("utf-8")) > 65536:
        raise ValueError("Patch exceeds the 64 KiB byte limit.")
    if not patch.endswith("\n"):
        raise ValueError(
            "Unified patch lines must end with a newline; use the explicit no-newline marker for file contents."
        )
    lines = patch.splitlines(keepends=True)
    if (
        len(lines) < 3
        or lines[0].rstrip("\r\n") != f"--- a/{path}"
        or lines[1].rstrip("\r\n") != f"+++ b/{path}"
    ):
        raise ValueError("Patch must have exactly matching --- a/path and +++ b/path headers.")
    # The conventional marker changes the preceding payload's newline, not file contents itself.
    normalized: list[str] = []
    for line in lines[2:]:
        if line.rstrip("\r\n") == "\\ No newline at end of file":
            if (
                not normalized
                or normalized[-1][:1] not in {" ", "+", "-"}
                or not normalized[-1].endswith("\n")
            ):
                raise ValueError("Invalid no-newline marker.")
            normalized[-1] = normalized[-1].removesuffix("\n").removesuffix("\r")
        else:
            normalized.append(line)
    original = source.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    index = 0
    hunks = 0
    while index < len(normalized):
        header = _HUNK.fullmatch(normalized[index].rstrip("\r\n"))
        if header is None:
            raise ValueError(
                "Expected a unified hunk header; extra files and metadata are unsupported."
            )
        hunks += 1
        if hunks > 128:
            raise ValueError("Patch exceeds 128 hunks.")
        old_line, old_count, new_line, new_count = (
            int(header[1]),
            int(header[2] or "1"),
            int(header[3]),
            int(header[4] or "1"),
        )
        start = old_line - 1 if old_count else old_line
        new_start = new_line - 1 if new_count else new_line
        if start < cursor or start > len(original) or new_start < 0:
            raise ValueError("Patch hunk position is outside the file or overlaps a prior hunk.")
        output.extend(original[cursor:start])
        if new_start != len(output):
            raise ValueError("Patch new-file coordinates disagree with earlier hunks.")
        cursor = start
        removed = added = 0
        index += 1
        while index < len(normalized) and not normalized[index].startswith("@@"):
            line = normalized[index]
            prefix, text = line[:1], line[1:]
            if prefix not in {" ", "-", "+"}:
                raise ValueError("Invalid unified hunk line.")
            if prefix in {" ", "-"}:
                if cursor >= len(original) or original[cursor] != text:
                    raise ValueError("Patch context does not match the exact file bytes.")
                cursor += 1
                removed += 1
            if prefix in {" ", "+"}:
                output.append(text)
                added += 1
            index += 1
        if (removed, added) != (old_count, new_count):
            raise ValueError("Patch hunk line counts disagree with its header.")
    if not hunks:
        raise ValueError("Patch must contain a hunk.")
    output.extend(original[cursor:])
    result = "".join(output)
    if len(result.encode("utf-8")) > 65536:
        raise ValueError("Patched output exceeds the 64 KiB byte limit.")
    return result
