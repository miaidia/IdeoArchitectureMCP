"""Pydantic v2 domain models for all base_assumptions §11.1 entities.

Geometry is stored at the domain level as a GeoJSON dict or a WKT string (no
shapely dependency yet — that arrives in Phase 5). Analytical geometry uses
EPSG:2180; the input CRS is kept as metadata (§26.3). Every field carries a
``description`` so the derived JSON Schema / MCP ``outputSchema`` is high quality
(Phase 0.1).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from plot_domain.enums import (
    AnalysisMode,
    AnalysisStatus,
    ConfidenceLevel,
    Decision,
    Freshness,
    GeometryPrecision,
    InvestmentType,
    LegalStatus,
    PlanningActType,
    RiskStatus,
    RiskType,
    Severity,
    SourceType,
)

# A GeoJSON geometry is just a JSON object at the domain level (no shapely yet).
GeoJSON = dict[str, Any]


class _Base(BaseModel):
    """Common config for domain models."""

    model_config = ConfigDict(use_enum_values=False)


# --------------------------------------------------------------------------- #
# Source / evidence
# --------------------------------------------------------------------------- #
class SourceRecord(_Base):
    """A data source record — mirrors base_assumptions §5 exactly."""

    source_id: str = Field(description="Stable identifier of the source record.")
    source_type: SourceType = Field(
        description="official_register | local_sip | user_document | commercial | auxiliary."
    )
    publisher: str = Field(description="Publishing authority or organisation.")
    url_or_origin: str = Field(description="URL or origin descriptor of the source.")
    retrieved_at: datetime = Field(description="When the source was retrieved.")
    valid_from: date | None = Field(default=None, description="Start of validity, if known.")
    valid_to: date | None = Field(default=None, description="End of validity, if known.")
    license: str = Field(description="License string, or 'unknown'.")
    legal_status: LegalStatus = Field(
        description="binding | informative | auxiliary | unknown."
    )
    geometry_precision: GeometryPrecision = Field(
        description="survey | cadastral | topographic | raster_derived | approximate | unknown."
    )
    freshness: Freshness = Field(description="current | stale | archived | unknown.")
    confidence: float = Field(ge=0.0, le=1.0, description="Source confidence, 0.0-1.0.")
    notes: str = Field(default="", description="Free-text notes about the source.")


class EvidenceItem(_Base):
    """A single piece of evidence backing a claim (§11.1, §26.1 evidence_items)."""

    id: str = Field(description="Evidence item identifier.")
    analysis_id: str = Field(description="Owning analysis run id.")
    source_id: str = Field(description="Source record this evidence derives from.")
    subject_type: str = Field(description="Entity type the evidence is about (e.g. 'parcel').")
    subject_id: str = Field(description="Identifier of the subject entity.")
    claim: str = Field(description="The claim this evidence supports.")
    value_json: dict[str, Any] = Field(
        default_factory=dict, description="Structured value backing the claim."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this evidence, 0.0-1.0.")
    geometry: GeoJSON | None = Field(
        default=None, description="Optional GeoJSON geometry (EPSG:2180) for the evidence."
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp.")


# --------------------------------------------------------------------------- #
# Parcel / area / administrative context
# --------------------------------------------------------------------------- #
class AdministrativeContext(_Base):
    """Administrative context for a parcel (§11.1)."""

    teryt: str = Field(description="TERYT code of the administrative unit.")
    commune: str | None = Field(default=None, description="Commune (gmina) name.")
    county: str | None = Field(default=None, description="County (powiat) name.")
    voivodeship: str | None = Field(default=None, description="Voivodeship (województwo) name.")
    district: str | None = Field(default=None, description="Cadastral district / obręb.")
    municipality_id: str | None = Field(
        default=None, description="Municipality id used to key planning acts."
    )


class Parcel(_Base):
    """A cadastral parcel (§11.1, §26.1 parcels)."""

    id: str = Field(description="Internal parcel id.")
    external_id: str | None = Field(default=None, description="External cadastral id.")
    teryt: str | None = Field(default=None, description="TERYT code.")
    obr: str | None = Field(default=None, description="Cadastral district (obręb).")
    number: str | None = Field(default=None, description="Parcel number.")
    geometry: GeoJSON | None = Field(
        default=None, description="Parcel geometry in analytical CRS EPSG:2180 (GeoJSON)."
    )
    geometry_wkt: str | None = Field(
        default=None, description="Optional WKT representation of the geometry."
    )
    input_crs: str | None = Field(
        default=None, description="Original input CRS, kept as metadata (§26.3)."
    )
    area_m2: float | None = Field(default=None, ge=0.0, description="Parcel area in m^2.")
    source_id: str | None = Field(default=None, description="Source record id for this parcel.")
    snapshot_id: str | None = Field(default=None, description="Source snapshot id.")
    administrative_context: AdministrativeContext | None = Field(
        default=None, description="Administrative context for the parcel."
    )


class InvestmentArea(_Base):
    """One or more parcels forming an investment area (§11.1, §26.1 investment_areas)."""

    id: str = Field(description="Investment area id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    parcel_ids: list[str] = Field(
        default_factory=list, description="Parcel ids composing the area."
    )
    geometry: GeoJSON | None = Field(
        default=None, description="Merged geometry in EPSG:2180 (GeoJSON)."
    )
    area_m2: float | None = Field(default=None, ge=0.0, description="Total area in m^2.")
    created_at: datetime | None = Field(default=None, description="Creation timestamp.")


# --------------------------------------------------------------------------- #
# Analysis run
# --------------------------------------------------------------------------- #
class AnalysisRun(_Base):
    """An analysis run (§11.1, §26.1 analysis_runs; reproducibility fields §9.4)."""

    id: str = Field(description="Analysis run id (uuid).")
    input_hash: str = Field(description="Hash of the input for idempotency (NFR-REL-002/003).")
    status: AnalysisStatus = Field(description="complete | partial | failed | manual_review_required.")
    mode: AnalysisMode = Field(description="Analysis mode (§10.6).")
    ruleset_version: str = Field(description="Ruleset version applied to this run.")
    source_snapshot_id: str | None = Field(
        default=None, description="Source snapshot id for reproducibility (§9.4)."
    )
    tenant_id: str | None = Field(default=None, description="Tenant id for isolation (NFR-SEC-008).")
    created_at: datetime | None = Field(default=None, description="Creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp.")


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
class PlanningAct(_Base):
    """A planning act: MPZP / POG / WZ / ULICP / ZPI / draft (§11.1, §26.1 planning_acts)."""

    id: str = Field(description="Planning act id.")
    municipality_id: str = Field(description="Municipality id the act belongs to.")
    act_type: PlanningActType = Field(description="MPZP | POG | WZ | ULICP | ZPI | draft_plan.")
    title: str = Field(description="Act title.")
    status: str = Field(description="Lifecycle status of the act (e.g. in_force, draft).")
    valid_from: date | None = Field(default=None, description="Validity start date.")
    valid_to: date | None = Field(default=None, description="Validity end date.")
    source_id: str | None = Field(default=None, description="Source record id.")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional act metadata (metadata_json)."
    )


class PlanningZone(_Base):
    """A planning unit / zone within an act (§11.1, §26.1 planning_zones)."""

    id: str = Field(description="Planning zone id.")
    act_id: str = Field(description="Owning planning act id.")
    symbol: str = Field(description="Zone symbol (e.g. MN, U).")
    geometry: GeoJSON | None = Field(
        default=None, description="Zone geometry in EPSG:2180 (GeoJSON)."
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Zone attributes (attributes_json)."
    )


class PlanningIndicator(_Base):
    """A planning indicator extracted from an act (§11.1)."""

    id: str = Field(description="Indicator id.")
    zone_id: str | None = Field(default=None, description="Planning zone id, if zone-scoped.")
    name: str = Field(description="Indicator name (e.g. max_building_height_m).")
    value: Any = Field(default=None, description="Indicator value (numeric, string, or range).")
    unit: str | None = Field(default=None, description="Unit of the value, if any.")
    source_id: str | None = Field(default=None, description="Source record id.")
    evidence_id: str | None = Field(
        default=None, description="Evidence item id with the source fragment (NFR-AUD-002)."
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Extraction confidence.")


# --------------------------------------------------------------------------- #
# Constraints / no-build / envelope / capacity
# --------------------------------------------------------------------------- #
class Constraint(_Base):
    """A geometric or descriptive constraint — mirrors base_assumptions §11.3 exactly."""

    constraint_id: str = Field(description="Constraint identifier.")
    constraint_type: str = Field(description="Constraint type (free string).")
    source_id: str = Field(description="Source record id.")
    source_legal_status: LegalStatus = Field(
        description="binding | informative | auxiliary | unknown."
    )
    geometry: GeoJSON | None = Field(
        default=None, description="Constraint geometry (GeoJSON) in EPSG:2180, or null."
    )
    applies_to_area_m2: float | None = Field(
        default=None, description="Area the constraint applies to, in m^2."
    )
    applies_to_percent: float | None = Field(
        default=None, description="Percent of the parcel/area the constraint applies to."
    )
    rule_id: str | None = Field(default=None, description="Rule id that produced this constraint.")
    rule_version: str | None = Field(default=None, description="Version of the applied rule.")
    severity: Severity = Field(description="info | low | medium | high | critical.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence, 0.0-1.0.")
    human_summary: str = Field(description="Human-readable summary of the constraint.")
    machine_summary: dict[str, Any] = Field(
        default_factory=dict, description="Machine-readable structured summary."
    )
    mitigation: str | None = Field(default=None, description="Possible mitigation, if any.")


class NoBuildZone(_Base):
    """An excluded (no-build) area derived from constraints (§11.1)."""

    id: str = Field(description="No-build zone id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    reason: str = Field(description="Why this area is excluded.")
    geometry: GeoJSON | None = Field(
        default=None, description="Excluded geometry in EPSG:2180 (GeoJSON)."
    )
    area_m2: float | None = Field(default=None, ge=0.0, description="Excluded area in m^2.")
    source_constraint_id: str | None = Field(
        default=None, description="Constraint that produced this zone (area attribution)."
    )


class BuildableEnvelope(_Base):
    """Resulting buildable area (§11.1, §26.1 buildable_envelopes; §7.5)."""

    id: str = Field(description="Buildable envelope id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    geometry: GeoJSON | None = Field(
        default=None, description="Buildable polygon in EPSG:2180 (GeoJSON)."
    )
    largest_inscribed_rectangle: GeoJSON | None = Field(
        default=None, description="Largest inscribed rectangle (GeoJSON), if computed (Phase 5)."
    )
    area_m2: float | None = Field(default=None, ge=0.0, description="Buildable area in m^2.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Envelope confidence.")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Spatial-op trace and style metadata (metadata_json)."
    )


class CapacityScenario(_Base):
    """A capacity (chłonność) scenario (§11.1, §26.1 capacity_scenarios; §8.5)."""

    id: str = Field(description="Capacity scenario id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    scenario_type: str = Field(
        description="Scenario type (conservative | base | optimistic | max)."
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict, description="Capacity metrics (metrics_json): GFA, PUM, parking, etc."
    )
    risks: list[dict[str, Any]] = Field(
        default_factory=list, description="Scenario-specific risks (risks_json)."
    )
    geometry: GeoJSON | None = Field(
        default=None, description="Scenario footprint geometry in EPSG:2180 (GeoJSON)."
    )
    masterplan_variant_id: str | None = Field(
        default=None,
        description=(
            "Optional MasterplanVariant id this scenario was derived from (Phase 9). "
            "None for the no-drawing envelope+indicator scenarios."
        ),
    )


class StoreyRecord(_Base):
    """One storey of a building (Phase 9 PB-forward placeholder; filled in Phase 15)."""

    level: int = Field(description="Storey level (0 = parter; negative = underground).")
    height_m: float | None = Field(default=None, description="Clear storey height in metres.")
    use: str = Field(description="Storey use (mieszkalny | uslugowy | garaz | techniczny | ...).")
    area_m2: float | None = Field(default=None, ge=0.0, description="Storey floor area in m^2.")


class BuildingRecord(_Base):
    """One building of a masterplan variant (Phase 9 §9.1.3; ROBYG-class deliverable)."""

    id: str = Field(description="Building record id.")
    name: str = Field(description="Building name (e.g. 'Budynek 1').")
    geometry: GeoJSON | None = Field(
        default=None, description="Footprint geometry in EPSG:2180 (GeoJSON)."
    )
    floors_by_segment: list[int] = Field(
        default_factory=list, description="Above-ground floors per building segment/wing."
    )
    uses: list[str] = Field(
        default_factory=list, description="Uses per segment (mieszkalny | uslugowy | ...)."
    )
    stage: int | None = Field(default=None, description="Construction stage (etap realizacji).")
    status: str = Field(
        default="projektowany",
        description="projektowany | istniejacy | w_budowie | zrealizowany | zabytek_do_remontu.",
    )
    underground_floors: int = Field(
        default=0, ge=0, description="Underground (hala garażowa) levels."
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-building capacity metrics (PUM/PUU/mieszkania with basis metadata).",
    )
    storeys: list[StoreyRecord] = Field(
        default_factory=list,
        description="PB-forward storey records (empty until Phase 15; no schema break later).",
    )


class MasterplanVariant(_Base):
    """A multi-building masterplan variant (Phase 9 §9.1.3; extends toward PZT)."""

    id: str = Field(description="Masterplan variant id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    buildings: list[BuildingRecord] = Field(
        default_factory=list, description="Buildings of the variant."
    )
    roads: list[dict[str, Any]] = Field(
        default_factory=list, description="Internal roads (geometry + width + function)."
    )
    parking: list[dict[str, Any]] = Field(
        default_factory=list, description="Parking elements (kind, polygon, spaces)."
    )
    greenery: list[GeoJSON] = Field(
        default_factory=list, description="Greenery (PBC) polygons in EPSG:2180."
    )
    playgrounds: list[GeoJSON] = Field(
        default_factory=list, description="Playground (plac zabaw) polygons in EPSG:2180."
    )
    retention: list[GeoJSON] = Field(
        default_factory=list, description="Retention (retencja) polygons in EPSG:2180."
    )
    totals: dict[str, Any] = Field(
        default_factory=dict,
        description="Variant totals (PUM/PUU/PU/mieszkania/coverage with basis metadata).",
    )
    stage_table: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-stage rows (Liczba mieszkań / PUM / PUU / PU) + SUMA row.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Variant metadata (config, ruleset version, notes)."
    )


# --------------------------------------------------------------------------- #
# Infrastructure / terrain
# --------------------------------------------------------------------------- #
class UtilityNetwork(_Base):
    """A utility network (uzbrojenie) (§11.1)."""

    id: str = Field(description="Utility network id.")
    network_type: str = Field(description="Type (water | sewer | gas | power | telecom | heat).")
    geometry: GeoJSON | None = Field(
        default=None, description="Network geometry in EPSG:2180 (GeoJSON)."
    )
    distance_m: float | None = Field(
        default=None, description="Nearest distance from the parcel, in metres."
    )
    operator: str | None = Field(default=None, description="Network operator (gestor).")
    source_id: str | None = Field(default=None, description="Source record id.")


class RoadAccess(_Base):
    """Road access information (§11.1)."""

    id: str = Field(description="Road access id.")
    has_public_road_access: bool | None = Field(
        default=None, description="Whether direct public road access exists."
    )
    road_class: str | None = Field(default=None, description="Road class.")
    authority: str | None = Field(default=None, description="Road authority (zarządca).")
    frontage_length_m: float | None = Field(
        default=None, description="Frontage length onto the road, in metres."
    )
    access_chain: list[str] = Field(
        default_factory=list, description="Access chain (parcels/servitudes) to a public road."
    )
    geometry: GeoJSON | None = Field(
        default=None, description="Access geometry in EPSG:2180 (GeoJSON)."
    )
    source_id: str | None = Field(default=None, description="Source record id.")


class TerrainModel(_Base):
    """Terrain model (NMT/NMPT) and derivatives (§11.1).

    No raster blobs in the DB — only references to object storage (§26.3).
    """

    id: str = Field(description="Terrain model id.")
    model_type: str = Field(description="Model type (NMT | NMPT | LiDAR).")
    resolution_m: float | None = Field(default=None, description="Raster resolution in metres.")
    storage_uri: str | None = Field(
        default=None, description="Object-storage URI for the raster (never a DB blob, §26.3)."
    )
    slope_summary: dict[str, Any] = Field(
        default_factory=dict, description="Derived slope/aspect/elevation summary."
    )
    source_id: str | None = Field(default=None, description="Source record id.")


# --------------------------------------------------------------------------- #
# Risks / unknowns / recommendations
# --------------------------------------------------------------------------- #
class RiskItem(_Base):
    """A risk item (§11.1, §11.2, §26.1 risk_items)."""

    id: str = Field(description="Risk item id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    risk_type: RiskType = Field(description="Risk category (§11.2).")
    severity: Severity = Field(description="info | low | medium | high | critical.")
    confidence: ConfidenceLevel = Field(description="low | medium | high (§11.2).")
    status: RiskStatus = Field(
        description="detected | suspected | not_detected | unknown | manual_review_required."
    )
    summary: str = Field(description="Human-readable risk summary.")
    mitigation: str | None = Field(default=None, description="Mitigation, if any.")
    source_id: str | None = Field(default=None, description="Source record id.")


class UnknownItem(_Base):
    """An unresolved / undetermined item (§11.1, §26.1 unknown_items)."""

    id: str = Field(description="Unknown item id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    topic: str = Field(description="Topic that could not be determined.")
    severity: Severity = Field(description="Severity of not knowing this.")
    reason: str = Field(description="Why it is unknown (e.g. source_unavailable, no_data).")
    suggested_action: str | None = Field(
        default=None, description="Suggested action to resolve the unknown."
    )


class Recommendation(_Base):
    """A recommendation / next action (§11.1)."""

    id: str = Field(description="Recommendation id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    title: str = Field(description="Short recommendation title.")
    detail: str = Field(description="Recommendation detail / rationale.")
    priority: Severity = Field(
        default=Severity.MEDIUM, description="Priority expressed on the severity scale."
    )
    addressed_to: str | None = Field(
        default=None, description="Specialist/authority the action is addressed to (NFR-DOM-008)."
    )


# --------------------------------------------------------------------------- #
# Artifacts / rulesets / overrides
# --------------------------------------------------------------------------- #
class ReportArtifact(_Base):
    """A report / map / export artifact (§11.1, §26.1 report_artifacts)."""

    id: str = Field(description="Artifact id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    artifact_type: str = Field(description="Artifact type (md | html | pdf | json | gpkg | dxf | png).")
    uri: str = Field(description="Object-storage URI of the artifact.")
    content_hash: str | None = Field(default=None, description="Content hash for reproducibility.")
    created_at: datetime | None = Field(default=None, description="Creation timestamp.")


class Ruleset(_Base):
    """A ruleset version (§11.1, §12)."""

    id: str = Field(description="Ruleset id.")
    version: str = Field(description="Ruleset version string.")
    country: str = Field(default="PL", description="Country the ruleset applies to.")
    valid_from: date | None = Field(default=None, description="Validity start (§12.1, no undated rules).")
    valid_to: date | None = Field(default=None, description="Validity end.")
    source_reference: str | None = Field(
        default=None, description="Legal/technical source reference for the rules."
    )
    categories: list[str] = Field(
        default_factory=list, description="Rule categories covered (§12.3)."
    )


class Override(_Base):
    """An expert override with audit trail (§11.1, §26.1 overrides; NFR-AUD-003)."""

    id: str = Field(description="Override id.")
    analysis_id: str | None = Field(default=None, description="Owning analysis run id.")
    user_id: str = Field(description="Author of the override (NFR-AUD-003).")
    target_type: str = Field(description="Entity type being overridden.")
    target_id: str = Field(description="Identifier of the overridden entity.")
    before_json: dict[str, Any] = Field(
        default_factory=dict, description="Value before the override."
    )
    after_json: dict[str, Any] = Field(
        default_factory=dict, description="Value after the override."
    )
    reason: str = Field(description="Reason for the override (NFR-AUD-003).")
    created_at: datetime | None = Field(default=None, description="Creation timestamp.")


# --------------------------------------------------------------------------- #
# Top-level analysis input / result envelopes (§10.6 / §10.7)
# --------------------------------------------------------------------------- #
class PointInput(_Base):
    """A point input (base_assumptions §10.6 ``input.point``)."""

    x: float = Field(description="X coordinate.")
    y: float = Field(description="Y coordinate.")
    crs: str = Field(default="EPSG:4326", description="CRS of the point.")


class AnalysisInputData(_Base):
    """The ``input`` block of ``parcel_analyze`` (base_assumptions §10.6)."""

    parcel_id: str | None = Field(default=None, description="Parcel identifier.")
    address: str | None = Field(default=None, description="Free-text address.")
    point: PointInput | None = Field(default=None, description="Point with CRS.")
    geometry: str | None = Field(
        default=None, description="GeoJSON or WKT geometry as a string, or null."
    )
    uploaded_files: list[str] = Field(
        default_factory=list, description="Uploaded file ids attached to the analysis."
    )


class InvestmentGoal(_Base):
    """Investment goal (base_assumptions §10.6 ``investment_goal``)."""

    type: InvestmentType = Field(
        default=InvestmentType.UNKNOWN, description="Investment type."
    )
    target_gfa_m2: float | None = Field(default=None, description="Target gross floor area, m^2.")
    target_units: int | None = Field(default=None, description="Target number of units.")
    risk_preference: str = Field(
        default="balanced", description="conservative | balanced | optimistic."
    )


class AnalysisOptions(_Base):
    """Analysis options (base_assumptions §10.6 ``options``)."""

    country: str = Field(default="PL", description="Country code.")
    ruleset_version: str = Field(default="latest", description="Ruleset version or 'latest'.")
    strict_sources_only: bool = Field(
        default=False, description="Use only strict (binding) sources."
    )
    include_auxiliary_sources: bool = Field(
        default=True, description="Include auxiliary sources."
    )
    return_maps: bool = Field(default=True, description="Whether to return map artifacts.")
    return_evidence: bool = Field(default=True, description="Whether to return evidence.")
    max_runtime_profile: str = Field(
        default="standard", description="fast | standard | exhaustive."
    )


class AnalysisInput(_Base):
    """Full ``parcel_analyze`` input (base_assumptions §10.6)."""

    input: AnalysisInputData = Field(description="Input locating the parcel/area.")
    analysis_mode: AnalysisMode = Field(description="quick/full/design/portfolio.")
    investment_goal: InvestmentGoal = Field(
        default_factory=InvestmentGoal, description="Investment goal."
    )
    options: AnalysisOptions = Field(
        default_factory=AnalysisOptions, description="Analysis options."
    )


class AnalysisScores(_Base):
    """Score block (base_assumptions §10.7 ``scores``; §14)."""

    buildability: float = Field(default=0.0, description="Buildability score.")
    planning_certainty: float = Field(default=0.0, description="Planning certainty score.")
    infrastructure: float = Field(default=0.0, description="Infrastructure score.")
    terrain: float = Field(default=0.0, description="Terrain score.")
    environmental_risk: float = Field(default=0.0, description="Environmental risk score.")
    procedural_risk: float = Field(default=0.0, description="Procedural risk score.")
    data_confidence: float = Field(default=0.0, description="Data confidence score.")


class AnalysisResult(_Base):
    """Full structured analysis result (base_assumptions §10.7).

    This is the canonical contract returned by ``parcel_analyze`` /
    ``analysis_get_result`` and the basis of ``analysis-result.schema.json``.
    """

    analysis_id: str = Field(description="Analysis run id (uuid).")
    status: AnalysisStatus = Field(
        description="complete | partial | failed | manual_review_required."
    )
    decision: Decision = Field(
        description="OK | OK_WITH_RISKS | NEEDS_MANUAL_REVIEW | LIKELY_BLOCKED."
    )
    scores: AnalysisScores = Field(
        default_factory=AnalysisScores, description="Analysis scores (§14)."
    )
    parcel: Parcel | None = Field(default=None, description="Resolved parcel.")
    planning: dict[str, Any] = Field(
        default_factory=dict, description="Planning context summary block."
    )
    constraints: list[Constraint] = Field(
        default_factory=list, description="Computed constraints (§11.3)."
    )
    buildable_envelope: BuildableEnvelope | None = Field(
        default=None, description="Buildable envelope result."
    )
    capacity_scenarios: list[CapacityScenario] = Field(
        default_factory=list, description="Capacity scenarios (§8.5)."
    )
    risks: list[RiskItem] = Field(default_factory=list, description="Risk register (§11.2).")
    unknowns: list[UnknownItem] = Field(
        default_factory=list, description="Unknown items (must persist, §20.10)."
    )
    next_actions: list[Recommendation] = Field(
        default_factory=list, description="Recommended next actions (NFR-DOM-006/008)."
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list, description="Evidence backing the result (NFR-AUD-001)."
    )
    artifacts: list[ReportArtifact] = Field(
        default_factory=list, description="Report/map/export artifacts."
    )
