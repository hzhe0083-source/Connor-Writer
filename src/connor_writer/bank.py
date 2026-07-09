"""JSON-backed Certified Skill Bank."""

from __future__ import annotations

import json
from pathlib import Path

from .schema import (
    CertifiedSkill,
    PromotionRecord,
    SkillDraft,
    STATE_CERTIFIED,
    SchemaError,
    dumps_pretty,
    slugify,
    stable_id,
)


class CertifiedSkillBank:
    """File-backed bank of runtime-eligible certified skills."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def commit(self, draft: SkillDraft, promotion: PromotionRecord) -> CertifiedSkill:
        if not promotion.passed or promotion.state != STATE_CERTIFIED:
            raise SchemaError("cannot commit a draft that did not pass promotion")
        existing = self.get(draft.key)
        version = 1
        if existing is not None:
            version = int(existing.Z.get("version", 0)) + 1
        certificate = dict(draft.Z)
        audit_trail = list(certificate.get("audit_trail", []))
        audit_trail.append(
            {
                "event": "certified",
                "timestamp": promotion.timestamp,
                "promotion_id": promotion.id,
                "version": version,
            }
        )
        certificate.update(
            {
                "state": STATE_CERTIFIED,
                "scope": promotion.scope,
                "promotion_tests": promotion.checks,
                "promotion_id": promotion.id,
                "version": version,
                "audit_trail": audit_trail,
                "updated_at": promotion.timestamp,
            }
        )
        payload = {
            "key": draft.key,
            "state": STATE_CERTIFIED,
            "C": draft.C,
            "O": draft.O,
            "P": draft.P,
            "Z": certificate,
        }
        skill = CertifiedSkill(id=stable_id("skill", payload), **payload)
        self._path_for_key(skill.key, skill.C.get("name")).write_text(
            dumps_pretty(skill.to_dict()),
            encoding="utf-8",
        )
        return skill

    def get(self, key: str) -> CertifiedSkill | None:
        for skill in self.list():
            if skill.key == key:
                return skill
        return None

    def list(self) -> list[CertifiedSkill]:
        skills: list[CertifiedSkill] = []
        for path in sorted(self.path.glob("*.skill.json")):
            skills.append(load_skill(path))
        return skills

    def _path_for_key(self, key: str, name: str | None = None) -> Path:
        prefix = slugify(name or key.split("|")[0])
        return self.path / f"{prefix}.skill.json"


def load_skill(path: str | Path) -> CertifiedSkill:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{path}: invalid JSON: {exc}") from exc
    return CertifiedSkill.from_dict(payload)


def load_draft(path: str | Path) -> SkillDraft:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{path}: invalid JSON: {exc}") from exc
    return SkillDraft.from_dict(payload)
