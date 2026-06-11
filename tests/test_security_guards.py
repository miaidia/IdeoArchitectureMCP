"""Repo-wide security guard tests (Phase 14B; F-0477/0496/0497, NFR-SEC-006).

* **No shell-outs** (F-0496/0497 tool/command allowlist): no MCP tool or any
  package code spawns processes — ``subprocess`` / ``os.system`` / ``os.popen``
  must not appear in ``packages/`` or ``apps/`` source. There are currently
  ZERO known-safe exceptions; adding one requires listing it here with a
  justification.
* **No hardcoded secrets** (F-0477): pattern scan over source + infra. The
  documented dev-only defaults (``minioadmin``, ``plot:plot`` in compose/env
  examples) are explicitly allowed — they are published MinIO/postgres dev
  defaults, not secrets.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Known-safe shell-out locations: NONE today (justify any addition here).
SUBPROCESS_ALLOWLIST: frozenset[str] = frozenset()


def _source_files(*roots: str) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(
            p
            for p in (REPO / root).rglob("*.py")
            if "__pycache__" not in p.parts and ".venv" not in p.parts
        )
    return files


def test_no_subprocess_or_os_system_in_source() -> None:
    # actual USAGE patterns (imports/calls), not prose mentions in docstrings
    pattern = re.compile(
        r"^\s*(?:import\s+subprocess|from\s+subprocess\s+import)"
        r"|subprocess\.(?:run|call|Popen|check_output|check_call)"
        r"|os\.system\s*\(|os\.popen\s*\(",
        re.MULTILINE,
    )
    offenders: list[str] = []
    for path in _source_files("packages", "apps"):
        rel = str(path.relative_to(REPO))
        if rel in SUBPROCESS_ALLOWLIST:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == [], f"shell-out found outside the allowlist: {offenders}"


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "assigned_secret_literal",
        re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|password|auth[_-]?token|access[_-]?token)\b"
            r"\s*[:=]\s*[\"'][A-Za-z0-9+/_\-]{16,}[\"']"
        ),
    ),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
)

#: Dev-only documented defaults (NOT secrets): MinIO/postgres dev credentials.
_ALLOWED_VALUES = ("minioadmin", "plot:plot")


def test_no_hardcoded_secrets_in_source_and_infra() -> None:
    offenders: list[str] = []
    files = _source_files("packages", "apps")
    files += [
        p
        for root in ("infra", ".github")
        for p in (REPO / root).rglob("*")
        if p.is_file() and p.suffix in (".yml", ".yaml", ".tf", ".json", "")
    ]
    files.append(REPO / "Dockerfile")
    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if any(allowed in match.group(0) for allowed in _ALLOWED_VALUES):
                    continue
                offenders.append(f"{path.relative_to(REPO)}: {name}")
    assert offenders == [], f"possible hardcoded secrets: {offenders}"


def test_env_example_has_no_real_looking_values() -> None:
    env_example = REPO / ".env.example"
    if not env_example.exists():
        return
    text = env_example.read_text(encoding="utf-8")
    for name, pattern in _SECRET_PATTERNS:
        matches = [
            m.group(0)
            for m in pattern.finditer(text)
            if not any(allowed in m.group(0) for allowed in _ALLOWED_VALUES)
        ]
        assert matches == [], f".env.example contains {name}: {matches}"
