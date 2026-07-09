"""Known option-family profiles for runtime subskill readouts."""

from __future__ import annotations

from typing import Any


FAMILY_SPECS: dict[str, dict[str, Any]] = {
    "displace_context": {
        "relation_kernel": {
            "name": "boundary_contact_away_from_corridor",
            "inputs": ["anchor_mask", "target_region", "approach_corridor"],
            "output": "anchor-relative relation evidence",
        },
        "geometric_feature_reducer": [
            "peak",
            "centroid",
            "covariance",
            "corridor_clearance",
            "boundary_side",
        ],
        "null_grounding_policy": "null_if_anchor_or_target_unbound",
    },
    "align_release": {
        "relation_kernel": {
            "name": "target_to_goal_release_region",
            "inputs": ["anchor_mask", "target_region", "goal_axis"],
            "output": "target-relative release evidence",
        },
        "geometric_feature_reducer": [
            "goal_centroid",
            "axis_alignment",
            "inside_probability",
            "release_region_spread",
        ],
        "null_grounding_policy": "null_if_anchor_or_target_unbound",
    },
    "recover_grasp": {
        "relation_kernel": {
            "name": "target_regrasp_region",
            "inputs": ["anchor_mask", "gripper_state", "reachable_boundary"],
            "output": "target-relative recovery evidence",
        },
        "geometric_feature_reducer": [
            "target_centroid",
            "reachable_boundary",
            "grasp_axis",
            "slip_risk",
        ],
        "null_grounding_policy": "null_if_anchor_unbound",
    },
}


def relation_type_from_contract(contract: dict[str, Any]) -> str:
    preconditions = contract.get("preconditions") or []
    if not preconditions:
        return "unknown"
    relation = str(preconditions[0]).split("(")[0].strip().lower()
    relation = relation.replace(" ", "_")
    if relation == "not held":
        return "not_held"
    return relation or "unknown"


def effect_type_from_contract(contract: dict[str, Any]) -> str:
    effect = str(contract.get("intended_effect") or "unknown_effect")
    return effect.split(";")[0].strip().lower().replace(" ", "_")


def build_subskill_surface(contract: dict[str, Any], operator: dict[str, Any]) -> dict[str, Any]:
    option_family = str(operator.get("option_family") or "unknown_option")
    spec = FAMILY_SPECS.get(
        option_family,
        {
            "relation_kernel": {
                "name": "generic_anchor_target_relation",
                "inputs": ["anchor_mask", "target_mask"],
                "output": "generic relation evidence",
            },
            "geometric_feature_reducer": ["peak", "centroid", "covariance"],
            "null_grounding_policy": "null_if_required_roles_unbound",
        },
    )
    roles = contract.get("roles", {})
    anchor_role = str(roles.get("anchor", "anchor"))
    target_role = str(roles.get("target", "target"))
    relation_type = relation_type_from_contract(contract)
    activation_predicate = (
        f"bound({anchor_role}) and bound({target_role}) "
        f"and active({relation_type})"
    )
    return {
        "relation_signature": f"{relation_type}({anchor_role},{target_role})",
        "relation_type": relation_type,
        "activation_predicate": activation_predicate,
        "anchor_role": anchor_role,
        "target_role": target_role,
        "grounding_requirements": [
            f"bound({anchor_role})",
            f"bound({target_role})",
            "current_scene_object_slots",
        ],
        "relation_kernel": spec["relation_kernel"],
        "geometric_feature_reducer": spec["geometric_feature_reducer"],
        "null_grounding_policy": spec["null_grounding_policy"],
        "notation": {"surface": "runtime_subskill_projection"},
    }
