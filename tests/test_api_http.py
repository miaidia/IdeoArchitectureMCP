"""HTTP API endpoint tests (Phase 14B; §27, F-0463) — zero network.

Every §27 endpoint happy path, OpenAPI schema served, /metrics scrapes,
/healthz liveness. Handlers must stay THIN — these tests assert the DOMAIN
content matches what the shared use-cases return (the parity test in
``test_api_parity.py`` closes the MCP loop).
"""

from __future__ import annotations

from tests.api_helpers import (  # noqa: F401 (api fixture)
    ANALYST_KEY,
    READ_KEY,
    api,
    auth,
    create_analysis,
)


def test_healthz_unauthenticated(api) -> None:
    response = api.client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["auth_configured"] is True
    assert body["broker"]["kind"] == "in-process"


def test_openapi_schema_served(api) -> None:
    response = api.client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for expected in (
        "/v1/parcels/resolve",
        "/v1/analyses",
        "/v1/analyses/{analysis_id}/status",
        "/v1/analyses/{analysis_id}/report",
        "/v1/documents/ingest",
        "/v1/portfolio/analyze",
        "/v1/monitoring",
        "/v1/rulesets",
        "/v1/overrides",
    ):
        assert expected in paths, f"missing {expected} in OpenAPI"


def test_metrics_endpoint_scrapes(api) -> None:
    api.client.get("/healthz")  # generate at least one observation
    response = api.client.get("/metrics")
    assert response.status_code == 200
    assert "plot_api_requests_total" in response.text


def test_parcels_resolve(api) -> None:
    response = api.client.post(
        "/v1/parcels/resolve",
        json={"parcel_id": "141201_1.0001.1867/2"},
        headers=auth(READ_KEY),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["geometry_wkt"]  # mock ULDK fixture parcel
    assert body["teryt"] == "141201_1.0001.1867/2"


def test_analyses_create_and_read_flow(api) -> None:
    analysis_id = create_analysis(api)

    result = api.client.get(f"/v1/analyses/{analysis_id}", headers=auth(READ_KEY))
    assert result.status_code == 200
    body = result.json()
    assert body["analysis_id"] == analysis_id
    assert body["decision"]
    assert body["buildable_envelope"]["area_m2"] > 0

    status = api.client.get(f"/v1/analyses/{analysis_id}/status", headers=auth(READ_KEY))
    assert status.status_code == 200
    assert status.json()["progress"] == 1.0

    risks = api.client.get(f"/v1/analyses/{analysis_id}/risks", headers=auth(READ_KEY))
    assert risks.status_code == 200
    assert "risks" in risks.json() and "unknowns" in risks.json()

    unknowns = api.client.get(
        f"/v1/analyses/{analysis_id}/unknowns", headers=auth(READ_KEY)
    )
    assert unknowns.status_code == 200

    evidence = api.client.get(
        f"/v1/analyses/{analysis_id}/evidence", headers=auth(READ_KEY)
    )
    assert evidence.status_code == 200
    assert evidence.json()["evidence_count"] > 0
    assert evidence.json()["evidence_pack_uri"]

    envelope = api.client.get(
        f"/v1/analyses/{analysis_id}/buildable-envelope", headers=auth(READ_KEY)
    )
    assert envelope.status_code == 200
    roles = {f["properties"]["role"] for f in envelope.json()["features"]}
    assert "buildable_envelope" in roles and "parcel" in roles


def test_analyses_report_md_and_export_geojson(api) -> None:
    analysis_id = create_analysis(api)

    report = api.client.get(
        f"/v1/analyses/{analysis_id}/report",
        params={"format": "md", "audience": "architect"},
        headers=auth(READ_KEY),
    )
    assert report.status_code == 200
    assert report.json()["status"] == "rendered"
    assert "# " in report.json()["content"]

    # §27 POST form returns the same rendering
    posted = api.client.post(
        f"/v1/analyses/{analysis_id}/reports",
        json={"format": "md", "audience": "architect"},
        headers=auth(READ_KEY),
    )
    assert posted.status_code == 200
    assert posted.json()["content"] == report.json()["content"]

    export = api.client.get(
        f"/v1/analyses/{analysis_id}/export",
        params={"format": "geojson"},
        headers=auth(READ_KEY),
    )
    assert export.status_code == 200
    body = export.json()
    assert body["status"] == "exported"
    assert body["mime_type"] == "application/geo+json"

    # the exported artifact is downloadable through /v1/artifacts
    key = f"analysis/{analysis_id}/export/layers.geojson"
    artifact = api.client.get(f"/v1/artifacts/{key}", headers=auth(READ_KEY))
    assert artifact.status_code == 200
    assert b"FeatureCollection" in artifact.content


def test_unknown_analysis_is_404(api) -> None:
    response = api.client.get("/v1/analyses/no-such-id", headers=auth(READ_KEY))
    assert response.status_code == 404


def test_portfolio_and_monitoring_and_cache_warm(api) -> None:
    portfolio = api.client.post(
        "/v1/portfolio/analyze",
        json={"parcels": [{"parcel_id": "141201_1.0001.1867/2"}]},
        headers=auth(ANALYST_KEY),
    )
    assert portfolio.status_code == 202
    assert portfolio.json()["batch_id"].startswith("batch:")

    monitoring = api.client.post(
        "/v1/monitoring",
        json={
            "scope": "municipality",
            "target_id": "141201",
            "purpose": "TEST FIXTURE: obserwacja zmian aktów",
        },
        headers=auth(ANALYST_KEY),
    )
    assert monitoring.status_code == 201
    assert monitoring.json()["audit_logged"] is True

    warm = api.client.post(
        "/v1/cache/warm",
        json={"scope": "parcel", "target_id": "141201_1.0001.1867/2"},
        headers=auth(ANALYST_KEY),
    )
    assert warm.status_code == 202


def test_rulesets_and_sources_health(api) -> None:
    rulesets = api.client.get("/v1/rulesets", headers=auth(READ_KEY))
    assert rulesets.status_code == 200
    body = rulesets.json()
    assert body["rule_count"] > 0
    version = body["ruleset_version"]

    one = api.client.get(f"/v1/rulesets/{version}", headers=auth(READ_KEY))
    assert one.status_code == 200
    assert one.json()["loaded_matches_requested"] is True

    health = api.client.get("/v1/sources/health", headers=auth(READ_KEY))
    assert health.status_code == 200
    assert isinstance(health.json()["connectors"], list)


def test_overrides_admin_write(api) -> None:
    from tests.api_helpers import ADMIN_KEY

    response = api.client.post(
        "/v1/overrides",
        json={
            "analysis_id": "adhoc",
            "target_type": "rule",
            "target_id": "PL-WT-13",
            "reason": "TEST FIXTURE: ekspercka korekta",
            "after": {"status": "pass", "confidence": 0.9},
        },
        headers=auth(ADMIN_KEY),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["audit_logged"] is True
    assert body["override_id"]


def test_writes_are_audited_with_key_id(api) -> None:
    create_analysis(api)
    entries = api.audit.entries("analysis_created")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["tenant_id"] == "tenant-a"
    # the audit carries the DERIVED key id, never the key value (NFR-SEC-006)
    assert entry["key_id"] != ANALYST_KEY
    assert ANALYST_KEY not in str(entry)
