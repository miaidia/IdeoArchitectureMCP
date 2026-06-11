"""Declarative rule evaluation engine (Phase 8, base_assumptions §12, F-0128-0138).

``evaluate(rule, inputs)`` interprets the structured fields of a ruleset YAML
document — ``applies_when`` (equality/flag conditions), ``thresholds`` (named
numeric values), ``select`` (bracket tables), ``checks`` (comparisons), and
``input_defaults`` (documented conservative assumptions) — and returns a
:class:`RuleCheck` with status ``pass|fail|warning|unknown|not_applicable`` plus a
full trace (which inputs, which threshold, which source citation).

Core guarantees:

- A missing required input yields ``unknown`` — NEVER a silent pass (§21).
- Evaluation modes (F-0134-0138): ``conservative`` reports an unknown on a hard
  rule as a ``warning`` with a blocker note; ``optimistic`` keeps it ``unknown``;
  ``strict`` treats it as ``fail``.
- The engine is value-free: every legal threshold lives in YAML (Phase 8
  anti-pattern guard) — this module only knows how to compare and trace.
- Expert overrides (F-0137, §26.1) reuse the audited ``plot_domain`` ``Override``
  record; the override is applied on top of the computed result and the audit
  note (author + reason + before/after) is embedded in the trace.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from plot_domain.models import Override
from pydantic import BaseModel, ConfigDict, Field

from plot_rules.loader import Rule


class RuleStatus(str, Enum):
    """Rule outcome (base_assumptions §12.1)."""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class EvaluationMode(str, Enum):
    """How unknowns on hard rules are reported (F-0134-0138)."""

    STRICT = "strict"
    CONSERVATIVE = "conservative"
    OPTIMISTIC = "optimistic"


class RuleCheck(BaseModel):
    """Result of evaluating one rule against one input set (§12.1)."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(description="Id of the evaluated rule.")
    status: RuleStatus = Field(description="pass/fail/warning/unknown/not_applicable.")
    severity: str = Field(description="Rule severity: hard|soft (§12.1).")
    message: str = Field(default="", description="Human-readable outcome summary.")
    trace: dict[str, Any] = Field(
        default_factory=dict,
        description="Inputs used, thresholds applied, per-check outcomes, citations (NFR-AUD-005).",
    )
    source_reference: str | None = Field(
        default=None, description="Legal source citation of the rule (Dz.U. reference)."
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence in this outcome (§25.1)."
    )
    # Phase 10 (wt_validators): GeoJSON-bearing evidence of the checked/offending
    # geometry, e.g. {"geometry": <GeoJSON>, "building": ..., "assumed_windowed": ...}.
    # The engine itself never fills this — geometric validators attach it so the
    # renderer can draw a violation overlay (plan §10.1.7). None for pure evaluations.
    geometry_evidence: dict[str, Any] | None = Field(
        default=None,
        description="GeoJSON evidence of the offending geometry pair/zone (Phase 10).",
    )


# Internal per-check outcomes (before aggregation).
_DECIDED = ("pass", "fail")


def override_targets_rule(target_id: str, rule_id: str) -> bool:
    """Does an override ``target_id`` cover ``rule_id``?

    Two audited forms (F-0137, Phase 10): the bare rule id (rule-wide) and
    ``"<rule_id>#<subject>"`` (scoped to ONE evaluation subject, e.g. a building
    name — the caller decides which subject's evaluation receives the record).
    """
    return target_id == rule_id or target_id.startswith(f"{rule_id}#")


def override_subject(target_id: str) -> str | None:
    """The subject of a ``"<rule_id>#<subject>"`` target id (``None`` = rule-wide)."""
    _, _, subject = target_id.partition("#")
    return subject or None


def _conditions_met(cond: dict[str, Any], inputs: dict[str, Any]) -> bool | None:
    """Evaluate an ``applies_when`` map. ``None`` = a condition input is missing.

    Values are simple equality / flag conditions; a list value means membership
    (e.g. ``building_type: [wielorodzinny, oswiaty_i_wychowania]``); a dict value
    is a numeric bracket — ``min``/``max`` (inclusive) and ``gt``/``lt``
    (exclusive) bounds that must all hold. The bracket form is a GENERIC
    mechanism so YAML rules can gate checks/modifiers on value ranges (e.g. a
    legal regime that only applies above/below a width or unit count) — the
    bounds themselves always live in the YAML, never here.
    """
    for key, expected in cond.items():
        if key not in inputs or inputs[key] is None:
            return None
        actual = inputs[key]
        if isinstance(expected, dict):
            if "min" in expected and not actual >= expected["min"]:
                return False
            if "max" in expected and not actual <= expected["max"]:
                return False
            if "gt" in expected and not actual > expected["gt"]:
                return False
            if "lt" in expected and not actual < expected["lt"]:
                return False
        elif isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _bracket_matches(
    when: dict[str, Any], inputs: dict[str, Any]
) -> tuple[bool | None, str | None]:
    """Match one select bracket. Returns (matched, missing_param)."""
    for param, cond in when.items():
        if param not in inputs or inputs[param] is None:
            return None, param
        value = inputs[param]
        if isinstance(cond, dict):
            if "min" in cond and not value >= cond["min"]:
                return False, None
            if "max" in cond and not value <= cond["max"]:
                return False, None
        elif value != cond:
            return False, None
    return True, None


def _resolve_select(
    name: str, rule: Rule, inputs: dict[str, Any]
) -> tuple[float | None, dict[str, Any]]:
    """Resolve a named bracket table to a value. First matching bracket wins.

    A bracket whose parameter is missing makes the whole select unresolvable
    (``None``) — the engine cannot prove which legal bracket applies, so the
    check becomes ``unknown`` rather than silently picking a bracket.
    """
    tables = rule.raw.get("select") or {}
    brackets = tables.get(name)
    info: dict[str, Any] = {"select": name}
    if not brackets:
        info["error"] = "select table not found"
        return None, info
    for idx, bracket in enumerate(brackets):
        matched, missing = _bracket_matches(bracket.get("when", {}), inputs)
        if matched is None:
            info["missing_input"] = missing
            return None, info
        if not matched:
            continue
        info["bracket"] = bracket.get("when")
        if "value" in bracket:
            return float(bracket["value"]), info
        if "value_per_unit" in bracket:
            # Per-unit brackets multiply by the bracket's (single) when-param,
            # e.g. WT §40 ust. 8 pkt 1: 1 m2 na kazde mieszkanie (21-50).
            param = next(iter(bracket["when"]))
            value = float(bracket["value_per_unit"]) * float(inputs[param])
            info["per_unit_param"] = param
            return value, info
        if "value_from_input" in bracket:
            source = bracket["value_from_input"]
            if source not in inputs or inputs[source] is None:
                info["missing_input"] = source
                return None, info
            info["value_from_input"] = source
            return float(inputs[source]), info
        info["error"] = f"bracket {idx} has no value"
        return None, info
    info["error"] = "no bracket matched"
    return None, info


def _resolve_target(
    check: dict[str, Any], rule: Rule, inputs: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    """Resolve the comparison target of a check (threshold/select/other_input/value)."""
    info: dict[str, Any] = {}
    if "threshold" in check:
        ref = check["threshold"]
        if isinstance(ref, str):
            thresholds = rule.raw.get("thresholds") or {}
            if ref not in thresholds:
                info["error"] = f"named threshold '{ref}' not found"
                return None, info
            info["threshold"] = ref
            return thresholds[ref], info
        info["threshold"] = ref
        return ref, info
    if "select" in check:
        value, sel_info = _resolve_select(check["select"], rule, inputs)
        info.update(sel_info)
        return value, info
    if "other_input" in check:
        name = check["other_input"]
        info["other_input"] = name
        if name not in inputs or inputs[name] is None:
            info["missing_input"] = name
            return None, info
        return inputs[name], info
    if "value" in check:
        info["literal"] = check["value"]
        return check["value"], info
    info["error"] = "check has no comparison target"
    return None, info


def _apply_modifiers(
    target: float, check: dict[str, Any], inputs: dict[str, Any]
) -> tuple[float, list[dict[str, Any]]]:
    """Apply threshold modifiers in declaration order (e.g. srodmiejska halving)."""
    applied: list[dict[str, Any]] = []
    for modifier in check.get("modifiers") or []:
        met = _conditions_met(modifier.get("when", {}), inputs)
        if met is None:
            applied.append({"when": modifier.get("when"), "status": "undetermined"})
            continue
        if not met:
            continue
        before = target
        target = target * float(modifier["multiply"])
        if "at_least" in modifier:
            target = max(target, float(modifier["at_least"]))
        applied.append(
            {
                "when": modifier.get("when"),
                "multiply": modifier["multiply"],
                "before": before,
                "after": target,
                "note": modifier.get("note"),
            }
        )
    return target, applied


def _compare(op: str, value: Any, target: Any) -> bool:
    if op == "gte":
        return bool(value >= target)
    if op == "lte":
        return bool(value <= target)
    if op == "gt":
        return bool(value > target)
    if op == "lt":
        return bool(value < target)
    if op == "eq":
        return bool(value == target)
    if op == "ne":
        return bool(value != target)
    raise ValueError(f"unsupported op: {op}")


def _run_check(
    check: dict[str, Any], rule: Rule, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate one check; returns its trace entry with a ``status`` field."""
    entry: dict[str, Any] = {"name": check.get("name"), "op": check.get("op")}
    if check.get("source_reference"):
        entry["source_reference"] = check["source_reference"]

    cond = check.get("applies_when") or {}
    met = _conditions_met(cond, inputs)
    if met is None:
        entry["status"] = "undetermined"
        entry["reason"] = "applies_when condition input missing"
        entry["applies_when"] = cond
        return entry
    if not met:
        entry["status"] = "skipped"
        entry["applies_when"] = cond
        return entry

    op = check["op"]
    if op == "exempt":
        # Explicit legal exemption branch (e.g. WT §60 ust. 3: mieszkanie
        # jednopokojowe w zabudowie srodmiejskiej — no minimum required).
        entry["status"] = "pass"
        entry["exempt"] = True
        return entry

    input_name = check.get("input")
    if op == "present":
        entry["input"] = input_name
        present = bool(input_name) and inputs.get(str(input_name)) is not None
        entry["status"] = "pass" if present else "unknown"
        if not present:
            entry["reason"] = f"required input '{input_name}' missing"
        return entry

    if op == "resolve":
        target, info = _resolve_target(check, rule, inputs)
        entry.update(info)
        entry["resolved_value"] = target
        entry["status"] = "pass" if target is not None else "unknown"
        if target is None:
            entry["reason"] = "comparison/select target unresolvable"
        return entry

    # Comparison ops need the input value...
    entry["input"] = input_name
    if input_name is None or input_name not in inputs or inputs[input_name] is None:
        entry["status"] = "unknown"
        entry["reason"] = f"required input '{input_name}' missing"
        return entry
    value = inputs[input_name]
    # ...and a resolvable target.
    target, info = _resolve_target(check, rule, inputs)
    entry.update(info)
    if target is None:
        entry["status"] = "unknown"
        entry["reason"] = "comparison target unresolvable"
        return entry
    if isinstance(target, int | float) and not isinstance(target, bool):
        modified, applied = _apply_modifiers(float(target), check, inputs)
        if applied:
            entry["modifiers"] = applied
        target = modified
    entry["input_value"] = value
    entry["target_value"] = target
    entry["status"] = "pass" if _compare(op, value, target) else "fail"
    return entry


def _confidence(
    rule: Rule, status: RuleStatus, from_unknown: bool, assumed: dict[str, Any]
) -> float:
    policy = rule.raw.get("confidence_policy") or {}
    decided = float(policy.get("decided", 0.9))
    unknown = float(policy.get("unknown", 0.2))
    conservative_unknown = float(policy.get("conservative_unknown", 0.4))
    assumed_penalty = float(policy.get("assumed_penalty", 0.1))
    if status is RuleStatus.UNKNOWN:
        base = unknown
    elif from_unknown:
        # warning/fail produced from an unknown by the evaluation mode.
        base = conservative_unknown
    else:
        base = decided
    if assumed:
        base -= assumed_penalty
    return max(0.05, min(1.0, base))


def evaluate(
    rule: Rule,
    inputs: dict[str, Any],
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    override: Override | None = None,
) -> RuleCheck:
    """Evaluate a declarative rule against ``inputs`` (base_assumptions §12).

    ``inputs`` maps the rule's declared input names to values. Inputs absent from
    the map (or ``None``) are *missing*; if the rule declares ``input_defaults``
    the default is applied and recorded in the trace as an assumption (never
    silently). The returned :class:`RuleCheck` carries the full evaluation trace.
    """
    mode = EvaluationMode(mode)
    severity = rule.severity if rule.severity in ("hard", "soft") else "hard"

    # Documented conservative assumptions (input_defaults) — recorded, not silent.
    defaults = rule.raw.get("input_defaults") or {}
    assumed = {k: v for k, v in defaults.items() if inputs.get(k) is None}
    effective = {**assumed, **{k: v for k, v in inputs.items() if v is not None}}

    trace: dict[str, Any] = {
        "mode": mode.value,
        "inputs": {k: effective.get(k) for k in rule.inputs if k in effective},
        "source_reference": rule.source_reference,
    }
    if assumed:
        trace["assumed_inputs"] = assumed

    status: RuleStatus
    from_unknown = False
    blocker_note: str | None = None

    rule_cond = rule.raw.get("applies_when") or {}
    applicable = _conditions_met(rule_cond, effective)
    checks = rule.raw.get("checks") or []

    if applicable is False:
        status = RuleStatus.NOT_APPLICABLE
        trace["applies_when"] = rule_cond
        message = f"{rule.id}: not applicable ({rule_cond})"
    elif applicable is None:
        status = RuleStatus.UNKNOWN
        from_unknown = True
        trace["applies_when"] = rule_cond
        trace["missing_inputs"] = [k for k in rule_cond if effective.get(k) is None]
        message = f"{rule.id}: applicability undetermined — missing {trace['missing_inputs']}"
    elif not checks:
        # No machine-evaluable checks: the engine cannot verify the rule, so it
        # reports unknown (never a silent pass).
        status = RuleStatus.UNKNOWN
        from_unknown = True
        message = f"{rule.id}: rule has no machine-evaluable checks"
    else:
        entries = [_run_check(check, rule, effective) for check in checks]
        trace["checks"] = entries
        # ``missing_input`` (the select/bracket parameter that was actually
        # absent) takes precedence over ``input`` (the provided comparison
        # value) so the trace names what is really missing.
        missing = sorted(
            {
                str(e.get("missing_input") or e.get("input"))
                for e in entries
                if e["status"] == "unknown"
                and (e.get("missing_input") or e.get("input")) is not None
            }
        )
        if missing:
            trace["missing_inputs"] = missing
        failed = [e for e in entries if e["status"] == "fail"]
        unknown = [e for e in entries if e["status"] in ("unknown", "undetermined")]
        passed = [e for e in entries if e["status"] == "pass"]
        if failed:
            status = RuleStatus.FAIL
            names = ", ".join(str(e.get("name")) for e in failed)
            message = f"{rule.id}: FAILED checks: {names}"
            if unknown:
                message += " (some conditions could not be verified — see trace)"
        elif unknown:
            status = RuleStatus.UNKNOWN
            from_unknown = True
            names = ", ".join(str(e.get("name")) for e in unknown)
            message = f"{rule.id}: could not verify: {names}"
        elif passed:
            status = RuleStatus.PASS
            message = f"{rule.id}: all applicable checks passed"
        else:
            # Every check was skipped by its own applies_when -> nothing applies.
            status = RuleStatus.NOT_APPLICABLE
            message = f"{rule.id}: no check applicable to these inputs"

    # Mode handling for unknowns / soft failures (F-0134-0138, §12.1).
    if status is RuleStatus.FAIL and severity == "soft":
        status = RuleStatus.WARNING
        message += " [soft rule -> warning]"
    elif status is RuleStatus.UNKNOWN and severity == "hard":
        if mode is EvaluationMode.STRICT:
            status = RuleStatus.FAIL
            message += " [strict mode: unknown on hard rule -> fail]"
        elif mode is EvaluationMode.CONSERVATIVE:
            status = RuleStatus.WARNING
            blocker_note = (
                "Hard rule could not be verified (missing inputs) — treat as a "
                "potential blocker until data is provided (conservative mode)."
            )
            trace["blocker_note"] = blocker_note
            message += " [conservative mode: potential blocker]"
        # optimistic mode: stays unknown.

    confidence = _confidence(rule, status, from_unknown, assumed)

    # Expert override hook (F-0137): applied last, fully audited (NFR-AUD-003).
    if override is not None and override_targets_rule(override.target_id, rule.id):
        new_status = override.after_json.get("status")
        if new_status is None:
            raise ValueError(f"override {override.id} for {rule.id} carries no status")
        subject = override_subject(override.target_id)
        trace["override"] = {
            "id": override.id,
            "user_id": override.user_id,
            "reason": override.reason,
            "before": override.before_json,
            "after": override.after_json,
            "original_status": status.value,
            # Audited scope: an override recorded WITHOUT a subject is an
            # explicit rule-wide choice (covers every subject of the rule).
            "scope": f"subject:{subject}" if subject else "rule-wide",
        }
        status = RuleStatus(new_status)
        message = (
            f"{rule.id}: status overridden to '{status.value}' by {override.user_id} "
            f"(reason: {override.reason}) [audited override]"
        )
        confidence = float(override.after_json.get("confidence", 0.95))

    return RuleCheck(
        rule_id=rule.id,
        status=status,
        severity=severity,
        message=message,
        trace=trace,
        source_reference=rule.source_reference,
        confidence=confidence,
    )
