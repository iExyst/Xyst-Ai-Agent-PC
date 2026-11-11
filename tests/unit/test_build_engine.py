from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

spec = spec_from_file_location(
    "xaa_build_engine", Path(__file__).parents[2] / "scripts" / "build_engine.py"
)
assert spec is not None and spec.loader is not None
build_engine = module_from_spec(spec)
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
