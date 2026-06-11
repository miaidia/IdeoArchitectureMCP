"""Shared helpers for the Phase 14B HTTP API tests (zero network, no subprocess).

Builds the FastAPI app with TEST FIXTURE keys (synthetic values, clearly not
real secrets) + the Phase 7 mock connector bundle injected into the SHARED
use-case module, so both surfaces run zero-network.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from tests.mocks import mock_connectors

#: TEST FIXTURE keys (synthetic; role + tenant matrix for the auth tests).
ADMIN_KEY = "test-admin-key-a"
ANALYST_KEY = "test-analyst-key-a"
READ_KEY = "test-read-key-a"
TENANT_B_KEY = "test-analyst-key-b"

API_KEYS_ENV = ",".join(
    [
        f"{ADMIN_KEY}:admin:tenant-a",
        f"{ANALYST_KEY}:analyst:tenant-a",
        f"{READ_KEY}:read:tenant-a",
        f"{TENANT_B_KEY}:analyst:tenant-b",
    ]
)


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@dataclass
class ApiHarness:
    client: TestClient
    audit: object
    tenants: object


def build_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connectors: object | None = None,
    extra_env: dict[str, str] | None = None,
) -> ApiHarness:
    """Fresh app + injected mock connectors + isolated audit/tenant stores."""
    monkeypatch.setenv("PLOT_API_KEYS", API_KEYS_ENV)
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    for name, value in (extra_env or {}).items():
        monkeypatch.setenv(name, value)
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()

    from plot_mcp_server import usecases

    usecases.set_connectors(connectors if connectors is not None else mock_connectors())

    from plot_api.app import create_app
    from plot_api.audit import ApiAuditLog
    from plot_api.tenancy import TenantIndex

    audit = ApiAuditLog()
    tenants = TenantIndex()
    app = create_app(cfg.get_settings(), audit=audit, tenants=tenants)
    return ApiHarness(client=TestClient(app), audit=audit, tenants=tenants)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    """Default harness; restores the connector injection + settings cache."""
    harness = build_api(monkeypatch)
    yield harness
    from plot_mcp_server import usecases

    usecases.set_connectors(None)
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()


def create_analysis(harness: ApiHarness, key: str = ANALYST_KEY) -> str:
    response = harness.client.post(
        "/v1/analyses",
        json={"input": {"parcel_id": "141201_1.0001.1867/2"}, "analysis_mode": "quick_screening"},
        headers=auth(key),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["analysis_id"])
