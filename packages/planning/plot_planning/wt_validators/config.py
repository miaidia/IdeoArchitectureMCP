"""Geometric config + shared helpers for the Phase 10 WT/ppoż validators.

EVERY legal threshold (distances, hours, brackets, modifiers) is read from the
Phase 8 ruleset YAMLs through :func:`plot_rules.evaluate` / the rule's
``thresholds`` table — this module carries ONLY geometric/estimation parameters
(sampling densities, heuristic sill height, classification defaults), each with
an explicit ``basis`` marker (plan §10.4 anti-pattern guard: no bare legal
constants like 4.0/3.0/5.0/8.0/35.0 in ``wt_validators``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from plot_domain.models import Override
from plot_rules import EvaluationMode, OverrideStore, Rule, RuleCheck, RuleStatus

# --------------------------------------------------------------------------- #
# Rule ids consumed by this package (structural references; VALUES live in YAML).
# --------------------------------------------------------------------------- #
RULE_WT12 = "PL-WT-12-SETBACKS-001"
RULE_WT13 = "PL-WT-13-PRZESLANIANIE-001"
RULE_WT60 = "PL-WT-60-NASLONECZNIENIE-001"
RULE_WT19 = "PL-WT-19-PARKING-DISTANCES-001"
RULE_WT21 = "PL-WT-21-PARKING-DIMENSIONS-001"
RULE_WT39 = "PL-WT-39-PBC-001"
RULE_WT40 = "PL-WT-40-PLAC-ZABAW-001"
RULE_PPOZ_271 = "PL-PPOZ-271-273-FIRE-SEPARATION-001"
RULE_PPOZ_DROGA = "PL-PPOZ-DROGA-POZAROWA-001"

#: Rule ids whose evaluation path consumes the OverrideStore (manual_override
#: reports ``applied=True``/``status="active"`` ONLY for these AND only under
#: an analysis id a production path actually queries — §21 honesty).
CONSUMED_RULE_IDS: tuple[str, ...] = (
    RULE_WT12,
    RULE_WT13,
    RULE_WT60,
    RULE_WT19,
    RULE_WT39,
    RULE_WT40,
    RULE_PPOZ_271,
    RULE_PPOZ_DROGA,
)

# Basis tags (mirroring plot_planning.capacity — §0v2.4 anti-pattern guard).
BASIS_GEOMETRY = "geometry_measured"
BASIS_HEURISTIC = "industry_heuristic"
BASIS_SIMPLIFIED = "simplified_classification"

#: Mirrors plot_rules.engine default ``confidence_policy.decided`` (0.9) for the
#: few MANUALLY constructed checks (e.g. "no obstruction within search radius");
#: a presentation default, not a legal value.
DECIDED_CONFIDENCE = 0.9
#: Confidence reduction applied to manual checks built on ASSUMED inputs
#: (mirrors the engine's ``assumed_penalty`` default 0.1).
ASSUMED_PENALTY = 0.1


@dataclass(frozen=True)
class ValidatorConfig:
    """Geometric/estimation parameters of the Phase 10 validators (config, NOT law).

    Every field is either a pure sampling/numerical-precision parameter
    (``basis: geometry_sampling``) or an industry/classification heuristic
    (``basis`` noted per field). Legal values NEVER live here.
    """

    # basis: industry_heuristic — storey height for floors→height derivation
    # (same default as plot_planning.CapacityConfig.floor_height_m; height =
    # floors × floor_height_m unless an explicit height is given, plan §10).
    floor_height_m: float = 3.3
    # basis: industry_heuristic — sill of the LOWEST window assumed at the
    # ground-floor parapet (~1.0 m above terrain); §13 ust. 2 measures wysokość
    # przesłaniania from the lower edge of the lowest windows (documented
    # assumption — the DSL carries no per-window sill data).
    window_sill_m: float = 1.0
    # basis: geometry_sampling — window-axis sampling spacing along windowed
    # walls (§13/§60); every wall gets at least its midpoint sample.
    window_spacing_m: float = 6.0
    # basis: geometry_sampling — outward offset of sampled window points so the
    # point lies just OUTSIDE its own wall (numerical separation only).
    window_offset_m: float = 0.05
    # basis: geometry_sampling — grid spacing for playground insolation (§40).
    playground_grid_m: float = 2.0
    # basis: industry_heuristic — surface stall + maneuvering area (~2.5×5 m
    # stall per WT §21 + dojazd) used ONLY for the §19 plausibility WARNING.
    stall_area_m2: float = 25.0
    # basis: simplified_classification — gęstość obciążenia ogniowego Q assumed
    # for PM buildings (garażowy/techniczny) when no Q is declared; ≤1000 MJ/m²
    # keeps the §271 base bracket (the conservative ZL-equivalent row).
    pm_q_mj_m2: float = 1000.0
    # basis: geometry_sampling — facade sampling step for the fire-road band test.
    facade_sample_m: float = 1.0
    # basis: simplification — share of the longest facade edge that must see the
    # fire road within the legal 5–15 m band to count as "wzdłuż dłuższego boku"
    # (rozp. MSWiA §12 ust. 2 gives no numeric share; documented simplification).
    fire_road_coverage_share: float = 0.5
    # basis: geometry_sampling — tolerance for road-endpoint connectivity
    # (dead-end detection) against other roads / the parcel boundary.
    road_connection_tol_m: float = 0.5
    # Default site for solar position (parameterized; tests use Warsaw).
    site_lat: float = 52.2297
    site_lon: float = 21.0122
    # Równonoc reference date (plan §10.1.3: March equinox; fixed year for
    # deterministic golden tests — year-to-year solar drift is negligible).
    equinox_date: str = "2026-03-20"
    # basis: geometry_sampling — solar sampling step (15-min per plan §10.1.3).
    sun_step_min: int = 15
    # Civil-time zone the WT hour windows (7–17 / 10–16) refer to.
    timezone: str = "Europe/Warsaw"

    def basis_block(self, *names: str) -> dict[str, Any]:
        """Metadata block recorded in validator trace/evidence (auditability).

        ``names`` selects the entries RELEVANT to one validator (e.g. §13 uses
        ``floor_height_m`` + ``window_sill_m``); without arguments the full
        block is returned.
        """
        block = {
            "floor_height_m": {"value": self.floor_height_m, "basis": BASIS_HEURISTIC},
            "window_sill_m": {"value": self.window_sill_m, "basis": BASIS_HEURISTIC},
            "stall_area_m2": {"value": self.stall_area_m2, "basis": BASIS_HEURISTIC},
            "pm_q_mj_m2": {"value": self.pm_q_mj_m2, "basis": BASIS_SIMPLIFIED},
            "fire_road_coverage_share": {
                "value": self.fire_road_coverage_share,
                "basis": "simplification",
            },
        }
        if not names:
            return block
        return {name: block[name] for name in names}


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def rule_threshold(rule: Rule, name: str) -> float:
    """Read a named threshold VALUE from a loaded rule (values live in YAML only)."""
    thresholds = rule.raw.get("thresholds") or {}
    if name not in thresholds:
        raise KeyError(f"rule {rule.id} has no threshold '{name}'")
    return float(thresholds[name])


def find_override(
    overrides: OverrideStore | None,
    analysis_id: str | None,
    rule_id: str,
    subject: str | None = None,
) -> Override | None:
    """Latest audited override for ``(analysis_id, rule_id[, subject])`` (F-0137).

    ``subject`` is the validator's evaluation subject: a building name for
    per-building rules (§12/§13/§60/droga pożarowa), ``"pair:A|B"`` (names
    sorted) for the pairwise §271 evaluation, ``"parking:N"`` for §19. A
    subject-scoped record (``target_id == f"{rule_id}#{subject}"``) wins over a
    rule-wide one (bare rule id), which covers EVERY subject of the rule.
    """
    if overrides is None or analysis_id is None:
        return None
    return overrides.for_rule(analysis_id, rule_id, subject)


def missing_rule_check(rule_id: str, mode: EvaluationMode | str) -> RuleCheck:
    """The honest outcome when a consumed ruleset is NOT loaded: never a silent pass.

    Mirrors the engine's mode handling for unknowns on hard rules (strict→fail,
    conservative→warning + blocker note, optimistic→unknown).
    """
    mode = EvaluationMode(mode)
    status = RuleStatus.UNKNOWN
    note = ""
    if mode is EvaluationMode.STRICT:
        status = RuleStatus.FAIL
        note = " [strict mode: unknown on hard rule -> fail]"
    elif mode is EvaluationMode.CONSERVATIVE:
        status = RuleStatus.WARNING
        note = " [conservative mode: potential blocker]"
    return RuleCheck(
        rule_id=rule_id,
        status=status,
        severity="hard",
        message=(
            f"{rule_id}: ruleset not loaded — the rule cannot be verified "
            f"(missing YAML in the registry){note}"
        ),
        trace={"mode": mode.value, "reason": "ruleset_not_loaded"},
        confidence=0.2,
    )


def with_evidence(
    check: RuleCheck,
    *,
    message: str | None = None,
    evidence: dict[str, Any] | None = None,
    extra_trace: dict[str, Any] | None = None,
) -> RuleCheck:
    """Attach geometry evidence (+ a human message naming the buildings) to a check.

    An audited override message from the engine ("status overridden ... [audited
    override]") is NEVER rewritten — a validator's geometric summary would turn
    into a false affirmative claim (e.g. "wszystkie sciany >= wymaganej
    odleglosci" for a failing-but-overridden building). The validator context is
    appended instead, keeping the override statement primary.
    """
    update: dict[str, Any] = {}
    if message is not None:
        if "override" in check.trace:
            update["message"] = f"{check.message} [kontekst walidatora: {message}]"
        else:
            update["message"] = f"{check.rule_id}: {message}"
    if evidence is not None:
        update["geometry_evidence"] = evidence
    if extra_trace:
        update["trace"] = {**check.trace, **extra_trace}
    return check.model_copy(update=update)


def decided_entry(check: RuleCheck) -> dict[str, Any] | None:
    """The trace entry of the (single) applicable pass/fail comparison of a check."""
    entries = [
        e
        for e in check.trace.get("checks", [])
        if e.get("status") in ("pass", "fail") and "target_value" in e
    ]
    if not entries:
        return None
    # The FAILING entry is the binding one; otherwise the smallest pass margin.
    fails = [e for e in entries if e["status"] == "fail"]
    pool = fails or entries
    return min(pool, key=lambda e: float(e["input_value"]) - float(e["target_value"]))


def check_margin(check: RuleCheck) -> float:
    """input − target of the binding comparison (∞ when nothing was decided)."""
    entry = decided_entry(check)
    if entry is None:
        return float("inf")
    return float(entry["input_value"]) - float(entry["target_value"])
