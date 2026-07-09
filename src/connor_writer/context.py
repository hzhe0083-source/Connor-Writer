"""Deterministic current-scene context canonicalization.

Connor-Writer does not perform visual grounding.  This module only takes
already-extracted current-scene facts and normalizes them into the narrow
schema consumed by runtime readout generation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .schema import SchemaError, ensure_no_forbidden_payloads, slugify, stable_id


VALID_FAMILIES = {
    "container",
    "context_object",
    "goal_object",
    "goal_region",
    "loose_object",
    "manipulated_object",
    "support_surface",
    "target_object",
    "workspace_boundary",
}

VALID_RELATIONS = {
    "at",
    "blocks",
    "contains",
    "inside",
    "near",
    "not_inside",
    "occludes",
    "on",
    "reachable",
    "supports",
}

VALID_RELATION_STATUSES = {"observed", "asserted", "verified", "negated"}

SCHEMA_KEYS = ("task_id", "task", "source")
OBJECT_KEYS = ("slot", "label", "family", "class", "brand")
BINDING_KEYS = ("slot", "label", "family", "class", "brand", "binding_confidence")
RELATION_KEYS = ("evidence_id", "relation_type", "anchor", "target", "source", "status")


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SchemaError("context must be a JSON object")
    return payload


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def canonicalize_context(payload: dict[str, Any], rewrite_slots: bool = False) -> dict[str, Any]:
    """Return a stable, readout-safe current-scene context.

    Free-form fields such as ``candidate_skill`` and policy prose are not
    carried into the canonical context.  Relation evidence remains explicit:
    the compiler never infers relations from object bindings.
    """
    if not isinstance(payload, dict):
        raise SchemaError("context must be a JSON object")
    ensure_no_forbidden_payloads(payload)

    source = _context_source(payload)
    raw_objects = _raw_objects(payload)
    objects, slot_map = _canonical_objects(raw_objects, rewrite_slots=rewrite_slots)
    bindings = _canonical_bindings(
        payload.get("object_bindings", {}),
        objects=objects,
        slot_map=slot_map,
    )
    relation_evidence = _canonical_relation_evidence(
        payload.get("relation_evidence", _schema_relation_evidence(payload)),
        slot_map=slot_map,
        default_source=source,
    )

    context: dict[str, Any] = {}
    schema = _canonical_schema(payload.get("schema", {}), source)
    if schema:
        context["schema"] = schema
    if objects:
        context["objects"] = objects
    if bindings:
        context["object_bindings"] = bindings
    if relation_evidence:
        context["relation_evidence"] = relation_evidence
    return context


def _context_source(payload: dict[str, Any]) -> str | None:
    schema = payload.get("schema")
    if isinstance(schema, dict) and isinstance(schema.get("source"), str):
        return _token(schema["source"])
    provenance = payload.get("provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("source"), str):
        return _token(provenance["source"])
    return None


def _schema_relation_evidence(payload: dict[str, Any]) -> Any:
    schema = payload.get("schema")
    if isinstance(schema, dict):
        return schema.get("relation_evidence")
    return None


def _canonical_schema(value: Any, source: str | None) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise SchemaError("context.schema must be an object")
    result: dict[str, Any] = {}
    if isinstance(value.get("task_id"), str) and value["task_id"].strip():
        result["task_id"] = _token(value["task_id"])
    elif isinstance(value.get("task"), str) and value["task"].strip():
        result["task_id"] = slugify(value["task"]).replace("-", "_")
    if isinstance(value.get("task"), str) and value["task"].strip():
        result["task"] = _clean_text(value["task"])
    if source:
        result["source"] = source
    return {key: result[key] for key in SCHEMA_KEYS if key in result}


def _raw_objects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("objects", payload.get("object_slots", []))
    if isinstance(value, dict):
        objects = []
        for slot, item in value.items():
            if not isinstance(item, dict):
                raise SchemaError("context.objects values must be objects")
            merged = dict(item)
            merged.setdefault("slot", slot)
            objects.append(merged)
        return objects
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaError("context.objects/object_slots must be a list or object")
    objects = []
    for item in value:
        if not isinstance(item, dict):
            raise SchemaError("context object entries must be objects")
        objects.append(dict(item))
    return objects


def _canonical_objects(
    raw_objects: list[dict[str, Any]],
    rewrite_slots: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    normalized = [_canonical_object(item) for item in raw_objects]
    normalized.sort(key=lambda item: (_sort_family(item), item.get("class", ""), item.get("label", ""), item["slot"]))

    slot_map: dict[str, str] = {}
    if rewrite_slots:
        counters: dict[str, int] = {}
        for item in normalized:
            old_slot = item["slot"]
            prefix = item.get("family") or "object"
            class_name = item.get("class") or item.get("label") or "object"
            base = f"{_token(prefix)}.{slugify(class_name).replace('-', '_')}"
            counters[base] = counters.get(base, 0) + 1
            item["slot"] = f"{base}_{counters[base]}"
            slot_map[old_slot] = item["slot"]
    else:
        slot_map = {item["slot"]: item["slot"] for item in normalized}

    return {item["slot"]: {key: item[key] for key in OBJECT_KEYS if key in item} for item in normalized}, slot_map


def _sort_family(item: dict[str, Any]) -> str:
    return str(item.get("family") or "")


def _canonical_object(item: dict[str, Any]) -> dict[str, Any]:
    slot = _required_text(item, "slot", "context object")
    family = _optional_token(item.get("family"))
    if family and family not in VALID_FAMILIES:
        raise SchemaError(f"unknown object family: {family}")
    result: dict[str, Any] = {"slot": _slot_token(slot)}
    label = _optional_clean_text(item.get("label"))
    if label:
        result["label"] = label
    if family:
        result["family"] = family
    class_name = _optional_token(item.get("class") or item.get("type"))
    if class_name:
        result["class"] = class_name
    brand = _optional_clean_text(item.get("brand"))
    if brand:
        result["brand"] = brand
    return result


def _canonical_bindings(
    value: Any,
    objects: dict[str, dict[str, Any]],
    slot_map: dict[str, str],
) -> dict[str, dict[str, Any]]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise SchemaError("context.object_bindings must be an object")
    result: dict[str, dict[str, Any]] = {}
    for role in sorted(value):
        role_key = _token(role)
        binding = value[role]
        if not isinstance(binding, dict):
            raise SchemaError(f"context.object_bindings.{role} must be an object")
        slot = _required_text(binding, "slot", f"context.object_bindings.{role}")
        remapped_slot = slot_map.get(_slot_token(slot), _slot_token(slot))
        merged = dict(objects.get(remapped_slot, {}))
        for key in BINDING_KEYS:
            if key in binding:
                merged[key] = binding[key]
        merged["slot"] = remapped_slot
        normalized = _canonical_binding(merged, role_key)
        result[role_key] = normalized
    return result


def _canonical_binding(item: dict[str, Any], role: str) -> dict[str, Any]:
    result = _canonical_object(item)
    confidence = item.get("binding_confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise SchemaError(f"context.object_bindings.{role}.binding_confidence must be a number")
        result["binding_confidence"] = max(0.0, min(1.0, float(confidence)))
    return {key: result[key] for key in BINDING_KEYS if key in result}


def _canonical_relation_evidence(
    value: Any,
    slot_map: dict[str, str],
    default_source: str | None,
) -> list[dict[str, Any]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise SchemaError("context.relation_evidence must be a list")
    result = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise SchemaError(f"context.relation_evidence[{idx}] must be an object")
        relation_type = _token(_required_text(item, "relation_type", f"context.relation_evidence[{idx}]"))
        if relation_type not in VALID_RELATIONS:
            raise SchemaError(f"unknown relation_type: {relation_type}")
        anchor = _slot_token(_required_text(item, "anchor", f"context.relation_evidence[{idx}]"))
        target = _slot_token(_required_text(item, "target", f"context.relation_evidence[{idx}]"))
        source = _optional_token(item.get("source")) or default_source
        if not source:
            raise SchemaError(f"context.relation_evidence[{idx}].source is required")
        status = _optional_token(item.get("status")) or "observed"
        if status not in VALID_RELATION_STATUSES:
            raise SchemaError(f"unknown relation status: {status}")
        relation = {
            "relation_type": relation_type,
            "anchor": slot_map.get(anchor, anchor),
            "target": slot_map.get(target, target),
            "source": source,
            "status": status,
        }
        relation["evidence_id"] = stable_id("rel", relation)
        result.append({key: relation[key] for key in RELATION_KEYS})
    result.sort(key=lambda item: (item["relation_type"], item["anchor"], item["target"], item["source"], item["status"]))
    return result


def _required_text(item: dict[str, Any], key: str, parent: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{parent}.{key} is required")
    return value


def _optional_token(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _token(value)


def _optional_clean_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _clean_text(value)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip()).strip("_.-").lower()
    token = re.sub(r"_+", "_", token)
    return token or "unknown"


def _slot_token(value: str) -> str:
    return _token(value)
