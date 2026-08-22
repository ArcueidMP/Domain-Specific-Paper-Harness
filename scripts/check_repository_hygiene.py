"""Check tracked and generated artifacts for secrets and forbidden repository data."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

ALLOWED_TRACKED_FILES = frozenset({".env.example"})
FORBIDDEN_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".terraform",
        ".venv",
        "__pycache__",
        "data",
        "dist",
        "downloads",
        "models",
        "node_modules",
    }
)
FORBIDDEN_CREDENTIAL_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})
FORBIDDEN_MODEL_SUFFIXES = frozenset({".ckpt", ".onnx", ".pt", ".pth", ".safetensors"})
SENSITIVE_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "private key material",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "Google service-account document",
        re.compile(rb'"type"\s*:\s*"service_account"'),
    ),
    ("Google API key", re.compile(rb"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}")),
    ("AWS access key", re.compile(rb"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}")),
    ("GitHub token", re.compile(rb"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{24,}")),
    ("Slack token", re.compile(rb"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{20,}")),
    ("provider API key", re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{24,}")),
    (
        "OAuth client secret",
        re.compile(rb'"client_secret"\s*:\s*"(?!<|\$\{)[^"\r\n]{16,}"'),
    ),
)


class HygieneCommandError(RuntimeError):
    """A required read-only Git operation failed."""


@dataclass(frozen=True, slots=True)
class HygieneFinding:
    """A path and non-sensitive explanation of a repository hygiene violation."""

    path: str
    rule: str


def _run_git(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = f"git {' '.join(arguments)} exited with {result.returncode}"
        raise HygieneCommandError(detail)
    return result.stdout


def _path_rule(path: str, *, generated: bool) -> str | None:
    normalized = PurePosixPath(path.replace("\\", "/"))
    normalized_text = normalized.as_posix()
    lower_text = normalized_text.lower()
    lower_name = normalized.name.lower()
    lower_parts = {part.lower() for part in normalized.parts}

    if not generated and normalized_text in ALLOWED_TRACKED_FILES:
        return None
    if (
        not generated
        and lower_text.startswith("tests/contract/fixtures/")
        and lower_name.endswith(".tei.xml")
    ):
        return None
    if lower_name == ".env" or lower_name.startswith(".env."):
        return "environment file must remain untracked"
    if not generated and lower_parts & FORBIDDEN_DIRECTORY_NAMES:
        return "generated, cached, model, download, or local-data directory is tracked"
    if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_CREDENTIAL_SUFFIXES):
        return "credential or private-key file is tracked"
    if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_MODEL_SUFFIXES):
        return "model weight file is tracked"
    if lower_name.endswith((".pyc", ".pyo")):
        return "compiled Python artifact is tracked"
    if lower_name.endswith(".pdf"):
        return "downloaded PDF is tracked"
    if lower_name.endswith(".tei.xml"):
        return "downloaded TEI document is tracked"
    if lower_name.endswith(".tfplan") or ".tfstate" in lower_name:
        return "Terraform plan or state is tracked"
    if lower_name.endswith((".tfvars", ".tfvars.json")):
        return "Terraform variable values are tracked"
    if lower_name in {"application_default_credentials.json", "credentials.json"}:
        return "credential document is tracked"
    if lower_name.endswith(".json") and re.search(
        r"service[-_]?account", lower_name, flags=re.IGNORECASE
    ):
        return "service-account key document is tracked"
    if lower_name.endswith((".backup", ".dump")):
        return "database backup is tracked"
    if generated and lower_name.endswith(".map"):
        return "production source map is present"
    if lower_text.startswith(".git/"):
        return "Git metadata is present"
    return None


def _content_findings(path: str, content: bytes, *, generated: bool) -> list[HygieneFinding]:
    del generated
    return [
        HygieneFinding(path=path, rule=rule)
        for rule, pattern in SENSITIVE_CONTENT_PATTERNS
        if pattern.search(content) is not None
    ]


def check_tracked_repository(repository: Path) -> tuple[HygieneFinding, ...]:
    """Inspect indexed files and their working-tree versions without following symlinks."""

    repository = repository.resolve()
    raw_indexed_paths = _run_git(repository, "ls-files", "-z")
    indexed_paths = {
        item.decode("utf-8", errors="surrogateescape")
        for item in raw_indexed_paths.split(b"\0")
        if item
    }
    paths = sorted(indexed_paths)
    findings: list[HygieneFinding] = []
    for path in paths:
        rule = _path_rule(path, generated=False)
        if rule is not None:
            findings.append(HygieneFinding(path=path, rule=rule))

        indexed_content = _run_git(repository, "cat-file", "blob", f":{path}")
        findings.extend(_content_findings(path, indexed_content, generated=False))

        working_path = repository / Path(path)
        if working_path.is_symlink() or not working_path.is_file():
            continue
        try:
            working_content = working_path.read_bytes()
        except OSError:
            findings.append(HygieneFinding(path, "prospective tracked file could not be read"))
            continue
        if working_content != indexed_content:
            findings.extend(_content_findings(path, working_content, generated=False))
    return tuple(sorted(set(findings), key=lambda finding: (finding.path, finding.rule)))


def _display_path(repository: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repository).as_posix()
    except ValueError:
        return str(path.resolve())


def _generated_files(path: Path) -> Iterable[Path]:
    if path.is_symlink() or path.is_file():
        return (path,)
    return tuple(sorted(path.rglob("*")))


def check_generated_artifacts(
    repository: Path,
    generated_paths: Sequence[Path],
) -> tuple[HygieneFinding, ...]:
    """Inspect generated OpenAPI and frontend output without printing matched content."""

    repository = repository.resolve()
    findings: list[HygieneFinding] = []
    for configured_path in generated_paths:
        path = configured_path if configured_path.is_absolute() else repository / configured_path
        display_root = _display_path(repository, path)
        if not path.exists() and not path.is_symlink():
            findings.append(HygieneFinding(display_root, "required generated artifact is missing"))
            continue
        if path.is_dir() and path.name == "dist" and not (path / "index.html").is_file():
            findings.append(HygieneFinding(display_root, "frontend build is missing index.html"))

        candidates = tuple(_generated_files(path))
        if path.is_dir() and not candidates:
            findings.append(HygieneFinding(display_root, "generated artifact directory is empty"))
        for candidate in candidates:
            display = _display_path(repository, candidate)
            if candidate.is_symlink():
                findings.append(HygieneFinding(display, "generated artifact must not be a symlink"))
                continue
            if not candidate.is_file():
                continue
            rule = _path_rule(display, generated=True)
            if rule is not None:
                findings.append(HygieneFinding(display, rule))
            try:
                content = candidate.read_bytes()
            except OSError:
                findings.append(HygieneFinding(display, "generated artifact could not be read"))
                continue
            findings.extend(_content_findings(display, content, generated=True))
            if candidate.name == "openapi.json":
                try:
                    document = json.loads(content)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    findings.append(HygieneFinding(display, "OpenAPI artifact is not valid JSON"))
                else:
                    if not isinstance(document, dict) or "openapi" not in document:
                        findings.append(
                            HygieneFinding(
                                display, "OpenAPI artifact lacks an openapi document key"
                            )
                        )
    return tuple(sorted(set(findings), key=lambda finding: (finding.path, finding.rule)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check tracked files and generated artifacts for focused hygiene violations."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Git repository root (default: current directory).",
    )
    parser.add_argument("--tracked", action="store_true", help="Inspect every indexed Git blob.")
    parser.add_argument(
        "--generated",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Inspect a required generated file or directory; may be repeated.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    repository = Path(arguments.repository_root)
    generated_paths = tuple(cast(list[Path], arguments.generated))
    if not bool(arguments.tracked) and not generated_paths:
        parser.error("at least one of --tracked or --generated is required")

    findings: list[HygieneFinding] = []
    try:
        if bool(arguments.tracked):
            findings.extend(check_tracked_repository(repository))
        if generated_paths:
            findings.extend(check_generated_artifacts(repository, generated_paths))
    except HygieneCommandError as error:
        print(f"Repository hygiene check could not run: {error}")
        return 2

    unique_findings = sorted(set(findings), key=lambda finding: (finding.path, finding.rule))
    if unique_findings:
        print("Repository hygiene check failed:")
        for finding in unique_findings:
            print(f"- {finding.path}: {finding.rule}")
        return 1

    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
