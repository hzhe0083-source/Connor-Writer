"""Append-only JSONL evidence ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .schema import EvidenceRecord, SchemaError, dumps_pretty


class EvidenceLedger:
    """File-backed evidence ledger.

    The ledger is append-only for new evidence ids and idempotent for repeated
    ids. It stores filtered execution summaries, not runtime skills.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.suffix == ".jsonl":
            self.file_path = self.path
        else:
            self.file_path = self.path / "evidence.jsonl"

    def append(self, record: EvidenceRecord | dict) -> str:
        if not isinstance(record, EvidenceRecord):
            record = EvidenceRecord.from_dict(record)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        existing_ids = {item.id for item in self.iter_records()}
        if record.id in existing_ids:
            return record.id
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=True))
            handle.write("\n")
        return record.id

    def iter_records(self) -> Iterable[EvidenceRecord]:
        if not self.file_path.exists():
            return []
        records: list[EvidenceRecord] = []
        with self.file_path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                    records.append(EvidenceRecord.from_dict(payload))
                except (json.JSONDecodeError, SchemaError) as exc:
                    raise SchemaError(f"{self.file_path}:{lineno}: {exc}") from exc
        return records

    def load_all(self) -> list[EvidenceRecord]:
        return list(self.iter_records())

    def write_json_snapshot(self, out_path: str | Path) -> None:
        """Write a pretty JSON snapshot for debugging or fixture review."""
        payload = [record.to_dict() for record in self.iter_records()]
        Path(out_path).write_text(dumps_pretty(payload), encoding="utf-8")


def load_jsonl_records(path: str | Path) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(EvidenceRecord.from_dict(json.loads(stripped)))
            except (json.JSONDecodeError, SchemaError) as exc:
                raise SchemaError(f"{path}:{lineno}: {exc}") from exc
    return records

