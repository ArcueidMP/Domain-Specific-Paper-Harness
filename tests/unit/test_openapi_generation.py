"""Checks for deterministic OpenAPI artifact generation."""

from pathlib import Path

from paper_harness.entrypoints.openapi import generate_openapi


def test_openapi_generator_uses_lf_line_endings(tmp_path: Path) -> None:
    generated = tmp_path / "openapi.json"

    generate_openapi(generated)

    content = generated.read_bytes()
    assert content.endswith(b"\n")
    assert b"\r\n" not in content
