"""Regenerate JSON Schema files from the Pydantic domain models.

Run: ``uv run python schemas/generate.py``

Schemas are committed as the stable contract and regenerated from ``plot_domain``
via Pydantic v2 ``model_json_schema()`` (Phase 0.1: derive outputSchema from the
Pydantic model) so the files stay in sync with the models. The MCP tools schema
is hand-built from the §10.3 tool list (those are protocol names, not models).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Pydantic v2 model_json_schema() — draft 2020-12 by default (Phase 0.1).
from plot_domain import (
    AnalysisInput,
    AnalysisResult,
    Constraint,
    RiskItem,
    SourceRecord,
)

SCHEMA_DIR = Path(__file__).parent
DIALECT = "https://json-schema.org/draft/2020-12/schema"

# The 20 public MCP tools from base_assumptions §10.3 (the plan calls these the
# "21 tool names"; §10.3 enumerates 20 — see report note). diagnostics_run is the
# last of the public set; dev_reload is a dev-only tool added in Phase 2.
MCP_TOOLS: dict[str, str] = {
    "parcel_resolve": "Resolve parcel from id, address, point, geometry or uploaded file.",
    "parcel_analyze": "Run quick/full/design/portfolio analysis.",
    "analysis_get_status": "Return status, progress and partial results.",
    "analysis_get_result": "Return structured result for an analysis run.",
    "planning_fetch": "Fetch planning context and planning acts for parcel/area.",
    "planning_parse_document": "Parse user-supplied planning document with evidence.",
    "constraints_compute": "Compute constraints and buildable envelope.",
    "capacity_generate_scenarios": "Generate building capacity scenarios.",
    "risks_list": "Return red flags, risk register and unknowns.",
    "sources_collect": "Collect source records and evidence pack.",
    "report_generate": "Generate report artifact in selected format.",
    "export_layers": "Export GIS/CAD layers.",
    "portfolio_analyze": "Analyze many parcels.",
    "monitoring_create": "Create monitoring profile for changes.",
    "ruleset_explain": "Explain which rules were applied.",
    "source_healthcheck": "Check external source availability.",
    "cache_warm": "Preload source/cache data for municipality or parcel.",
    "document_ingest": "Ingest user documents and attach to analysis.",
    "manual_override": "Apply expert override with audit trail.",
    "diagnostics_run": "Run diagnostics for debugging and QA.",
}


def _with_meta(schema: dict[str, Any], schema_id: str, title: str) -> dict[str, Any]:
    """Prepend draft-2020-12 ``$schema`` / ``$id`` / ``title`` metadata."""
    out: dict[str, Any] = {
        "$schema": DIALECT,
        "$id": schema_id,
        "title": title,
    }
    out.update(schema)
    return out


def _write(name: str, schema: dict[str, Any]) -> None:
    path = SCHEMA_DIR / name
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(SCHEMA_DIR.parent)}")


def build_mcp_tools_schema() -> dict[str, Any]:
    """Hand-built schema listing the §10.3 public MCP tool names + descriptions."""
    return _with_meta(
        {
            "type": "object",
            "description": "Public MCP tool surface (base_assumptions §10.3).",
            "properties": {
                "tools": {
                    "type": "object",
                    "properties": {
                        name: {
                            "type": "object",
                            "properties": {
                                "description": {"const": desc},
                            },
                            "required": ["description"],
                        }
                        for name, desc in MCP_TOOLS.items()
                    },
                    "required": list(MCP_TOOLS),
                    "additionalProperties": False,
                }
            },
            "required": ["tools"],
        },
        "https://plot-analyzer/schemas/mcp-tools.schema.json",
        "MCP Tools",
    )


def main() -> None:
    _write(
        "analysis-input.schema.json",
        _with_meta(
            AnalysisInput.model_json_schema(),
            "https://plot-analyzer/schemas/analysis-input.schema.json",
            "Analysis Input",
        ),
    )
    _write(
        "analysis-result.schema.json",
        _with_meta(
            AnalysisResult.model_json_schema(),
            "https://plot-analyzer/schemas/analysis-result.schema.json",
            "Analysis Result",
        ),
    )
    _write(
        "risk-register.schema.json",
        _with_meta(
            RiskItem.model_json_schema(),
            "https://plot-analyzer/schemas/risk-register.schema.json",
            "Risk Register Item",
        ),
    )
    _write(
        "constraint.schema.json",
        _with_meta(
            Constraint.model_json_schema(),
            "https://plot-analyzer/schemas/constraint.schema.json",
            "Constraint",
        ),
    )
    _write(
        "source-record.schema.json",
        _with_meta(
            SourceRecord.model_json_schema(),
            "https://plot-analyzer/schemas/source-record.schema.json",
            "Source Record",
        ),
    )
    _write("mcp-tools.schema.json", build_mcp_tools_schema())


if __name__ == "__main__":
    main()
