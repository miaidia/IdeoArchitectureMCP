"""plot_security — upload sandbox + file-safety policy (Phase 14B; §16).

Implements the user-document upload sandbox (F-0491–0493): filename traversal
guard, size/type/page caps, capped text extraction, quarantine storage via an
injected callback, and the honest AV-scan hook (``not_scanned`` — no AV engine
ships here, F-0492). SSRF/egress enforcement lives in
``plot_connectors.base.egress`` (F-0488/0489); prompt-injection screening lives
in ``plot_planning.parser.security`` (NFR-SEC-002/003) — this package does not
duplicate them.
"""

from plot_security.uploads import (
    ALLOWED_UPLOAD_TYPES,
    DEFAULT_UPLOAD_STORE,
    AvScanner,
    NoAvScanner,
    UploadFilenameRejected,
    UploadPageCapExceeded,
    UploadRecord,
    UploadRejected,
    UploadStore,
    UploadTooLarge,
    UploadTypeForbidden,
    estimate_pdf_pages,
    ingest_upload,
    safe_filename,
)

__version__ = "0.1.0"

__all__ = [
    "ALLOWED_UPLOAD_TYPES",
    "DEFAULT_UPLOAD_STORE",
    "AvScanner",
    "NoAvScanner",
    "UploadFilenameRejected",
    "UploadPageCapExceeded",
    "UploadRecord",
    "UploadRejected",
    "UploadStore",
    "UploadTooLarge",
    "UploadTypeForbidden",
    "estimate_pdf_pages",
    "ingest_upload",
    "safe_filename",
]
