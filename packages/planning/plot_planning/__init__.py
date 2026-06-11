"""Planning intelligence (Phase 8): APP/GML pipeline + document parser + use matrix.

Public surface:

* :func:`parse_app_gml` / :func:`zone_coverage` / :func:`indicators_from_zone` —
  APP GML (XSD v2.0, Dz.U. 2023 poz. 2409) → ``PlanningAct``/``PlanningZone`` +
  zone∩parcel coverage % (F-0108–0111);
* :class:`PlanningStore` / :data:`DEFAULT_PLANNING_STORE` — process-local store
  behind ``planning_fetch`` and the ``planning://`` resources;
* :mod:`plot_planning.parser` — the §29 document parser (deterministic
  extractors + LLM-candidate validation with hallucination rejection, F-0550),
  untrusted-content mode (NFR-SEC-002/003/009);
* :func:`use_matrix_for` — allowed/conditional/forbidden per investment category
  (F-0121–0123);
* :func:`detect_conflicts` — GML-vs-document conflicts, never auto-resolved
  (§25.2, F-0125);
* :func:`stability_score` — traced planning-stability heuristic (F-0126);
* :mod:`plot_planning.capacity` — the Phase 9 chłonność engine
  (:func:`building_metrics` / :func:`masterplan_metrics` /
  :func:`generate_capacity_scenarios`, §8.5 F-0180–0215): estimation factors live in
  :class:`CapacityConfig` (basis ``industry_heuristic``); legal values (WT §39/§40) are
  read from the ruleset registry via :mod:`plot_rules`;
* :mod:`plot_planning.brief` — the Phase 11 design-brief generator
  (:func:`generate_design_brief` / :func:`composition_axes` — straight skeleton with
  medial-axis fallback) + :data:`DEFAULT_BRIEF_STORE` backing the
  ``analysis://{id}/design-brief`` resource;
* :mod:`plot_planning.typologies` — design-practice typology SUGGESTIONS
  (:func:`recommend_typologies`; never validators — plan §11.4);
* :mod:`plot_planning.staging` — Phase 11 etapowanie consistency checks
  (:func:`check_staging`; always soft; the WT §40 trigger comes from the rules
  engine, never a literal).

Decoupling (§9.4): no imports of ``plot_connectors`` (fetching is orchestrated
above this layer) and no legal threshold values in code (rulesets only).
"""

from __future__ import annotations

from plot_planning.brief import (
    DEFAULT_BRIEF_STORE,
    BriefConfig,
    DesignBrief,
    DesignBriefStore,
    analyze_frontages,
    composition_axes,
    generate_design_brief,
)
from plot_planning.capacity import (
    BuildingMetrics,
    CapacityConfig,
    CapacityScenarioSet,
    MasterplanMetrics,
    building_metrics,
    generate_capacity_scenarios,
    masterplan_metrics,
)
from plot_planning.conflicts import (
    MANUAL_REVIEW_REQUIRED,
    Conflict,
    IndicatorValue,
    detect_conflicts,
)
from plot_planning.gml import (
    UNKNOWN_ACT_ID,
    GmlParseError,
    ParsedPlanning,
    ZoneCoverage,
    indicators_from_zone,
    parse_app_gml,
    zone_coverage,
)
from plot_planning.parser import (
    INDICATOR_NAMES,
    PLANNING_INDICATORS_SCHEMA,
    IndicatorExtraction,
    extract_indicators,
    missing_indicators,
    screen_document,
    validate_candidates,
)
from plot_planning.stability import stability_score
from plot_planning.staging import StageCheck, check_staging
from plot_planning.store import DEFAULT_PLANNING_STORE, PlanningStore
from plot_planning.typologies import TypologyRecommendation, recommend_typologies
from plot_planning.use_matrix import USE_CATEGORIES, use_matrix_for

__version__ = "0.3.0"

__all__ = [
    "DEFAULT_BRIEF_STORE",
    "DEFAULT_PLANNING_STORE",
    "INDICATOR_NAMES",
    "MANUAL_REVIEW_REQUIRED",
    "PLANNING_INDICATORS_SCHEMA",
    "UNKNOWN_ACT_ID",
    "USE_CATEGORIES",
    "BriefConfig",
    "BuildingMetrics",
    "CapacityConfig",
    "CapacityScenarioSet",
    "Conflict",
    "DesignBrief",
    "DesignBriefStore",
    "GmlParseError",
    "IndicatorExtraction",
    "IndicatorValue",
    "MasterplanMetrics",
    "ParsedPlanning",
    "PlanningStore",
    "StageCheck",
    "TypologyRecommendation",
    "ZoneCoverage",
    "analyze_frontages",
    "building_metrics",
    "check_staging",
    "composition_axes",
    "detect_conflicts",
    "extract_indicators",
    "generate_capacity_scenarios",
    "generate_design_brief",
    "masterplan_metrics",
    "indicators_from_zone",
    "missing_indicators",
    "parse_app_gml",
    "recommend_typologies",
    "screen_document",
    "stability_score",
    "use_matrix_for",
    "validate_candidates",
    "zone_coverage",
]
