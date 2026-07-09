"""Runtime readout surfaces for certified skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bank import load_skill
from .families import effect_type_from_contract
from .grounding import audit_pointer, build_dcea_input, resolve_binding
from .schema import (
    ActiveSubskillReadout,
    CertifiedSkill,
    NullSubskillReadout,
    STATE_CERTIFIED,
    SchemaError,
    SemanticSkillToken,
    SubskillReadout,
    utc_now,
)
from .scoring import trust_score


class SkillReadoutBuilder:
    """Expose certified skill surfaces for DCEA, VLM, BSWM, and Q selection."""

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
        context = context or {}
        read_time = now or utc_now()
        trust = trust_score(skill.P, now=read_time)
        binding, null_reason = resolve_binding(skill, context)
        audit = audit_pointer(skill)
        if null_reason:
            return NullSubskillReadout(
                skill_id=skill.id,
                key=skill.key,
                status="null",
                reason=null_reason,
                binding=binding,
                trust_score=0.0,
                audit_pointer=audit,
            )

        option_prior = {
            "option_family": skill.O.get("option_family"),
            "relative_frame": skill.O.get("relative_frame"),
            "parameter_prior": skill.O.get("parameter_prior", {}),
        }
        dcea_input = build_dcea_input(skill, binding, context=context, now=read_time)
        semantic_token = SemanticSkillToken(
            skill_id=skill.id,
            skill_name=str(skill.C.get("name", "")),
            relation_type=str(skill.G.get("relation_type", "unknown")),
            option_family=str(skill.O.get("option_family", "unknown_option")),
            effect_type=effect_type_from_contract(skill.C),
            binding=binding,
            grounding_features={
                "activation_score": dcea_input.activation_score,
                "relation_kernel": skill.G.get("relation_kernel", {}),
                "geometric_feature_reducer": skill.G.get("geometric_feature_reducer", []),
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
        return ActiveSubskillReadout(
            skill_id=skill.id,
            key=skill.key,
            status="active",
            binding=binding,
            relation_type=str(skill.G.get("relation_type", "unknown")),
            activation_predicate=str(skill.G.get("activation_predicate", "")),
            dcea_input=dcea_input.to_dict(),
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
    return payload
