"""Build non-runtime skill drafts from evidence records."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .ledger import EvidenceLedger
from .schema import (
    EvidenceRecord,
    SkillDraft,
    STATE_DRAFT,
    STATE_SEED,
    canonical_key,
    dumps_pretty,
    slugify,
    stable_id,
    utc_now,
)
from .scoring import mean, variance


EXECUTION_SOURCES = {"execution", "verifier", "probe", "simulator", "perceived", "teleop"}
WEAK_SOURCES = {"vlm", "instruction", "perception", "unknown"}


def is_execution_evidence(record: EvidenceRecord) -> bool:
    labels = record.verifier_labels
    if labels.get("executed") is True:
        return True
    if record.source.lower() in EXECUTION_SOURCES:
        return True
    return any(key in labels for key in ("success", "progress_delta", "failure_mode", "risk"))


def numeric_value(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def infer_progress(record: EvidenceRecord) -> float:
    labels = record.verifier_labels
    observed = record.observed_delta_belief
    value = numeric_value(labels, "progress_delta", "progress", "effect_size")
    if value is None:
        value = numeric_value(observed, "progress_delta", "progress", "effect_size")
    if value is None and labels.get("success") is True:
        return 1.0
    if value is None and labels.get("success") is False:
        return 0.0
    return float(value or 0.0)


def infer_predicted_progress(record: EvidenceRecord) -> float | None:
    return numeric_value(
        record.bswm_predicted_delta_belief,
        "progress_delta",
        "progress",
        "effect_size",
    )


def merge_contract(records: list[EvidenceRecord]) -> dict[str, Any]:
    first = records[0].vlm_branch_contract
    return {
        "name": first.get("skill_name") or first.get("name") or "UnnamedSkill",
        "roles": first.get("roles", {}),
        "preconditions": first.get("preconditions", []),
        "intended_effect": first.get("intended_effect", ""),
        "stop_predicates": first.get("stop_predicates", []),
        "safety_constraints": first.get("safety_constraints", []),
        "invariance": first.get("invariance", []),
        "context_scope": first.get("context_scope", "default"),
        "notation": {"C_k": "tau_k"},
    }


def merge_operator(records: list[EvidenceRecord], progress_values: list[float]) -> dict[str, Any]:
    first = records[0].vlm_branch_contract
    option_family = first.get("option_family") or first.get("mode") or "unknown_option"
    frames = Counter()
    numeric_params: dict[str, list[float]] = defaultdict(list)
    categorical_params: dict[str, Counter] = defaultdict(Counter)
    expected_effect: dict[str, float] = {}
    for record in records:
        params = record.executed_relative_parameters
        frame = params.get("relative_frame") or first.get("relative_frame") or "anchor_to_target"
        frames[str(frame)] += 1
        for key, value in params.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_params[key].append(float(value))
            elif isinstance(value, str):
                categorical_params[key][value] += 1
        for key, value in record.observed_delta_belief.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                expected_effect.setdefault(key, 0.0)
    for key in list(expected_effect):
        vals = [
            float(record.observed_delta_belief[key])
            for record in records
            if isinstance(record.observed_delta_belief.get(key), (int, float))
            and not isinstance(record.observed_delta_belief.get(key), bool)
        ]
        expected_effect[key] = mean(vals)
    if progress_values:
        expected_effect.setdefault("progress_delta", mean(progress_values))
    param_prior = {
        "numeric_mean": {key: mean(values) for key, values in numeric_params.items()},
        "numeric_var": {key: variance(values) for key, values in numeric_params.items()},
        "categorical_mode": {
            key: counts.most_common(1)[0][0] for key, counts in categorical_params.items() if counts
        },
    }
    return {
        "option_family": option_family,
        "relative_frame": frames.most_common(1)[0][0] if frames else "anchor_to_target",
        "parameter_prior": param_prior,
        "expected_belief_effect": expected_effect,
        "applicability_model": {
            "preconditions": first.get("preconditions", []),
            "kind": "deterministic_predicate_gate",
        },
        "notation": {"O_k": "Omega_k"},
    }


def summarize_context(records: list[EvidenceRecord]) -> dict[str, Any]:
    buckets = Counter()
    object_instances = set()
    object_families = set()
    layouts = set()
    viewpoints = set()
    held_out_transfer_passes = 0
    for record in records:
        context = record.context
        if context.get("context_bucket"):
            buckets[str(context["context_bucket"])] += 1
        if context.get("object_instance"):
            object_instances.add(str(context["object_instance"]))
        for family in context.get("object_families", []) or []:
            object_families.add(str(family))
        if context.get("layout_id"):
            layouts.add(str(context["layout_id"]))
        if context.get("viewpoint_id"):
            viewpoints.add(str(context["viewpoint_id"]))
        if record.verifier_labels.get("held_out_transfer") and record.verifier_labels.get("success"):
            held_out_transfer_passes += 1
    return {
        "context_buckets": dict(buckets),
        "object_instance_count": len(object_instances),
        "object_family_count": len(object_families),
        "layout_count": len(layouts),
        "viewpoint_count": len(viewpoints),
        "held_out_transfer_passes": held_out_transfer_passes,
    }


def build_posterior(records: list[EvidenceRecord]) -> dict[str, Any]:
    execution_records = [record for record in records if is_execution_evidence(record)]
    successes = sum(1 for record in execution_records if record.verifier_labels.get("success") is True)
    failures = sum(1 for record in execution_records if record.verifier_labels.get("success") is False)
    progress_values = [infer_progress(record) for record in execution_records]
    failure_histogram = Counter(
        str(record.verifier_labels.get("failure_mode"))
        for record in execution_records
        if record.verifier_labels.get("failure_mode")
    )
    contradiction_count = sum(
        1 for record in execution_records if record.verifier_labels.get("contradiction") is True
    )
    critical_safety_failures = sum(
        1
        for record in execution_records
        if record.verifier_labels.get("critical_safety_failure") is True
        or record.verifier_labels.get("failure_mode") == "critical_safety"
    )
    calibration_errors: list[float] = []
    for record in execution_records:
        predicted = infer_predicted_progress(record)
        if predicted is not None:
            calibration_errors.append(abs(predicted - infer_progress(record)))
    source_counts = Counter(record.source.lower() for record in records)
    last_verified = max((record.timestamp for record in execution_records), default=None)
    return {
        "support_n": len(execution_records),
        "alpha_success": 1 + successes,
        "beta_failure": 1 + failures,
        "mean_progress_effect": mean(progress_values),
        "effect_mean": mean(progress_values),
        "effect_var": variance(progress_values),
        "failure_histogram": dict(failure_histogram),
        "contradiction_count": contradiction_count,
        "critical_safety_failures": critical_safety_failures,
        "calibration_error": mean(calibration_errors),
        "last_verified": last_verified,
        "freshness_decay": 86_400.0,
        "source_counts": dict(source_counts),
        "context_summary": summarize_context(records),
        "notation": {"P_k": "eta_k"},
    }


class SkillDraftBuilder:
    """Aggregate evidence into non-runtime skill drafts."""

    def build(self, ledger: EvidenceLedger | list[EvidenceRecord]) -> list[SkillDraft]:
        records = ledger.load_all() if isinstance(ledger, EvidenceLedger) else list(ledger)
        grouped: dict[str, list[EvidenceRecord]] = defaultdict(list)
        for record in records:
            contract = record.vlm_branch_contract
            key = canonical_key(contract, contract)
            grouped[key].append(record)

        drafts: list[SkillDraft] = []
        for key, group in sorted(grouped.items()):
            contract = merge_contract(group)
            progress_values = [infer_progress(record) for record in group if is_execution_evidence(record)]
            operator = merge_operator(group, progress_values)
            posterior = build_posterior(group)
            has_execution = posterior["support_n"] > 0
            state = STATE_DRAFT if has_execution else STATE_SEED
            evidence_ids = sorted({record.id for record in group})
            built_at = max((record.timestamp for record in group), default=utc_now())
            certificate = {
                "state": state,
                "scope": "instance",
                "evidence_ids": evidence_ids,
                "promotion_tests": [],
                "version": 0,
                "audit_trail": [
                    {
                        "event": "draft_built",
                        "timestamp": built_at,
                        "evidence_count": len(evidence_ids),
                    }
                ],
                "notation": {"Z_k": "promotion_certificate"},
            }
            draft_payload = {
                "key": key,
                "state": state,
                "C": contract,
                "O": operator,
                "P": posterior,
                "Z": certificate,
            }
            draft_id = stable_id("draft", draft_payload)
            drafts.append(SkillDraft(id=draft_id, **draft_payload))
        return drafts

    def write(self, drafts: list[SkillDraft], out_dir: str | Path) -> list[Path]:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for draft in drafts:
            filename = f"{slugify(draft.C.get('name', draft.key))}.draft.json"
            path = out_path / filename
            path.write_text(dumps_pretty(draft.to_dict()), encoding="utf-8")
            written.append(path)
        return written
