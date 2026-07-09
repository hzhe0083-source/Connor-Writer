"""Runtime readout surfaces for certified skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bank import load_skill
from .schema import CertifiedSkill, SkillReadout, STATE_CERTIFIED, SchemaError
from .scoring import trust_score


class SkillReadoutBuilder:
    """Expose certified skill surfaces for DCEA, VLM, BSWM, and Q selection."""

    def build(
        self,
        skill: CertifiedSkill | str | Path,
        context: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> SkillReadout:
        if not isinstance(skill, CertifiedSkill):
            skill = load_skill(skill)
        if skill.state != STATE_CERTIFIED:
            raise SchemaError("only certified skills can produce runtime readouts")
        context = context or {}
        trust = trust_score(skill.P, now=now)
        semantic_token = {
            "skill_name": skill.C.get("name"),
            "roles": skill.C.get("roles", {}),
            "preconditions": skill.C.get("preconditions", []),
            "intended_effect": skill.C.get("intended_effect"),
            "scope": skill.Z.get("scope"),
            "trust": trust,
        }
        geometric_prior = {
            "relative_frame": skill.O.get("relative_frame"),
            "parameter_prior": skill.O.get("parameter_prior", {}),
            "object_bindings": context.get("object_bindings", {}),
            "applicability_model": skill.O.get("applicability_model", {}),
        }
        option_prior = {
            "option_family": skill.O.get("option_family"),
            "relative_frame": skill.O.get("relative_frame"),
            "parameter_prior": skill.O.get("parameter_prior", {}),
        }
        safety_metadata = {
            "safety_constraints": skill.C.get("safety_constraints", []),
            "state": skill.state,
            "scope": skill.Z.get("scope"),
            "contradiction_count": skill.P.get("contradiction_count", 0),
            "critical_safety_failures": skill.P.get("critical_safety_failures", 0),
        }
        audit_pointer = {
            "skill_id": skill.id,
            "key": skill.key,
            "version": skill.Z.get("version"),
            "promotion_id": skill.Z.get("promotion_id"),
            "evidence_ids": skill.Z.get("evidence_ids", []),
        }
        return SkillReadout(
            skill_id=skill.id,
            key=skill.key,
            semantic_token=semantic_token,
            geometric_prior=geometric_prior,
            option_prior=option_prior,
            expected_belief_effect=skill.O.get("expected_belief_effect", {}),
            trust_score=trust,
            safety_metadata=safety_metadata,
            audit_pointer=audit_pointer,
        )


def load_context(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SchemaError("context must be a JSON object")
    return payload

