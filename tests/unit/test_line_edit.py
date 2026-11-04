import pytest
from pydantic import ValidationError
from xaa.runtime.line_edit import WriteLinesArguments


def test_line_edit_preserves_literal_backslashes_and_controls_exact_final_newline():
    arguments = WriteLinesArguments(
        path="sample.py",
        lines=["def value():", r"    return 'literal\n'"],
        final_newline=True,
        expected_sha256="a" * 64,
    )
    assert (
        arguments.write_arguments().content.encode() == b"def value():\n    return 'literal\\n'\n"
    )
    assert (
        arguments.model_copy(update={"final_newline": False}).write_arguments().content.encode()
        == b"def value():\n    return 'literal\\n'"
    )


@pytest.mark.parametrize("lines", [["two\nlines"], ["two\rlines"]])
def test_embedded_line_separators_are_refused(lines):
    with pytest.raises(ValidationError):
        WriteLinesArguments(
            path="sample.py", lines=lines, final_newline=True, expected_sha256="a" * 64
        )
