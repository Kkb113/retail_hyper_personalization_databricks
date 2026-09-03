"""Fail CI when the Azure Databricks source contains common credentials."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    ROOT / "azure_databricks",
    ROOT / ".github",
    ROOT / "tests" / "azure_databricks",
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "azure_databricks_implementation.md",
    ROOT / "pyproject.toml",
)
TEXT_SUFFIXES = {"", ".json", ".md", ".py", ".toml", ".yml", ".yaml"}
PATTERNS = {
    "Databricks personal access token": re.compile(r"\bdapi[a-zA-Z0-9]{20,}\b"),
    "OpenAI-style API key": re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "hard-coded client secret": re.compile(
        r"(?im)^\s*(?:client_secret|databricks_token|access_token)\s*[:=]\s*[\"']?(?!\$\{)[^#\s]+"
    ),
}


def _files():
    for path in SCAN_ROOTS:
        if path.is_file():
            yield path
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.suffix in TEXT_SUFFIXES:
                    yield candidate


def main() -> None:
    findings: list[str] = []
    for path in sorted(set(_files())):
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: {label}")
    if findings:
        raise SystemExit("Credential scan failed:\n" + "\n".join(findings))
    print("Credential scan passed; no embedded deployment secrets found")


if __name__ == "__main__":
    main()
