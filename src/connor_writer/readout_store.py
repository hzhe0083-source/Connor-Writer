"""Persistent readout and outcome ledgers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .schema import (
    ActiveSubskillReadout,
    CertifiedSkill,
    EvidenceRecord,
    OutcomeRecord,
    SchemaError,
    SubskillReadout,
    canonical_json,
    parse_subskill_readout,
)


class ReadoutLedger:
    """Append-only ledger of generated runtime subskill readouts."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.suffix == ".jsonl":
            self.file_path = self.path
        else:
            self.file_path = self.path / "readouts.jsonl"

    def append(self, readout: SubskillReadout | dict) -> str:
        if isinstance(readout, dict):
            readout = parse_subskill_readout(readout)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        for existing in self.iter_records():
            if existing.readout_id != readout.readout_id:
                continue
            if canonical_json(existing.to_dict()) != canonical_json(readout.to_dict()):
                raise SchemaError(
                    f"conflicting readout content for stable id: {readout.readout_id}"
                )
            return readout.readout_id
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(readout.to_dict(), sort_keys=True, ensure_ascii=True))
            handle.write("\n")
        return readout.readout_id

    def iter_records(self) -> Iterable[SubskillReadout]:
        if not self.file_path.exists():
            return []
        records: list[SubskillReadout] = []
        with self.file_path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(parse_subskill_readout(json.loads(stripped)))
                except (json.JSONDecodeError, SchemaError) as exc:
                    raise SchemaError(f"{self.file_path}:{lineno}: {exc}") from exc
        return records

    def get(self, readout_id: str) -> SubskillReadout | None:
        for record in self.iter_records():
            if record.readout_id == readout_id:
                return record
        return None


class OutcomeLedger:
    """Append-only ledger of execution outcomes tied to readout ids."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.suffix == ".jsonl":
            self.file_path = self.path
        else:
            self.file_path = self.path / "outcomes.jsonl"

    def append(self, outcome: OutcomeRecord | dict) -> str:
        if not isinstance(outcome, OutcomeRecord):
            outcome = OutcomeRecord.from_dict(outcome)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        existing_ids = {item.id for item in self.iter_records()}
        if outcome.id in existing_ids:
            return outcome.id
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome.to_dict(), sort_keys=True, ensure_ascii=True))
            handle.write("\n")
        return outcome.id

    def iter_records(self) -> Iterable[OutcomeRecord]:
        if not self.file_path.exists():
            return []
        records: list[OutcomeRecord] = []
        with self.file_path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(OutcomeRecord.from_dict(json.loads(stripped)))
                except (json.JSONDecodeError, SchemaError) as exc:
                    raise SchemaError(f"{self.file_path}:{lineno}: {exc}") from exc
        return records


def evidence_from_outcome(
    readout: SubskillReadout,
    outcome: OutcomeRecord,
    skill: CertifiedSkill,
) -> EvidenceRecord:
    """Convert a persisted readout outcome into evidence for skill updates."""
    if not isinstance(readout, ActiveSubskillReadout):
        raise SchemaError("only active readouts can produce execution evidence")
    if readout.readout_id != outcome.readout_id:
        raise SchemaError("outcome readout_id does not match readout")

    relation_item = (
        readout.geometric_readout.get("confidence_features", {}).get("relation_evidence", {})
    )
    contract = dict(skill.C)
    contract.setdefault("skill_name", skill.C.get("name"))
    contract["option_family"] = skill.O.get("option_family")
    contract["relative_frame"] = skill.O.get("relative_frame")
    verifier_labels = dict(outcome.verifier_labels)
    verifier_labels["success"] = outcome.success
    verifier_labels["executed"] = True
    payload = {
        "timestamp": outcome.timestamp,
        "source": outcome.source,
        "relation_traces": [relation_item] if relation_item else [],
        "vlm_branch_contract": contract,
        "bswm_predicted_delta_belief": readout.expected_belief_effect,
        "observed_delta_belief": outcome.observed_delta_belief,
        "executed_relative_parameters": outcome.executed_relative_parameters,
        "verifier_labels": verifier_labels,
        "context": {
            "readout_id": readout.readout_id,
            "context_signature": readout.context_signature,
            "relation_evidence_signature": readout.relation_evidence_signature,
        },
        "metadata": {
            "outcome_id": outcome.id,
            "skill_id": skill.id,
            "skill_version": skill.Z.get("version"),
            "readout_lifecycle_state": readout.lifecycle_state,
        },
    }
    return EvidenceRecord.from_dict(payload)
