"""Upload fuzzing through the HTTP layer (Phase 16; F-0556).

The Phase 14 sandbox tests cover the curated rejection cases; the fuzz gap is
adversarial JUNK: random bytes, malformed/truncated base64, truncated or
page-bombed PDFs, absurd filenames, wrong declared types. Contract under fuzz:
the API answers with a CLEAN 4xx (or, for well-formed-but-empty content, a 2xx
with an honest sandbox record) — NEVER a 500, never a crash.

Deterministic "random": a seeded ``random.Random`` so failures reproduce.
"""

from __future__ import annotations

import base64
import random
import string

import pytest
from tests.api_helpers import ANALYST_KEY, auth, build_api

SEED = 20260612
N_RANDOM_CASES = 25


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    harness = build_api(monkeypatch)
    yield harness
    from plot_mcp_server import usecases

    usecases.set_connectors(None)
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()


def _ingest(api, *, filename: str, content_b64: str, content_type: str = "application/pdf"):
    return api.client.post(
        "/v1/documents/ingest",
        json={
            "filename": filename,
            "content_base64": content_b64,
            "content_type": content_type,
            "purpose": "fuzz test",
        },
        headers=auth(ANALYST_KEY),
    )


def _assert_never_500(response, case: str) -> None:
    assert response.status_code < 500, (
        f"{case}: upload fuzz produced a {response.status_code} — uploads must "
        f"fail CLEAN (4xx), body: {response.text[:300]}"
    )


def test_random_binary_garbage_never_500(api) -> None:
    rng = random.Random(SEED)
    for i in range(N_RANDOM_CASES):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 4096)))
        declared = rng.choice(["application/pdf", "text/plain", "image/png", "application/x-evil"])
        name = "".join(rng.choice(string.printable[:-6]) for _ in range(rng.randrange(1, 60)))
        response = _ingest(
            api,
            filename=name or "x",
            content_b64=base64.b64encode(blob).decode(),
            content_type=declared,
        )
        _assert_never_500(response, f"random#{i} type={declared!r}")
        # 201 is acceptable ONLY for the allowlisted types with sane names —
        # anything else must have been rejected with a 4xx.
        if response.status_code == 201:
            assert declared in ("application/pdf", "text/plain")


def test_malformed_base64_is_clean_400(api) -> None:
    rng = random.Random(SEED + 1)
    cases = [
        "not base64 at all!!!",
        "AAA",  # bad padding
        "////\x00////",
        base64.b64encode(b"x" * 64).decode()[:-3] + "$!",
        "".join(rng.choice(string.printable) for _ in range(200)),
    ]
    for i, junk in enumerate(cases):
        response = _ingest(api, filename="doc.pdf", content_b64=junk)
        _assert_never_500(response, f"b64#{i}")
        assert response.status_code in (400, 413, 415, 422), response.text


def test_truncated_pdf_never_500(api) -> None:
    real_header = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    for cut in (1, 4, 8, len(real_header) - 1):
        response = _ingest(
            api,
            filename="truncated.pdf",
            content_b64=base64.b64encode(real_header[:cut]).decode(),
        )
        _assert_never_500(response, f"truncated@{cut}")


def test_page_bomb_pdf_rejected_413(api) -> None:
    bomb = b"%PDF-1.7\n" + b"/Type /Page\n" * 5000
    response = _ingest(api, filename="bomb.pdf", content_b64=base64.b64encode(bomb).decode())
    _assert_never_500(response, "page-bomb")
    assert response.status_code == 413


def test_oversize_body_rejected_before_decode(api) -> None:
    from plot_shared import get_settings

    too_big = "A" * (int(get_settings().upload_max_bytes * 1.4) + 2048)
    response = _ingest(api, filename="big.pdf", content_b64=too_big)
    _assert_never_500(response, "oversize")
    assert response.status_code == 413


def test_hostile_filenames_rejected_400(api) -> None:
    payload = base64.b64encode(b"hello world").decode()
    for name in (
        "../../etc/passwd",
        "..\\..\\boot.ini",
        "doc\x00.pdf",
        "/absolute/path.pdf",
        "a" * 600 + ".pdf",
    ):
        response = _ingest(api, filename=name, content_b64=payload, content_type="text/plain")
        _assert_never_500(response, f"filename={name[:40]!r}")
        assert response.status_code in (400, 422), f"{name[:40]!r}: {response.status_code}"


def test_wrong_declared_type_rejected_415(api) -> None:
    payload = base64.b64encode(b"GIF89a fuzz").decode()
    response = _ingest(api, filename="img.gif", content_b64=payload, content_type="image/gif")
    _assert_never_500(response, "type-allowlist")
    assert response.status_code == 415
