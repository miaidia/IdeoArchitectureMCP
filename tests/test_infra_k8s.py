"""k8s manifest structural validation (Phase 16; v1 plan 13.1.3).

Syntax/structure only — no cluster, no kubectl: the YAML parses, the
Deployment/Service wire to each other and to the Dockerfile's actual contract
(port 8000, non-root uid 10001, /api/healthz probes, env-only secrets), and the
"minimal + honest" guards hold (single replica for the in-memory stores; no
inline secret values; hot reload pinned off).
"""

from __future__ import annotations

from pathlib import Path

import yaml

K8S = Path(__file__).resolve().parents[1] / "infra" / "k8s"


def _load(name: str) -> dict:
    doc = yaml.safe_load((K8S / name).read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{name} is not a mapping"
    return doc


def test_deployment_matches_the_dockerfile_contract() -> None:
    doc = _load("deployment.yaml")
    assert (doc["apiVersion"], doc["kind"]) == ("apps/v1", "Deployment")
    spec = doc["spec"]
    # in-memory stores are per-process → one replica (RUNBOOK honesty).
    assert spec["replicas"] == 1
    pod = spec["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] == 10001  # Dockerfile `plot` user

    (container,) = pod["containers"]
    assert container["ports"][0]["containerPort"] == 8000  # Dockerfile EXPOSE
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"]["path"] == "/api/healthz"
    # Secrets ONLY by reference — never inline values (F-0477).
    assert container["envFrom"][0]["secretRef"]["name"] == "plot-analyzer-secrets"
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    assert env.get("PLOT_DEV_HOT_RELOAD") == "false"
    assert "PLOT_API_KEYS" not in env, "API keys must come from the Secret"
    # Resources declared (no unbounded pod).
    assert container["resources"]["requests"] and container["resources"]["limits"]


def test_deployment_selector_matches_pod_labels() -> None:
    doc = _load("deployment.yaml")
    selector = doc["spec"]["selector"]["matchLabels"]
    labels = doc["spec"]["template"]["metadata"]["labels"]
    assert selector.items() <= labels.items()


def test_service_targets_the_deployment() -> None:
    service = _load("service.yaml")
    deployment = _load("deployment.yaml")
    assert (service["apiVersion"], service["kind"]) == ("v1", "Service")
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert service["spec"]["selector"].items() <= pod_labels.items()
    (port,) = service["spec"]["ports"]
    assert port["targetPort"] == "http"  # the deployment's named port


def test_no_secret_material_committed() -> None:
    """No Secret manifest with data, no key-looking literals in infra/k8s."""
    for path in K8S.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "Secret":
                assert not doc.get("data") and not doc.get("stringData"), (
                    f"{path.name}: committed Secret values are forbidden (F-0477)"
                )
        assert "PLOT_API_KEYS=" not in text or "kubectl create secret" in text
