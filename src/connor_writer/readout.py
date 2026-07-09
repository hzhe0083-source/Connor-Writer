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
from .scoring import success_lcb, success_mean, trust_score
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
    generated_at: str,
    reason: str | None = None,
) -> str:
    return stable_id(
        "ro",
        {
            "skill_id": skill.id,
            "skill_version": skill.Z.get("version"),
            "generated_at": generated_at,
            "status": status,
            "binding": binding,
            "context_signature": context_sig,
            "relation_evidence_signature": relation_sig,
            "reason": reason,
        },
    )


def option_prior_for(skill: CertifiedSkill) -> dict[str, Any]:
    return {
        "option_family": skill.O.get("option_family"),
        "relative_frame": skill.O.get("relative_frame"),
        "parameter_prior": skill.O.get("parameter_prior", {}),
    }


def posterior_summary_for(skill: CertifiedSkill, trust: float) -> dict[str, Any]:
    alpha = float(skill.P.get("alpha_success", 1.0))
    beta = float(skill.P.get("beta_failure", 1.0))
    return {
        "support_n": skill.P.get("support_n", 0),
        "alpha_success": alpha,
        "beta_failure": beta,
        "success_mean": success_mean(alpha, beta),
        "success_lcb": success_lcb(alpha, beta),
        "mean_progress_effect": skill.P.get("mean_progress_effect", 0.0),
        "effect_var": skill.P.get("effect_var", 0.0),
        "failure_histogram": skill.P.get("failure_histogram", {}),
        "contradiction_count": skill.P.get("contradiction_count", 0),
        "critical_safety_failures": skill.P.get("critical_safety_failures", 0),
        "calibration_error": skill.P.get("calibration_error", 0.0),
        "last_verified": skill.P.get("last_verified"),
        "source_counts": skill.P.get("source_counts", {}),
        "context_summary": skill.P.get("context_summary", {}),
        "trust_score": trust,
    }


def experience_readout_for(
    skill: CertifiedSkill,
    *,
    trust: float,
    gate_status: str,
    gate_reason: str | None = None,
    surface: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the compact past-experience surface carried by every readout."""
    surface = surface or surface_for_skill(skill)
    anchor_role = str(surface.get("anchor_role", "anchor"))
    target_role = str(surface.get("target_role", "target"))
    relation_type = str(surface.get("relation_type", "unknown"))
    return {
        "memory_role": "certified_skill_experience_summary",
        "skill_id": skill.id,
        "skill_name": str(skill.C.get("name", "")),
        "branch_mode": str(skill.O.get("option_family", "unknown_option")),
        "relation_type": relation_type,
        "effect_type": effect_type_from_contract(skill.C),
        "applicability": {
            "preconditions": skill.C.get("preconditions", []),
            "activation_predicate": str(surface.get("activation_predicate", "")),
            "required_relation_evidence": {
                "relation_type": relation_type,
                "anchor_role": anchor_role,
                "target_role": target_role,
                "source_required": True,
            },
            "grounding_requirements": surface.get("grounding_requirements", []),
        },
        "option_prior": option_prior_for(skill),
        "expected_belief_effect": skill.O.get("expected_belief_effect", {}),
        "outcome_prior": posterior_summary_for(skill, trust),
        "safety": {
            "safety_constraints": skill.C.get("safety_constraints", []),
            "stop_predicates": skill.C.get("stop_predicates", []),
            "critical_safety_failures": skill.P.get("critical_safety_failures", 0),
            "contradiction_count": skill.P.get("contradiction_count", 0),
        },
        "current_scene_gate": {
            "status": gate_status,
            "reason": gate_reason,
        },
        "scope": str(skill.Z.get("scope", "")),
        "audit_pointer": audit_pointer(skill),
    }


def bswm_input_for(
    skill: CertifiedSkill,
    *,
    readout_id: str,
    generated_at: str,
    context_signature: str,
    relation_evidence_signature: str | None,
    status: str,
    binding: dict[str, Any],
    experience_readout: dict[str, Any],
    trust: float,
    gate_reason: str | None = None,
    relation_item: dict[str, Any] | None = None,
    geometric_readout: dict[str, Any] | None = None,
    semantic_token: dict[str, Any] | None = None,
    option_prior: dict[str, Any] | None = None,
    expected_belief_effect: dict[str, Any] | None = None,
    safety_metadata: dict[str, Any] | None = None,
    surface: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable BSWM consumption surface for a persisted readout.

    ``experience_readout`` is the past-skill summary. ``bswm_input`` is the
    read-time contract that tells Connor-0 which parts may enter belief
    construction, which parts may become active skill tokens, and which storage
    identity must be used for outcome writeback.
    """
    surface = surface or surface_for_skill(skill)
    relation_type = str(surface.get("relation_type", "unknown"))
    anchor_role = str(surface.get("anchor_role", "anchor"))
    target_role = str(surface.get("target_role", "target"))
    is_active = status == "active"
    option_prior = option_prior if option_prior is not None else option_prior_for(skill)
    expected_belief_effect = (
        expected_belief_effect if expected_belief_effect is not None else skill.O.get("expected_belief_effect", {})
    )
    safety_metadata = safety_metadata or {
        "safety_constraints": skill.C.get("safety_constraints", []),
        "stop_predicates": skill.C.get("stop_predicates", []),
        "preconditions": skill.C.get("preconditions", []),
        "state": skill.state,
        "scope": skill.Z.get("scope"),
        "contradiction_count": skill.P.get("contradiction_count", 0),
        "critical_safety_failures": skill.P.get("critical_safety_failures", 0),
    }
    payload: dict[str, Any] = {
        "schema_version": "connor_writer.bswm_input.v1",
        "consumer": "connor0.bswm",
        "state_role": "planning_sufficient_evidence_conditioned_belief_input",
        "readout_ref": {
            "readout_id": readout_id,
            "generated_at": generated_at,
            "status": status,
            "skill_id": skill.id,
            "skill_key": skill.key,
            "skill_version": skill.Z.get("version"),
            "context_signature": context_signature,
            "relation_evidence_signature": relation_evidence_signature,
        },
        "storage_contract": {
            "readout_ledger": "append_only",
            "outcome_writeback_key": "readout_id",
            "same_readout_id_requires_same_content": True,
            "certified_skill_mutation": "forbidden",
        },
        "current_scene_gate": {
            "status": status,
            "reason": gate_reason,
            "ready_for_bswm": is_active,
            "active_routes": {
                "belief_evidence": is_active,
                "skill_token": is_active,
                "audit_only": not is_active,
            },
        },
        "belief_evidence": {
            "route": "geometric",
            "object_bindings": binding,
            "required_relation_evidence": {
                "relation_type": relation_type,
                "anchor_role": anchor_role,
                "target_role": target_role,
                "source_required": True,
            },
            "relation_evidence": relation_item or {},
            "geometric_readout": geometric_readout or {},
            "evidence_policy": {
                "explicit_relation_evidence_required": True,
                "no_raw_media_or_trajectory_payloads": True,
                "current_scene_only": True,
            },
        },
        "skill_conditioning": {
            "route": "semantic",
            "semantic_token": semantic_token or {},
            "experience_readout": experience_readout,
            "historical_trust_score": trust,
            "current_scene_trust_score": trust if is_active else 0.0,
        },
        "branch_conditioning": {
            "branch_mode": str(skill.O.get("option_family", "unknown_option")),
            "effect_type": effect_type_from_contract(skill.C),
            "option_prior": option_prior,
            "expected_belief_effect": expected_belief_effect,
            "safety_metadata": safety_metadata,
        },
        "audit_pointer": audit_pointer(skill),
    }
    payload["bswm_input_id"] = stable_id("bswm", payload)
    return payload


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
        surface = surface_for_skill(skill)
        binding, null_reason = resolve_binding(skill, context)
        audit = audit_pointer(skill)
        if null_reason:
            readout_id = readout_id_for(
                skill,
                "null",
                binding,
                context_sig,
                None,
                read_time,
                null_reason,
            )
            experience = experience_readout_for(
                skill,
                trust=trust,
                gate_status="null",
                gate_reason=null_reason,
                surface=surface,
            )
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
                experience_readout=experience,
                bswm_input=bswm_input_for(
                    skill,
                    readout_id=readout_id,
                    generated_at=read_time,
                    context_signature=context_sig,
                    relation_evidence_signature=None,
                    status="null",
                    binding=binding,
                    experience_readout=experience,
                    trust=trust,
                    surface=surface,
                    gate_reason=null_reason,
                ),
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
                read_time,
                relation_reason,
            )
            experience = experience_readout_for(
                skill,
                trust=trust,
                gate_status="null",
                gate_reason=relation_reason,
                surface=surface,
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
                experience_readout=experience,
                bswm_input=bswm_input_for(
                    skill,
                    readout_id=readout_id,
                    generated_at=read_time,
                    context_signature=context_sig,
                    relation_evidence_signature=relation_sig,
                    status="null",
                    binding=binding,
                    experience_readout=experience,
                    trust=trust,
                    surface=surface,
                    gate_reason=relation_reason,
                    relation_item=relation_item,
                ),
            )

        option_prior = option_prior_for(skill)
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
        readout_id = readout_id_for(
            skill,
            "active",
            binding,
            context_sig,
            relation_sig,
            read_time,
        )
        experience = experience_readout_for(
            skill,
            trust=trust,
            gate_status="active",
            surface=surface,
        )
        semantic_payload = semantic_token.to_dict()
        geometric_payload = geometric_readout.to_dict()
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
            geometric_readout=geometric_payload,
            semantic_token=semantic_payload,
            option_prior=option_prior,
            expected_belief_effect=skill.O.get("expected_belief_effect", {}),
            trust_score=trust,
            safety_metadata=safety_metadata,
            audit_pointer=audit,
            experience_readout=experience,
            bswm_input=bswm_input_for(
                skill,
                readout_id=readout_id,
                generated_at=read_time,
                context_signature=context_sig,
                relation_evidence_signature=relation_sig,
                status="active",
                binding=binding,
                experience_readout=experience,
                trust=trust,
                surface=surface,
                relation_item=relation_item,
                geometric_readout=geometric_payload,
                semantic_token=semantic_payload,
                option_prior=option_prior,
                expected_belief_effect=skill.O.get("expected_belief_effect", {}),
                safety_metadata=safety_metadata,
            ),
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
