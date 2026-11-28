from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from xaa.runtime import frozen_archive

spec = spec_from_file_location(
    "xaa_build_engine", Path(__file__).parents[2] / "scripts" / "build_engine.py"
)
assert spec is not None and spec.loader is not None
build_engine: Any = module_from_spec(spec)
spec.loader.exec_module(build_engine)


def test_frozen_engine_copies_metadata_for_locked_runtime_distributions() -> None:
    arguments = build_engine.metadata_copy_arguments(
        "# generated lock export\n"
        "requests[security]>=2.0\n"
        'future-package>=1; python_version < "0"\n'
        "uvicorn[standard]==0.38.0\n"
    )

    assert arguments == [
        "--copy-metadata",
        "requests",
        "--copy-metadata",
        "uvicorn",
        "--copy-metadata",
        "xaa-engine",
    ]


def test_frozen_engine_metadata_export_rejects_invalid_requirement_lines() -> None:
    with pytest.raises(ValueError):
        build_engine.metadata_copy_arguments("not a valid requirement !!!\n")


class _Archive:
    def __init__(self, entries: dict[str, bytes], python_modules: set[str] | None = None) -> None:
        self.toc = dict.fromkeys(entries)
        self.entries = entries
        self.python_modules: set[str] = set() if python_modules is None else python_modules

    def extract(self, name: str) -> bytes:
        return self.entries[name]

    def open_embedded_archive(self, name: str) -> SimpleNamespace:
        assert name == "PYZ.pyz"
        return SimpleNamespace(toc=self.python_modules)


class _ReaderFactory:
    def __init__(self, archive: _Archive) -> None:
        self.archive = archive

    def __call__(self, _path: str) -> _Archive:
        return self.archive


def _use_archive(monkeypatch: pytest.MonkeyPatch, archive: _Archive) -> None:
    readers = SimpleNamespace(CArchiveReader=_ReaderFactory(archive))

    def _import_module(_name: str) -> Any:
        return readers

    monkeypatch.setattr(frozen_archive.importlib, "import_module", _import_module)


def test_frozen_runtime_size_verifier_sums_uncompressed_archive_members(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _use_archive(monkeypatch, _Archive({"first": b"abc", "second": b"defg"}))

    expanded_bytes = frozen_archive.verify_frozen_runtime_size(tmp_path / "engine.exe")

    assert expanded_bytes == 7


def test_frozen_runtime_size_verifier_fails_above_shared_appcontainer_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(frozen_archive, "MAX_SHIM_RUNTIME_BYTES", 3)
    _use_archive(monkeypatch, _Archive({"runtime": b"four"}))

    with pytest.raises(RuntimeError, match="Windows AppContainer runtime limit"):
        frozen_archive.verify_frozen_runtime_size(tmp_path / "engine.exe")


def test_frozen_audio_resources_require_dynamic_soundfile_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resources = {
        "_soundfile_data\\libsndfile_x64.dll": b"decoder",
        "_soundfile_data\\COPYING": b"decoder license",
        "soundfile-0.14.0.dist-info\\LICENSE": b"soundfile license",
        "numpy-2.5.5.dist-info\\licenses\\LICENSE.txt": b"numpy license",
    }
    _use_archive(monkeypatch, _Archive(resources))

    with pytest.raises(RuntimeError, match="dynamically imported _soundfile binding"):
        build_engine.verify_audio_decoder_resources(tmp_path / "engine.exe")
