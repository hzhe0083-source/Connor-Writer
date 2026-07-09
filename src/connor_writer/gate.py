"""Deterministic promotion gate for skill drafts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import (
    PromotionRecord,
    SkillDraft,
    STATE_CERTIFIED,
    STATE_DRAFT,
    STATE_SEED,
    STATE_SUPPRESSED,
    SCOPE_FAMILY,
    SCOPE_INSTANCE,
    SCOPE_ROLE_GENERAL,
    SchemaError,
    dumps_pretty,
    find_forbidden_payloads,
    stable_id,
    utc_now,
)
from .scoring import success_lcb


@dataclass(frozen=True, slots=True)
class GateConfig:
    min_support: int = 3
    success_lcb_threshold: float = 0.40
    max_contradictions: int = 0


class PromotionGate:
    def __init__(self, config: GateConfig | None = None):
        self.config = config or GateConfig()

    def evaluate(self, draft: SkillDraft | dict[str, Any]) -> PromotionRecord:
        if not isinstance(draft, SkillDraft):
            draft = SkillDraft.from_dict(draft)
        checks: list[dict[str, Any]] = []
        reasons: list[str] = []

        def add(name: str, passed: bool, detail: str = "") -> None:
            checks.append({"name": name, "passed": passed, "detail": detail})
            if not passed:
                reasons.append(f"{name}: {detail}")

        add("schema_valid", self._schema_valid(draft), "draft dataclass and required fields")
        forbidden = find_forbidden_payloads(draft.to_dict())
        add("no_forbidden_payload", not forbidden, ", ".join(forbidden))
        support = int(draft.P.get("support_n", 0))
        add("has_execution_evidence", support > 0, f"support_n={support}")
        add(
            "minimum_support",
            support >= self.config.min_support,
            f"support_n={support}, min={self.config.min_support}",
        )
        lcb = success_lcb(float(draft.P.get("alpha_success", 1.0)), float(draft.P.get("beta_failure", 1.0)))
        add(
            "success_lcb_threshold",
            lcb >= self.config.success_lcb_threshold,
            f"lcb={lcb:.4f}, threshold={self.config.success_lcb_threshold:.4f}",
        )
        progress = float(draft.P.get("mean_progress_effect", 0.0))
        add("positive_progress_effect", progress > 0.0, f"mean_progress_effect={progress:.4f}")
        contradictions = int(draft.P.get("contradiction_count", 0))
        add(
            "contradiction_threshold",
            contradictions <= self.config.max_contradictions,
            f"contradictions={contradictions}, max={self.config.max_contradictions}",
        )
        safety_failures = int(draft.P.get("critical_safety_failures", 0))
        failure_hist = draft.P.get("failure_histogram", {})
        critical_hist = int(failure_hist.get("critical_safety", 0)) if isinstance(failure_hist, dict) else 0
        add(
            "no_critical_safety_failure",
            safety_failures == 0 and critical_hist == 0,
            f"critical_safety_failures={safety_failures + critical_hist}",
        )
        add("readout_contract_valid", self._readout_contract_valid(draft), "runtime surfaces available")
        add("audit_complete", self._audit_complete(draft), "evidence ids and audit trail present")

        passed = all(check["passed"] for check in checks)
        if passed:
            state = STATE_CERTIFIED
        elif safety_failures or critical_hist or contradictions > self.config.max_contradictions:
            state = STATE_SUPPRESSED
        elif support == 0:
            state = STATE_SEED
        else:
            state = STATE_DRAFT
        scope = self._scope(draft)
        timestamp = draft.P.get("last_verified") or utc_now()
        payload = {
            "draft_id": draft.id,
            "key": draft.key,
            "passed": passed,
            "state": state,
            "scope": scope,
            "checks": checks,
            "reasons": reasons,
            "timestamp": timestamp,
            "version": 1,
        }
        return PromotionRecord(id=stable_id("promo", payload), **payload)

    def write(self, record: PromotionRecord, out_dir: str | Path) -> Path:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        out_file = path / f"{record.key.replace('|', '-')}.promotion.json"
        out_file.write_text(dumps_pretty(record.to_dict()), encoding="utf-8")
        return out_file

    def _schema_valid(self, draft: SkillDraft) -> bool:
        try:
            SkillDraft.from_dict(draft.to_dict())
        except SchemaError:
            return False
        return True

    def _readout_contract_valid(self, draft: SkillDraft) -> bool:
        if not draft.C.get("intended_effect"):
            return False
        if not draft.O.get("option_family"):
            return False
        if not isinstance(draft.O.get("expected_belief_effect"), dict):
            return False
        if not draft.C.get("safety_constraints"):
            return False
        return True

    def _audit_complete(self, draft: SkillDraft) -> bool:
        evidence_ids = draft.Z.get("evidence_ids")
        audit_trail = draft.Z.get("audit_trail")
        return bool(evidence_ids) and bool(audit_trail)

    def _scope(self, draft: SkillDraft) -> str:
        summary = draft.P.get("context_summary", {})
        if not isinstance(summary, dict):
            return SCOPE_INSTANCE
        object_families = int(summary.get("object_family_count", 0))
        layouts = int(summary.get("layout_count", 0))
        viewpoints = int(summary.get("viewpoint_count", 0))
        held_out = int(summary.get("held_out_transfer_passes", 0))
        instances = int(summary.get("object_instance_count", 0))
        if object_families >= 2 and layouts >= 2 and viewpoints >= 2 and held_out >= 1:
            return SCOPE_ROLE_GENERAL
        if instances >= 2 or layouts >= 2:
            return SCOPE_FAMILY
        return SCOPE_INSTANCE
