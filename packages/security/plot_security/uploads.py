"""Upload sandbox for user documents (Phase 14B; F-0491–0493, §16, NFR-SEC-001/009).

User uploads are UNTRUSTED CONTENT. The sandbox enforces, in order:

1. **filename safety** — path-traversal guard (no separators, no ``..``, no NUL,
   no leading dot); the artifact key is additionally guarded by the
   ArtifactStore's own traversal check (F-0490);
2. **size cap** — ``Settings.upload_max_bytes`` (default 20 MB, F-0493) → typed
   :class:`UploadTooLarge`;
3. **type allowlist** — pdf / html / txt only (declared MIME *and* extension must
   agree) → :class:`UploadTypeForbidden`;
4. **page cap (PDF)** — a documented HEURISTIC estimate (``/Type /Page`` object
   scan, not a full PDF parse) → :class:`UploadPageCapExceeded`;
5. **char cap + injection screening (text types)** — extracted text is capped at
   ``Settings.upload_max_chars`` and screened by the EXISTING Phase 8
   untrusted-content screen (``plot_planning.parser.security``) at parse time;
   the sandbox records injection *flags* only, never echoes flagged text;
6. **quarantine storage** — bytes land under the ``quarantine/`` prefix of the
   artifact store via an injected ``store_bytes`` callback (this package never
   imports the reports/connectors packages — §9.4 decoupling);
7. **AV scan hook** — :class:`AvScanner` is an interface; no AV engine ships in
   this environment, so the default scanner reports the HONEST ``not_scanned``
   status (F-0492) — never a fake "clean".

Uploaded text is parsed exclusively through ``planning_parse_document``'s
untrusted-content mode — it is DATA, never instructions (NFR-SEC-002/003).
"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from plot_shared import Settings, get_settings

#: Allowed upload types (F-0493 type allowlist): declared MIME → extensions.
ALLOWED_UPLOAD_TYPES: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "text/html": (".html", ".htm"),
    "text/plain": (".txt",),
}

#: MIME types whose text is retained for the parser (PDF text extraction is NOT
#: implemented — no PDF parser in this environment; recorded honestly, §21).
_TEXT_TYPES = ("text/html", "text/plain")

#: Crude PDF page-count estimate: count page OBJECTS (documented heuristic).
_PDF_PAGE_PATTERN = re.compile(rb"/Type\s*/Page(?![s])")

_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$")


class UploadRejected(ValueError):
    """Base class for typed sandbox rejections (maps to HTTP 4xx in the API)."""


class UploadFilenameRejected(UploadRejected):
    """Path traversal / unsafe filename (F-0490)."""


class UploadTooLarge(UploadRejected):
    """Size cap exceeded (F-0493) — HTTP 413."""


class UploadTypeForbidden(UploadRejected):
    """Type not on the allowlist (F-0493) — HTTP 415."""


class UploadPageCapExceeded(UploadRejected):
    """Estimated PDF page count over the configured cap."""


class AvScanner(Protocol):
    """AV scan hook (F-0492). Implementations return a status string."""

    def scan(self, data: bytes) -> str:
        """Return ``clean`` / ``infected`` / ``not_scanned`` (honest statuses only)."""
        ...


class NoAvScanner:
    """The honest default: NO antivirus engine is available in this environment.

    Reports ``not_scanned`` — never a fabricated ``clean`` (anti-pattern guard:
    faking AV capability). Deployments plug a real engine via the hook.
    """

    note = (
        "Brak silnika antywirusowego w tym środowisku — plik NIE został przeskanowany "
        "(status not_scanned, F-0492). Wdrożenie produkcyjne podpina własny skaner "
        "przez interfejs AvScanner."
    )

    def scan(self, data: bytes) -> str:  # noqa: ARG002 - interface contract
        return "not_scanned"


@dataclass(frozen=True)
class UploadRecord:
    """Sandboxed upload metadata (file content lives in quarantine storage only)."""

    file_id: str
    filename: str
    declared_type: str
    size_bytes: int
    sha256: str
    artifact_uri: str
    quarantine_key: str
    av_status: str
    av_note: str
    text: str | None = None  # capped extracted text (text types only)
    text_status: str = "no_text"  # extracted | truncated | extraction_unavailable | no_text
    page_count_estimate: int | None = None
    analysis_id: str | None = None
    purpose: str | None = None

    def summary(self) -> dict[str, Any]:
        """Result block for tool/API responses — NEVER includes the text/bytes."""
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "declared_type": self.declared_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "artifact_uri": self.artifact_uri,
            "quarantine_key": self.quarantine_key,
            "av_status": self.av_status,
            "av_note": self.av_note,
            "text_status": self.text_status,
            "text_chars": len(self.text) if self.text is not None else 0,
            "page_count_estimate": self.page_count_estimate,
            "analysis_id": self.analysis_id,
            "purpose": self.purpose,
        }


def safe_filename(filename: str) -> str:
    """Validate an upload filename against path traversal (F-0490).

    Allows a single plain path component (letters/digits/dot/dash/underscore/
    space, not starting with a dot); rejects separators, ``..``, NULs, control
    characters and empty names.
    """
    if not filename or "\x00" in filename:
        raise UploadFilenameRejected("Pusta nazwa pliku albo bajt NUL — odrzucono (F-0490).")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise UploadFilenameRejected(
            "Nazwa pliku zawiera separator ścieżki lub '..' — path traversal odrzucony (F-0490)."
        )
    if not _FILENAME_PATTERN.match(filename):
        raise UploadFilenameRejected(
            "Nazwa pliku poza dozwolonym wzorcem [A-Za-z0-9._ -] (max 255 znaków, "
            "bez kropki na początku) — odrzucono (F-0490)."
        )
    return filename


def _validate_type(filename: str, declared_type: str | None) -> str:
    """Resolve + validate the upload type from declared MIME and extension.

    NOTE: this is a CALLER-ASSERTED MIME check (declared type + extension must
    agree) — no magic-bytes sniffing of the content happens here. Content is
    screened at parse time by the untrusted-content channel, and bytes stay in
    quarantine storage regardless of what they really are.
    """
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    by_ext = next(
        (mime for mime, exts in ALLOWED_UPLOAD_TYPES.items() if ext in exts), None
    )
    if declared_type:
        declared = declared_type.split(";")[0].strip().lower()
        if declared not in ALLOWED_UPLOAD_TYPES:
            raise UploadTypeForbidden(
                f"Typ '{declared}' poza allowlistą uploadów "
                f"({', '.join(sorted(ALLOWED_UPLOAD_TYPES))}) — odrzucono (F-0493)."
            )
        if by_ext != declared:
            raise UploadTypeForbidden(
                f"Rozszerzenie '{ext or '(brak)'}' nie zgadza się z deklarowanym typem "
                f"'{declared}' — odrzucono (F-0493)."
            )
        return declared
    if by_ext is None:
        raise UploadTypeForbidden(
            f"Rozszerzenie '{ext or '(brak)'}' poza allowlistą uploadów — odrzucono (F-0493)."
        )
    return by_ext


def _extract_text(content: bytes, mime: str, max_chars: int) -> tuple[str | None, str]:
    """Capped text extraction for the parser channel (text types only).

    PDF text extraction is honestly UNAVAILABLE (no PDF parser in this
    environment) — the document is quarantined and the status says so (§21).
    """
    if mime == "application/pdf":
        return None, "extraction_unavailable"
    if mime not in _TEXT_TYPES:
        return None, "no_text"
    text = content.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars], "truncated"
    return text, "extracted"


def estimate_pdf_pages(content: bytes) -> int:
    """HEURISTIC page count: ``/Type /Page`` object scan (documented estimate)."""
    return len(_PDF_PAGE_PATTERN.findall(content))


def ingest_upload(
    filename: str,
    content: bytes,
    *,
    declared_type: str | None = None,
    purpose: str | None = None,
    analysis_id: str | None = None,
    store_bytes: Callable[[str, bytes, str], str],
    settings: Settings | None = None,
    av_scanner: AvScanner | None = None,
) -> UploadRecord:
    """Run the full sandbox pipeline over one upload; raise typed rejections.

    ``store_bytes(key, data, content_type) -> uri`` is the quarantine-storage
    callback (the caller passes ``ArtifactStore.put`` — this package stays free
    of report/connector imports).
    """
    settings = settings or get_settings()
    filename = safe_filename(filename)
    if len(content) > settings.upload_max_bytes:
        raise UploadTooLarge(
            f"Plik ma {len(content)} B — przekracza limit "
            f"{settings.upload_max_bytes} B (F-0493)."
        )
    mime = _validate_type(filename, declared_type)

    page_estimate: int | None = None
    if mime == "application/pdf":
        page_estimate = estimate_pdf_pages(content)
        if page_estimate > settings.upload_max_pages:
            raise UploadPageCapExceeded(
                f"Szacowana liczba stron PDF ({page_estimate}, heurystyka /Type /Page) "
                f"przekracza limit {settings.upload_max_pages}."
            )

    scanner = av_scanner or NoAvScanner()
    av_status = scanner.scan(content)
    av_note = getattr(scanner, "note", "")
    text, text_status = _extract_text(content, mime, settings.upload_max_chars)

    file_id = f"file:{uuid.uuid4().hex[:12]}"
    digest = hashlib.sha256(content).hexdigest()
    quarantine_key = f"quarantine/{analysis_id or 'unattached'}/{file_id}/{filename}"
    artifact_uri = store_bytes(quarantine_key, content, mime)

    return UploadRecord(
        file_id=file_id,
        filename=filename,
        declared_type=mime,
        size_bytes=len(content),
        sha256=digest,
        artifact_uri=artifact_uri,
        quarantine_key=quarantine_key,
        av_status=av_status,
        av_note=av_note,
        text=text,
        text_status=text_status,
        page_count_estimate=page_estimate,
        analysis_id=analysis_id,
        purpose=purpose,
    )


@dataclass
class UploadStore:
    """Process-local store of sandboxed uploads keyed by ``file_id``.

    Same lifecycle as the analysis store: in-memory, shared by both surfaces
    when MCP + API run in one process. The MCP ``document_ingest`` /
    ``planning_parse_document(file_id=...)`` paths read from here.
    """

    _records: dict[str, UploadRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, record: UploadRecord) -> None:
        with self._lock:
            self._records[record.file_id] = record

    def get(self, file_id: str) -> UploadRecord | None:
        with self._lock:
            return self._records.get(file_id)

    def attach(self, file_id: str, analysis_id: str | None, purpose: str) -> UploadRecord | None:
        """Re-key an upload to an analysis + purpose (document_ingest attach step)."""
        from dataclasses import replace

        with self._lock:
            record = self._records.get(file_id)
            if record is None:
                return None
            updated = replace(record, analysis_id=analysis_id, purpose=purpose)
            self._records[file_id] = updated
            return updated

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


#: Module-level default store (process-local; mirrors plot_agent DEFAULT_STORE).
DEFAULT_UPLOAD_STORE = UploadStore()
