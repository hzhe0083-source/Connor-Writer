"""Current-scene grounding helpers for certified skill readout."""

from __future__ import annotations

from typing import Any

from .schema import CertifiedSkill, DCEAInput
from .scoring import trust_score


def audit_pointer(skill: CertifiedSkill) -> dict[str, Any]:
    return {
        "skill_id": skill.id,
        "key": skill.key,
        "version": skill.Z.get("version"),
        "promotion_id": skill.Z.get("promotion_id"),
        "evidence_ids": skill.Z.get("evidence_ids", []),
    }


def resolve_binding(skill: CertifiedSkill, context: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    bindings = context.get("object_bindings", {})
    if not isinstance(bindings, dict):
        return {}, "context.object_bindings must be an object"

    anchor_role = str(skill.G.get("anchor_role", "anchor"))
    target_role = str(skill.G.get("target_role", "target"))
    anchor = bindings.get(anchor_role) or bindings.get("anchor")
    target = bindings.get(target_role) or bindings.get("target")
    binding = {
        "anchor_role": anchor_role,
        "target_role": target_role,
        "anchor": anchor,
        "target": target,
    }
    if not isinstance(anchor, dict):
        return binding, f"missing grounded anchor role: {anchor_role}"
    if not isinstance(target, dict):
        return binding, f"missing grounded target role: {target_role}"
    if float(anchor.get("grounding_confidence", 0.0)) <= 0.0:
        return binding, f"ungrounded anchor role: {anchor_role}"
    if float(target.get("grounding_confidence", 0.0)) <= 0.0:
        return binding, f"ungrounded target role: {target_role}"
    return binding, None


def activation_score(skill: CertifiedSkill, binding: dict[str, Any], trust: float) -> float:
    anchor = binding.get("anchor") or {}
    target = binding.get("target") or {}
    if not isinstance(anchor, dict) or not isinstance(target, dict):
        return 0.0
    p_ground = min(
        float(anchor.get("grounding_confidence", 0.0)),
        float(target.get("grounding_confidence", 0.0)),
    )
    return max(0.0, min(1.0, trust * p_ground))


def build_dcea_input(
    skill: CertifiedSkill,
    binding: dict[str, Any],
    context: dict[str, Any] | None = None,
    now: str | None = None,
) -> DCEAInput:
    context = context or {}
    trust = trust_score(skill.P, now=now)
    activation = activation_score(skill, binding, trust)
    anchor = binding.get("anchor") if isinstance(binding.get("anchor"), dict) else {}
    target = binding.get("target") if isinstance(binding.get("target"), dict) else {}
    return DCEAInput(
        relation_type=str(skill.G.get("relation_type", "unknown")),
        anchor_slot=anchor.get("slot"),
        target_slot=target.get("slot"),
        activation_score=activation,
        relation_kernel=skill.G.get("relation_kernel", {}),
        grounding_requirements=skill.G.get("grounding_requirements", []),
        confidence_features={
            "p_ground_anchor": float(anchor.get("grounding_confidence", 0.0)),
            "p_ground_target": float(target.get("grounding_confidence", 0.0)),
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

