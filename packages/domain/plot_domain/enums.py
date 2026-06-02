"""Domain enums (base_assumptions §11.2 risk taxonomy + supporting enums).

String enums so they serialize cleanly to JSON / JSON Schema.
"""

from __future__ import annotations

from enum import Enum


class RiskType(str, Enum):
    """Risk categories (base_assumptions §11.2 ``risk_type``)."""

    PLANNING = "planning"
    LEGAL = "legal"
    OWNERSHIP = "ownership"
    ROAD_ACCESS = "road_access"
    UTILITIES = "utilities"
    ENVIRONMENTAL = "environmental"
    FLOOD = "flood"
    GEOLOGY = "geology"
    HERITAGE = "heritage"
    TERRAIN = "terrain"
    TECHNICAL_BUILDING_RULES = "technical_building_rules"
    PROCEDURAL = "procedural"
    COST = "cost"
    DATA_QUALITY = "data_quality"
    MARKET = "market"


class Severity(str, Enum):
    """Severity scale (base_assumptions §11.2 ``severity``)."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConfidenceLevel(str, Enum):
    """Qualitative confidence (base_assumptions §11.2 ``confidence``)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskStatus(str, Enum):
    """Risk status (base_assumptions §11.2 ``status``).

    Keeps the spec distinction between detected / suspected / not_detected /
    unknown / manual_review_required (§2.1, §21).
    """

    DETECTED = "detected"
    SUSPECTED = "suspected"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class SourceType(str, Enum):
    """Source type (base_assumptions §5 ``source_type``)."""

    OFFICIAL_REGISTER = "official_register"
    LOCAL_SIP = "local_sip"
    USER_DOCUMENT = "user_document"
    COMMERCIAL = "commercial"
    AUXILIARY = "auxiliary"


class LegalStatus(str, Enum):
    """Legal status of a source / constraint (base_assumptions §5, §11.3)."""

    BINDING = "binding"
    INFORMATIVE = "informative"
    AUXILIARY = "auxiliary"
    UNKNOWN = "unknown"


class GeometryPrecision(str, Enum):
    """Geometry precision class (base_assumptions §5 ``geometry_precision``)."""

    SURVEY = "survey"
    CADASTRAL = "cadastral"
    TOPOGRAPHIC = "topographic"
    RASTER_DERIVED = "raster_derived"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


class Freshness(str, Enum):
    """Source freshness (base_assumptions §5 ``freshness``)."""

    CURRENT = "current"
    STALE = "stale"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


class AnalysisMode(str, Enum):
    """Analysis mode (base_assumptions §10.6 ``analysis_mode``)."""

    QUICK_SCREENING = "quick_screening"
    FULL_DUE_DILIGENCE = "full_due_diligence"
    DESIGN_FEASIBILITY = "design_feasibility"
    PORTFOLIO_BATCH = "portfolio_batch"


class AnalysisStatus(str, Enum):
    """Analysis run status (base_assumptions §10.7 ``status``)."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class Decision(str, Enum):
    """Top-level decision (base_assumptions §10.7 ``decision``, §4.1)."""

    OK = "OK"
    OK_WITH_RISKS = "OK_WITH_RISKS"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    LIKELY_BLOCKED = "LIKELY_BLOCKED"


class InvestmentType(str, Enum):
    """Investment goal type (base_assumptions §10.6 ``investment_goal.type``)."""

    SINGLE_FAMILY = "single_family"
    MULTIFAMILY = "multifamily"
    SERVICES = "services"
    WAREHOUSE = "warehouse"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class PlanningActType(str, Enum):
    """Planning act type (base_assumptions §11.1 PlanningAct: MPZP/POG/WZ/ULICP/ZPI/draft)."""

    MPZP = "MPZP"
    POG = "POG"
    WZ = "WZ"
    ULICP = "ULICP"
    ZPI = "ZPI"
    DRAFT_PLAN = "draft_plan"
