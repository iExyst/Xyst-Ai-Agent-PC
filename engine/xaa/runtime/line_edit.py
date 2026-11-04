"""Explicit LF line arrays, an alternative to nested multiline JSON strings."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from xaa.runtime.executor import WriteFileArguments


class WriteLinesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=2048)
    lines: list[str] = Field(max_length=2048)
    final_newline: bool
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("lines")
    @classmethod
    def separate_lines(cls, value: list[str]) -> list[str]:
        if any("\n" in line or "\r" in line for line in value):
            raise ValueError(
                "Each line must exclude CR/LF; line separators are supplied explicitly."
            )
        return value

    def write_arguments(self) -> WriteFileArguments:
        content = "\n".join(self.lines) + ("\n" if self.final_newline else "")
        if len(content.encode("utf-8")) > 65536:
            raise ValueError("Line-based edits exceed the 64 KiB file limit.")
        return WriteFileArguments(
            path=self.path, content=content, expected_sha256=self.expected_sha256
        )
