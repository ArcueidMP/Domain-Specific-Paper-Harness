"""Generate the checked-in OpenAPI contract from FastAPI."""

from __future__ import annotations

import json
from pathlib import Path

from paper_harness.entrypoints.api import app


def generate_openapi(path: Path) -> None:
    path.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    generate_openapi(Path("apps/api/openapi.json"))
