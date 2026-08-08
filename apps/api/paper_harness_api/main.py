"""ASGI application exported by the Web/API runtime unit."""

from __future__ import annotations

import os

import uvicorn

from paper_harness.entrypoints.api import app

__all__ = ["app", "run"]


def run() -> None:
    uvicorn.run(
        "paper_harness_api.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        access_log=False,
    )
