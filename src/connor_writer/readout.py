"""Runtime readout surfaces for certified skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bank import load_skill
from .context import canonicalize_context
from .families import effect_type_from_contract
from .schema import (
    ActiveSubskillReadout,
    CertifiedSkill,
    NullSubskillReadout,
    STATE_CERTIFIED,
    SchemaError,
    SemanticSkillToken,
    SubskillReadout,
    ensure_no_forbidden_payloads,
    stable_id,
    utc_now,
)
from .scoring import trust_score
from .subskill import (
    audit_pointer,
    build_geometric_signal,
    relation_evidence,
    resolve_binding,
    surface_for_skill,
)


def context_signature(context: dict[str, Any]) -> str:
    return stable_id("ctx", context)


def relation_signature(relation_item: dict[str, Any] | None) -> str | None:
    if relation_item is None:
        return None
    return stable_id("rel", relation_item)


def readout_id_for(
    skill: CertifiedSkill,
    status: str,
    binding: dict[str, Any],
    context_sig: str,
    relation_sig: str | None,
    reason: str | None = None,
) -> str:
    return stable_id(
        "ro",
        {
            "skill_id": skill.id,
            "skill_version": skill.Z.get("version"),
            "status": status,
            "binding": binding,
            "context_signature": context_sig,
            "relation_evidence_signature": relation_sig,
            "reason": reason,
        },
    )


class SkillReadoutBuilder:
    """Project a certified skill into a current-scene subskill readout."""

    def build(
        self,
        skill: CertifiedSkill | str | Path,
        context: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> SubskillReadout:
        if not isinstance(skill, CertifiedSkill):
            skill = load_skill(skill)
        if skill.state != STATE_CERTIFIED:
            raise SchemaError("only certified skills can produce runtime readouts")
        context = canonicalize_context(context or {})
        context_sig = context_signature(context)
        read_time = now or utc_now()
        trust = trust_score(skill.P, now=read_time)
        binding, null_reason = resolve_binding(skill, context)
        audit = audit_pointer(skill)
        if null_reason:
            readout_id = readout_id_for(skill, "null", binding, context_sig, None, null_reason)
            return NullSubskillReadout(
                readout_id=readout_id,
                generated_at=read_time,
                lifecycle_state="generated",
                context_signature=context_sig,
                relation_evidence_signature=None,
                skill_id=skill.id,
                key=skill.key,
                status="null",
                reason=null_reason,
                binding=binding,
                trust_score=0.0,
                audit_pointer=audit,
            )
        relation_item, relation_reason = relation_evidence(skill, binding, context)
        relation_sig = relation_signature(relation_item)
        if relation_reason:
            readout_id = readout_id_for(
                skill,
                "null",
                binding,
                context_sig,
                relation_sig,
                relation_reason,
            )
            return NullSubskillReadout(
                readout_id=readout_id,
                generated_at=read_time,
                lifecycle_state="generated",
                context_signature=context_sig,
                relation_evidence_signature=relation_sig,
                skill_id=skill.id,
                key=skill.key,
                status="null",
                reason=relation_reason,
                binding=binding,
                trust_score=0.0,
                audit_pointer=audit,
            )

        surface = surface_for_skill(skill)
        option_prior = {
            "option_family": skill.O.get("option_family"),
            "relative_frame": skill.O.get("relative_frame"),
            "parameter_prior": skill.O.get("parameter_prior", {}),
        }
        geometric_readout = build_geometric_signal(
            skill,
            binding,
            relation_item=relation_item,
            context=context,
            now=read_time,
        )
        semantic_token = SemanticSkillToken(
            skill_id=skill.id,
            skill_name=str(skill.C.get("name", "")),
            relation_type=str(surface.get("relation_type", "unknown")),
            option_family=str(skill.O.get("option_family", "unknown_option")),
            effect_type=effect_type_from_contract(skill.C),
            binding=binding,
            grounding_features={
                "activation_score": geometric_readout.activation_score,
                "relation_kernel": surface.get("relation_kernel", {}),
                "geometric_feature_reducer": surface.get("geometric_feature_reducer", []),
            },
            posterior_summary={
                "support_n": skill.P.get("support_n", 0),
                "mean_progress_effect": skill.P.get("mean_progress_effect", 0.0),
                "effect_var": skill.P.get("effect_var", 0.0),
                "failure_histogram": skill.P.get("failure_histogram", {}),
                "contradiction_count": skill.P.get("contradiction_count", 0),
                "calibration_error": skill.P.get("calibration_error", 0.0),
            },
            trust_score=trust,
            scope=str(skill.Z.get("scope", "")),
            audit_pointer=audit,
        )
        safety_metadata = {
            "safety_constraints": skill.C.get("safety_constraints", []),
            "stop_predicates": skill.C.get("stop_predicates", []),
            "preconditions": skill.C.get("preconditions", []),
            "state": skill.state,
            "scope": skill.Z.get("scope"),
            "contradiction_count": skill.P.get("contradiction_count", 0),
            "critical_safety_failures": skill.P.get("critical_safety_failures", 0),
        }
        readout_id = readout_id_for(skill, "active", binding, context_sig, relation_sig)
        return ActiveSubskillReadout(
            readout_id=readout_id,
            generated_at=read_time,
            lifecycle_state="generated",
            context_signature=context_sig,
            relation_evidence_signature=relation_sig,
            skill_id=skill.id,
            key=skill.key,
            status="active",
            binding=binding,
            relation_type=str(surface.get("relation_type", "unknown")),
            activation_predicate=str(surface.get("activation_predicate", "")),
            geometric_readout=geometric_readout.to_dict(),
            semantic_token=semantic_token.to_dict(),
            option_prior=option_prior,
            expected_belief_effect=skill.O.get("expected_belief_effect", {}),
            trust_score=trust,
            safety_metadata=safety_metadata,
            audit_pointer=audit,
        )


def load_context(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SchemaError("context must be a JSON object")
    ensure_no_forbidden_payloads(payload)
    return canonicalize_context(payload)
