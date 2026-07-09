"""Current-scene helpers for certified skill to subskill readout projection."""

from __future__ import annotations

from typing import Any

from .families import build_subskill_surface
from .schema import CertifiedSkill, GeometricSubskillSignal
from .scoring import trust_score


def audit_pointer(skill: CertifiedSkill) -> dict[str, Any]:
    return {
        "skill_id": skill.id,
        "key": skill.key,
        "version": skill.Z.get("version"),
        "promotion_id": skill.Z.get("promotion_id"),
        "evidence_ids": skill.Z.get("evidence_ids", []),
    }


def surface_for_skill(skill: CertifiedSkill) -> dict[str, Any]:
    return build_subskill_surface(skill.C, skill.O)


def resolve_binding(skill: CertifiedSkill, context: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    bindings = context.get("object_bindings", {})
    if not isinstance(bindings, dict):
        return {}, "context.object_bindings must be an object"

    surface = surface_for_skill(skill)
    anchor_role = str(surface.get("anchor_role", "anchor"))
    target_role = str(surface.get("target_role", "target"))
    anchor = bindings.get(anchor_role) or bindings.get("anchor")
    target = bindings.get(target_role) or bindings.get("target")
    binding = {
        "anchor_role": anchor_role,
        "target_role": target_role,
        "anchor": anchor,
        "target": target,
    }
    if not isinstance(anchor, dict):
        return binding, f"missing bound anchor role: {anchor_role}"
    if not isinstance(target, dict):
        return binding, f"missing bound target role: {target_role}"
    anchor_confidence = binding_confidence(anchor)
    target_confidence = binding_confidence(target)
    if anchor_confidence is not None and anchor_confidence <= 0.0:
        return binding, f"unbound anchor role: {anchor_role}"
    if target_confidence is not None and target_confidence <= 0.0:
        return binding, f"unbound target role: {target_role}"
    return binding, None


def binding_confidence(slot: dict[str, Any]) -> float | None:
    """Return an optional confidence supplied by the current-scene interface.

    Connor-Writer does not compute this value. It may be provided by a human
    annotation, a VLM/perception adapter, or a future grounding module.
    """
    value = slot.get("binding_confidence")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def binding_quality(binding: dict[str, Any]) -> float | None:
    values: list[float] = []
    for role in ("anchor", "target"):
        slot = binding.get(role)
        if isinstance(slot, dict):
            confidence = binding_confidence(slot)
            if confidence is not None:
                values.append(confidence)
    if not values:
        return None
    return max(0.0, min(1.0, min(values)))


def _slot_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("slot") or value.get("id") or "")
    return str(value or "")


def _relation_matches(
    evidence: dict[str, Any],
    relation_type: str,
    anchor_slot: str,
    target_slot: str,
    anchor_role: str,
    target_role: str,
) -> bool:
    if str(evidence.get("relation_type", "")).strip().lower() != relation_type:
        return False
    evidence_anchor = _slot_id(evidence.get("anchor") or evidence.get("subject"))
    evidence_target = _slot_id(evidence.get("target") or evidence.get("object"))
    valid_anchors = {anchor_slot, anchor_role, "anchor"}
    valid_targets = {target_slot, target_role, "target"}
    return evidence_anchor in valid_anchors and evidence_target in valid_targets


def relation_evidence(
    skill: CertifiedSkill,
    binding: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return current-scene relation evidence required for active readout.

    This prevents Connor-Writer from turning a role binding into an active
    subskill unless the current-scene interface explicitly states that the
    skill's relation is active and where that statement came from.
    """
    surface = surface_for_skill(skill)
    relation_type = str(surface.get("relation_type", "unknown")).strip().lower()
    anchor_slot = _slot_id(binding.get("anchor"))
    target_slot = _slot_id(binding.get("target"))
    anchor_role = str(binding.get("anchor_role") or "anchor")
    target_role = str(binding.get("target_role") or "target")
    evidence_items = context.get("relation_evidence")
    if evidence_items is None:
        schema = context.get("schema", {})
        if isinstance(schema, dict):
            evidence_items = schema.get("relation_evidence")
    if not isinstance(evidence_items, list):
        return None, f"missing relation evidence: {relation_type}({anchor_slot},{target_slot})"
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        if _relation_matches(item, relation_type, anchor_slot, target_slot, anchor_role, target_role):
            source = item.get("source")
            if not isinstance(source, str) or not source.strip():
                return None, f"relation evidence lacks source: {relation_type}({anchor_slot},{target_slot})"
            return item, None
    return None, f"no matching relation evidence: {relation_type}({anchor_slot},{target_slot})"


def activation_score(skill: CertifiedSkill, binding: dict[str, Any], trust: float) -> float:
    anchor = binding.get("anchor") or {}
    target = binding.get("target") or {}
    if not isinstance(anchor, dict) or not isinstance(target, dict):
        return 0.0
    quality = binding_quality(binding)
    if quality is None:
        return max(0.0, min(1.0, trust))
    return max(0.0, min(1.0, trust * quality))


def build_geometric_signal(
    skill: CertifiedSkill,
    binding: dict[str, Any],
    relation_item: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    now: str | None = None,
) -> GeometricSubskillSignal:
    context = context or {}
    trust = trust_score(skill.P, now=now)
    activation = activation_score(skill, binding, trust)
    anchor = binding.get("anchor") if isinstance(binding.get("anchor"), dict) else {}
    target = binding.get("target") if isinstance(binding.get("target"), dict) else {}
    anchor_confidence = binding_confidence(anchor)
    target_confidence = binding_confidence(target)
    quality = binding_quality(binding)
    surface = surface_for_skill(skill)
    return GeometricSubskillSignal(
        relation_type=str(surface.get("relation_type", "unknown")),
        anchor_slot=anchor.get("slot"),
        target_slot=target.get("slot"),
        activation_score=activation,
        relation_kernel=surface.get("relation_kernel", {}),
        grounding_requirements=surface.get("grounding_requirements", []),
        confidence_features={
            "binding_confidence_anchor": anchor_confidence,
            "binding_confidence_target": target_confidence,
            "binding_quality_used": quality,
            "binding_confidence_source": "provided" if quality is not None else "unavailable",
            "relation_evidence": relation_item or {},
            "trust": trust,
            "scope": skill.Z.get("scope"),
            "context_schema": context.get("schema", {}),
        },
        relative_option_prior={
            "option_family": skill.O.get("option_family"),
            "relative_frame": skill.O.get("relative_frame"),
            "parameter_prior": skill.O.get("parameter_prior", {}),
        },
        expected_belief_effect=skill.O.get("expected_belief_effect", {}),
        audit_pointer=audit_pointer(skill),
    )
