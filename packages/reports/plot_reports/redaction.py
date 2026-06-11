"""PII redaction for SHARED report exports (Phase 14B; F-0483/0503, NFR-SEC).

Reports shared beyond the working architect (``audience != architect`` —
investor / lawyer / bank variants are the "share" channel today) get a redaction
pass over the unified :class:`~plot_reports.model.ReportModel` BEFORE rendering:
any value stored under a parcel-OWNER field name is replaced by
:data:`REDACTION_PLACEHOLDER` and the redacted paths are reported (auditable,
no silent edits).

HONEST SCOPE NOTE: the system stores NO owner/PII data today — no pipeline
writes any of the :data:`PII_FIELD_NAMES` keys (EGiB owner data is deliberately
out of the connector scope). This hook exists so that IF such a field ever
appears in a report model (new connector, manual annotation), shared exports
strip it by construction; the test exercises it with a synthetic field. Full
PII classification/retention (F-0482/0484) is a deployment/data-governance
concern documented in the README, not faked here.
"""

from __future__ import annotations

from typing import Any

from plot_reports.model import ReportModel

#: Field names (lower-cased) whose VALUES are PII in the parcel-owner sense.
PII_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "owner",
        "owners",
        "owner_name",
        "owner_names",
        "wlasciciel",
        "właściciel",
        "wlasciciele",
        "właściciele",
        "owner_address",
        "adres_wlasciciela",
    }
)

REDACTION_PLACEHOLDER = "[zredagowano: dane właścicielskie, F-0483]"


def _redact(node: Any, path: str, redacted_paths: list[str]) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key).lower() in PII_FIELD_NAMES and value is not None:
                out[key] = REDACTION_PLACEHOLDER
                redacted_paths.append(child_path)
            else:
                out[key] = _redact(value, child_path, redacted_paths)
        return out
    if isinstance(node, list):
        return [
            _redact(item, f"{path}[{i}]", redacted_paths) for i, item in enumerate(node)
        ]
    return node


def redact_model_for_sharing(model: ReportModel) -> tuple[ReportModel, list[str]]:
    """Return ``(redacted_model, redacted_paths)`` for a shared export.

    Walks the FULL model dump generically (every dict/list block), so a PII
    field can never ride out via a free-form block (``planning_summary``,
    ``sources``, ``brief``, ``title_block``...). The numbers/sections are
    untouched — redaction only replaces values under PII field names.
    """
    redacted_paths: list[str] = []
    dumped = model.model_dump(mode="json")
    cleaned = _redact(dumped, "", redacted_paths)
    if not redacted_paths:
        return model, []
    return ReportModel.model_validate(cleaned), redacted_paths
