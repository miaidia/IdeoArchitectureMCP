"""Upload sandbox unit tests (Phase 14B; F-0490–0493, §16) — plot_security +
the document_ingest / planning_parse_document wiring in the shared use-cases."""

from __future__ import annotations

import pytest
from plot_security import (
    DEFAULT_UPLOAD_STORE,
    NoAvScanner,
    UploadFilenameRejected,
    UploadPageCapExceeded,
    UploadTooLarge,
    UploadTypeForbidden,
    estimate_pdf_pages,
    ingest_upload,
    safe_filename,
)
from plot_shared import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _mem_store():
    stored: dict[str, bytes] = {}

    def put(key: str, data: bytes, content_type: str) -> str:
        stored[key] = data
        return f"mem://{key}"

    return stored, put


# --------------------------------------------------------------------------- #
# filename traversal guard (F-0490)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name",
    ["../up.txt", "a/b.txt", "c\\d.txt", "x\x00.txt", "", ".dotfile.txt", "a..b/../c.txt"],
)
def test_unsafe_filenames_rejected(name: str) -> None:
    with pytest.raises(UploadFilenameRejected):
        safe_filename(name)


def test_safe_filename_passes() -> None:
    assert safe_filename("uchwala MPZP_2024-3.txt") == "uchwala MPZP_2024-3.txt"


# --------------------------------------------------------------------------- #
# size / type / page caps (F-0493)
# --------------------------------------------------------------------------- #
def test_size_cap_enforced() -> None:
    _, put = _mem_store()
    with pytest.raises(UploadTooLarge):
        ingest_upload(
            "big.txt",
            b"x" * 100,
            store_bytes=put,
            settings=_settings(upload_max_bytes=10),
        )


def test_type_allowlist_enforced() -> None:
    _, put = _mem_store()
    with pytest.raises(UploadTypeForbidden):
        ingest_upload("run.exe", b"MZ", store_bytes=put, settings=_settings())
    with pytest.raises(UploadTypeForbidden):
        # declared type and extension must AGREE
        ingest_upload(
            "doc.txt", b"x", declared_type="text/html", store_bytes=put, settings=_settings()
        )


def test_pdf_page_cap_heuristic() -> None:
    pdf = b"%PDF-1.4\n" + b"<< /Type /Page >>\n" * 12
    assert estimate_pdf_pages(pdf) == 12
    _, put = _mem_store()
    with pytest.raises(UploadPageCapExceeded):
        ingest_upload(
            "duzy.pdf",
            pdf,
            store_bytes=put,
            settings=_settings(upload_max_pages=10),
        )


# --------------------------------------------------------------------------- #
# quarantine + AV honesty + text extraction
# --------------------------------------------------------------------------- #
def test_text_upload_quarantined_with_honest_av_status() -> None:
    stored, put = _mem_store()
    record = ingest_upload(
        "uchwala.txt",
        "Maksymalna wysokość: 12 m".encode(),
        purpose="test",
        store_bytes=put,
        settings=_settings(),
    )
    assert record.av_status == "not_scanned"  # F-0492: no AV engine — no fake "clean"
    assert NoAvScanner.note  # the honest note exists on the hook
    assert record.quarantine_key.startswith("quarantine/")
    assert record.quarantine_key in stored
    assert record.text is not None and "12 m" in record.text
    assert record.text_status == "extracted"


def test_text_char_cap_truncates() -> None:
    _, put = _mem_store()
    record = ingest_upload(
        "long.txt",
        b"a" * 50,
        store_bytes=put,
        settings=_settings(upload_max_chars=10),
    )
    assert record.text_status == "truncated"
    assert len(record.text or "") == 10


def test_pdf_has_no_fake_text_extraction() -> None:
    _, put = _mem_store()
    record = ingest_upload(
        "doc.pdf",
        b"%PDF-1.4 << /Type /Page >>",
        store_bytes=put,
        settings=_settings(),
    )
    assert record.text is None
    assert record.text_status == "extraction_unavailable"  # honest gap (§21)


# --------------------------------------------------------------------------- #
# shared use-case wiring (document_upload / document_ingest / parse by file_id)
# --------------------------------------------------------------------------- #
def test_document_upload_ingest_attach_and_parse_flow() -> None:
    from plot_agent.orchestrator import DEFAULT_ORCHESTRATOR_AUDIT
    from plot_mcp_server import usecases

    before_audit = len(DEFAULT_ORCHESTRATOR_AUDIT.entries())
    out = usecases.document_upload(
        "mpzp.txt",
        "Maksymalna wysokość zabudowy: 15 m. Intensywność zabudowy: 1,2.".encode(),
        purpose="TEST FIXTURE: upload",
    )
    assert out["ingested"] is True and out["status"] == "quarantined"
    file_id = out["file_id"]
    assert DEFAULT_UPLOAD_STORE.get(file_id) is not None
    # write audited (NFR-SEC-010)
    assert len(DEFAULT_ORCHESTRATOR_AUDIT.entries()) == before_audit + 1

    attached = usecases.document_ingest("an-1", file_id, "TEST FIXTURE: attach")
    assert attached["ingested"] is True and attached["status"] == "attached"
    assert attached["analysis_id"] == "an-1"

    parsed = usecases.planning_parse_document(file_id, None, None)
    assert parsed["source_type"] == "file"
    names = {i["name"] for i in parsed["indicators"]}
    assert "max_height_m" in names


def test_document_ingest_unknown_file_is_honest_miss() -> None:
    from plot_mcp_server import usecases

    out = usecases.document_ingest(None, "file:does-not-exist", "purpose")
    assert out["ingested"] is False
    assert out["status"] == "file_not_found"


def test_parse_pdf_upload_reports_extraction_unavailable() -> None:
    from plot_mcp_server import usecases

    out = usecases.document_upload(
        "skan.pdf", b"%PDF-1.4 << /Type /Page >>", purpose="TEST FIXTURE"
    )
    parsed = usecases.planning_parse_document(out["file_id"], None, None)
    assert parsed["status"] == "text_extraction_unavailable"
    assert parsed["indicators"] == []
