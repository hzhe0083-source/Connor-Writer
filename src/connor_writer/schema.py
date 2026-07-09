"""Schema objects for Connor-Writer.

The schema is intentionally plain dataclasses plus JSON-compatible dicts.  V1 is
deterministic and file-backed; no dense vectors, raw media, or trajectories are
accepted into the long-term skill lifecycle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


STATE_SEED = "seed"
STATE_DRAFT = "draft"
STATE_CERTIFIED = "certified"
STATE_SUPPRESSED = "suppressed"
VALID_STATES = {STATE_SEED, STATE_DRAFT, STATE_CERTIFIED, STATE_SUPPRESSED}

SCOPE_INSTANCE = "instance"
SCOPE_FAMILY = "family"
SCOPE_ROLE_GENERAL = "role_general"
VALID_SCOPES = {SCOPE_INSTANCE, SCOPE_FAMILY, SCOPE_ROLE_GENERAL}

FORBIDDEN_PAYLOAD_KEYS = {
    "raw_image",
    "raw_images",
    "image",
    "images",
    "image_path",
    "image_paths",
    "rgb",
    "frame",
    "frames",
    "video",
    "crop",
    "crops",
    "crop_path",
    "crop_paths",
    "feature",
    "features",
    "feature_vector",
    "feature_vectors",
    "dense_feature",
    "dense_features",
    "absolute_coordinate",
    "absolute_coordinates",
    "absolute_pixel",
    "absolute_pixel_target",
    "absolute_pixel_targets",
    "pixel_target",
    "pixel_targets",
    "trajectory",
    "trajectories",
    "action_trajectory",
    "low_level_trajectory",
}


class SchemaError(ValueError):
    """Raised when a lifecycle object violates the Connor-Writer schema."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or "unnamed"


def normalize_key_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def find_forbidden_payloads(value: Any, path: str = "$") -> list[str]:
    """Return JSON paths that contain forbidden long-term payload fields."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_name = normalize_key_name(str(key))
            child_path = f"{path}.{key}"
            if key_name in FORBIDDEN_PAYLOAD_KEYS:
                found.append(child_path)
            found.extend(find_forbidden_payloads(item, child_path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            found.extend(find_forbidden_payloads(item, f"{path}[{idx}]"))
    return found


def ensure_no_forbidden_payloads(value: Any) -> None:
    forbidden = find_forbidden_payloads(value)
    if forbidden:
        joined = ", ".join(forbidden)
        raise SchemaError(f"forbidden long-term payload fields: {joined}")


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{name} must be an object")
    return value


def require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaError(f"{name} must be a list")
    return value


def canonical_key(contract: dict[str, Any], operator: dict[str, Any] | None = None) -> str:
    roles = require_mapping(contract.get("roles", {}), "contract.roles")
    preconditions = contract.get("preconditions") or ["unknown"]
    if not isinstance(preconditions, list):
        raise SchemaError("contract.preconditions must be a list")
    pre_relation = str(preconditions[0]).split("(")[0]
    operator = operator or {}
    parts = [
        roles.get("anchor_family") or roles.get("anchor") or "any_anchor",
        pre_relation or "precondition",
        roles.get("target_family") or roles.get("target") or "any_target",
        contract.get("intended_effect") or "effect",
        contract.get("context_scope") or roles.get("context_scope") or "default",
        operator.get("option_family") or contract.get("option_family") or "option",
    ]
    return "|".join(slugify(str(part)) for part in parts)


@dataclass(slots=True)
class EvidenceRecord:
    """Filtered execution evidence. It is not runtime memory."""

    id: str
    timestamp: str
    source: str
    relation_traces: list[dict[str, Any]]
    vlm_branch_contract: dict[str, Any]
    bswm_predicted_delta_belief: dict[str, Any]
    observed_delta_belief: dict[str, Any]
    executed_relative_parameters: dict[str, Any]
    verifier_labels: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceRecord":
        payload = dict(payload)
        ensure_no_forbidden_payloads(payload)
        payload.setdefault("timestamp", utc_now())
        payload.setdefault("relation_traces", [])
        payload.setdefault("vlm_branch_contract", {})
        payload.setdefault("bswm_predicted_delta_belief", {})
        payload.setdefault("observed_delta_belief", {})
        payload.setdefault("executed_relative_parameters", {})
        payload.setdefault("verifier_labels", {})
        payload.setdefault("context", {})
        payload.setdefault("metadata", {})
        payload.setdefault("source", "unknown")
        for field_name in (
            "vlm_branch_contract",
            "bswm_predicted_delta_belief",
            "observed_delta_belief",
            "executed_relative_parameters",
            "verifier_labels",
            "context",
            "metadata",
        ):
            require_mapping(payload[field_name], field_name)
        require_list(payload["relation_traces"], "relation_traces")
        if not payload.get("id"):
            no_id = dict(payload)
            no_id.pop("id", None)
            payload["id"] = stable_id("ev", no_id)
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SkillDraft:
    id: str
    key: str
    state: str
    C: dict[str, Any]
    O: dict[str, Any]
    P: dict[str, Any]
    Z: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SkillDraft":
        ensure_no_forbidden_payloads(payload)
        if payload.get("state") not in VALID_STATES:
            raise SchemaError(f"invalid draft state: {payload.get('state')}")
        for name in ("C", "O", "P", "Z"):
            require_mapping(payload.get(name), name)
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PromotionRecord:
    id: str
    draft_id: str
    key: str
    passed: bool
    state: str
    scope: str
    checks: list[dict[str, Any]]
    reasons: list[str]
    timestamp: str
    version: int = 1

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PromotionRecord":
        ensure_no_forbidden_payloads(payload)
        if payload.get("state") not in VALID_STATES:
            raise SchemaError(f"invalid promotion state: {payload.get('state')}")
        if payload.get("scope") not in VALID_SCOPES:
            raise SchemaError(f"invalid promotion scope: {payload.get('scope')}")
        require_list(payload.get("checks"), "checks")
        require_list(payload.get("reasons"), "reasons")
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CertifiedSkill:
    id: str
    key: str
    state: str
    C: dict[str, Any]
    O: dict[str, Any]
    P: dict[str, Any]
    Z: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CertifiedSkill":
        ensure_no_forbidden_payloads(payload)
        if payload.get("state") not in VALID_STATES:
            raise SchemaError(f"invalid skill state: {payload.get('state')}")
        for name in ("C", "O", "P", "Z"):
            require_mapping(payload.get(name), name)
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GeometricSubskillSignal:
    relation_type: str
    anchor_slot: str | None
    target_slot: str | None
    activation_score: float
    relation_kernel: dict[str, Any]
    grounding_requirements: list[str]
    confidence_features: dict[str, Any]
    relative_option_prior: dict[str, Any]
    expected_belief_effect: dict[str, Any]
    audit_pointer: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GeometricSubskillSignal":
        ensure_no_forbidden_payloads(payload)
        for name in (
            "relation_kernel",
            "confidence_features",
            "relative_option_prior",
            "expected_belief_effect",
            "audit_pointer",
        ):
            require_mapping(payload.get(name), name)
        require_list(payload.get("grounding_requirements"), "grounding_requirements")
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SemanticSkillToken:
    skill_id: str
    skill_name: str
    relation_type: str
    option_family: str
    effect_type: str
    binding: dict[str, Any]
    grounding_features: dict[str, Any]
    posterior_summary: dict[str, Any]
    trust_score: float
    scope: str
    audit_pointer: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SemanticSkillToken":
        ensure_no_forbidden_payloads(payload)
        for name in (
            "binding",
            "grounding_features",
            "posterior_summary",
            "audit_pointer",
        ):
            require_mapping(payload.get(name), name)
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActiveSubskillReadout:
    skill_id: str
    key: str
    status: str
    binding: dict[str, Any]
    relation_type: str
    activation_predicate: str
    geometric_readout: dict[str, Any]
    semantic_token: dict[str, Any]
    option_prior: dict[str, Any]
    expected_belief_effect: dict[str, Any]
    trust_score: float
    safety_metadata: dict[str, Any]
    audit_pointer: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ActiveSubskillReadout":
        ensure_no_forbidden_payloads(payload)
        if payload.get("status") != "active":
            raise SchemaError(f"invalid active subskill status: {payload.get('status')}")
        for name in (
            "binding",
            "geometric_readout",
            "semantic_token",
            "option_prior",
            "expected_belief_effect",
            "safety_metadata",
            "audit_pointer",
        ):
            require_mapping(payload.get(name), name)
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NullSubskillReadout:
    skill_id: str
    key: str
    status: str
    reason: str
    binding: dict[str, Any]
    trust_score: float
    audit_pointer: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NullSubskillReadout":
        ensure_no_forbidden_payloads(payload)
        if payload.get("status") != "null":
            raise SchemaError(f"invalid null subskill status: {payload.get('status')}")
        for name in ("binding", "audit_pointer"):
            require_mapping(payload.get(name), name)
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SubskillReadout = ActiveSubskillReadout | NullSubskillReadout


def dumps_pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
