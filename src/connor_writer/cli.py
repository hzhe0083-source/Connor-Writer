"""Command line interface for Connor-Writer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .bank import CertifiedSkillBank, load_draft, load_skill
from .context import canonicalize_context, load_json as load_context_json, write_json
from .draft import SkillDraftBuilder
from .gate import PromotionGate
from .ledger import EvidenceLedger, load_jsonl_records
from .readout import SkillReadoutBuilder, load_context
from .readout_store import (
    OutcomeLedger,
    ReadoutLedger,
    evidence_from_outcome,
)
from .schema import (
    CertifiedSkill,
    EvidenceRecord,
    OutcomeRecord,
    PromotionRecord,
    SchemaError,
    SkillDraft,
    dumps_pretty,
    ensure_no_forbidden_payloads,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="connor-writer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate evidence, drafts, or skill JSON")
    p_validate.add_argument("path")

    p_compile_context = sub.add_parser(
        "compile-context",
        help="canonicalize current-scene context JSON for stable readout ids",
    )
    p_compile_context.add_argument("context")
    p_compile_context.add_argument("--out", help="write canonical context JSON to this path")
    p_compile_context.add_argument(
        "--rewrite-slots",
        action="store_true",
        help="replace supplied object slot ids with deterministic family/class ids",
    )

    p_ingest = sub.add_parser("ingest", help="ingest evidence JSONL into an append-only ledger")
    p_ingest.add_argument("evidence_jsonl")
    p_ingest.add_argument("--ledger", required=True)

    p_draft = sub.add_parser("draft", help="build non-runtime skill drafts from a ledger")
    p_draft.add_argument("--ledger", required=True)
    p_draft.add_argument("--drafts", required=True)

    p_promote = sub.add_parser("promote", help="run deterministic promotion gates over drafts")
    p_promote.add_argument("drafts")
    p_promote.add_argument("--bank", required=True)
    p_promote.add_argument("--promotions")

    p_list = sub.add_parser("list", help="list certified skills")
    p_list.add_argument("--bank", required=True)

    p_show = sub.add_parser("show", help="show a skill/draft/promotion JSON file")
    p_show.add_argument("path")

    p_readout = sub.add_parser("readout", help="build runtime readout for a certified skill")
    p_readout.add_argument("skill")
    p_readout.add_argument("--context")
    p_readout.add_argument("--readouts", help="append generated readout to this readout ledger")

    p_outcome = sub.add_parser("outcome", help="record execution outcome for a persisted readout")
    p_outcome.add_argument("readouts")
    p_outcome.add_argument("--readout-id", required=True)
    p_outcome.add_argument("--skill", required=True)
    p_outcome.add_argument("--ledger", required=True)
    p_outcome.add_argument("--outcomes", required=True)
    p_outcome.add_argument("--success", choices=("true", "false"), required=True)
    p_outcome.add_argument("--observed", default="{}")
    p_outcome.add_argument("--executed", default="{}")
    p_outcome.add_argument("--labels", default="{}")
    p_outcome.add_argument("--source", default="execution")

    p_audit = sub.add_parser("audit", help="show certificate audit trail for a skill")
    p_audit.add_argument("skill")
    return parser


def cmd_validate(path: str) -> int:
    target = Path(path)
    files: list[Path]
    if target.is_dir():
        files = sorted(
            item
            for item in target.rglob("*")
            if item.suffix in {".json", ".jsonl"} and item.is_file()
        )
    else:
        files = [target]
    errors: list[str] = []
    for file_path in files:
        try:
            if file_path.suffix == ".jsonl":
                load_jsonl_records(file_path)
            else:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                ensure_no_forbidden_payloads(payload)
                _validate_json_object(payload)
        except Exception as exc:  # noqa: BLE001 - CLI should report all validation errors.
            errors.append(f"{file_path}: {exc}")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"validated {len(files)} file(s)")
    return 0


def _validate_json_object(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise SchemaError("JSON file must contain an object")
    if {"source", "vlm_branch_contract"}.issubset(payload):
        EvidenceRecord.from_dict(payload)
    elif {"C", "O", "P", "Z", "state"}.issubset(payload) and payload.get("state") == "certified":
        CertifiedSkill.from_dict(payload)
    elif {"C", "O", "P", "Z", "state"}.issubset(payload):
        SkillDraft.from_dict(payload)
    elif {"checks", "passed", "scope"}.issubset(payload):
        PromotionRecord.from_dict(payload)
    elif {"readout_id", "status", "skill_id"}.issubset(payload):
        from .schema import parse_subskill_readout

        parse_subskill_readout(payload)
    elif {"readout_id", "success", "observed_delta_belief"}.issubset(payload):
        OutcomeRecord.from_dict(payload)
    elif _looks_like_context(payload):
        canonicalize_context(payload)


def _looks_like_context(payload: dict[str, Any]) -> bool:
    return bool(
        {"object_slots", "objects", "object_bindings", "relation_evidence", "schema", "provenance"}
        & set(payload)
    )


def cmd_compile_context(context_path: str, out_path: str | None, rewrite_slots: bool) -> int:
    payload = load_context_json(context_path)
    canonical = canonicalize_context(payload, rewrite_slots=rewrite_slots)
    if out_path:
        write_json(out_path, canonical)
    print(dumps_pretty(canonical), end="")
    return 0


def cmd_ingest(evidence_jsonl: str, ledger_path: str) -> int:
    ledger = EvidenceLedger(ledger_path)
    count = 0
    for record in load_jsonl_records(evidence_jsonl):
        ledger.append(record)
        count += 1
    print(f"ingested {count} evidence record(s) into {ledger.file_path}")
    return 0


def cmd_draft(ledger_path: str, drafts_path: str) -> int:
    ledger = EvidenceLedger(ledger_path)
    builder = SkillDraftBuilder()
    drafts = builder.build(ledger)
    written = builder.write(drafts, drafts_path)
    print(f"wrote {len(written)} draft(s) to {drafts_path}")
    return 0


def cmd_promote(drafts_path: str, bank_path: str, promotions_path: str | None = None) -> int:
    drafts_dir = Path(drafts_path)
    bank = CertifiedSkillBank(bank_path)
    gate = PromotionGate()
    promotions_dir = Path(promotions_path) if promotions_path else Path(bank_path).parent / "promotions"
    committed = 0
    evaluated = 0
    for draft_file in sorted(drafts_dir.glob("*.draft.json")):
        draft = load_draft(draft_file)
        record = gate.evaluate(draft)
        gate.write(record, promotions_dir)
        evaluated += 1
        if record.passed:
            bank.commit(draft, record)
            committed += 1
    print(f"evaluated {evaluated} draft(s); certified {committed}; promotions in {promotions_dir}")
    return 0


def cmd_list(bank_path: str) -> int:
    bank = CertifiedSkillBank(bank_path)
    for skill in bank.list():
        print(
            f"{skill.C.get('name')} | {skill.key} | "
            f"scope={skill.Z.get('scope')} | version={skill.Z.get('version')}"
        )
    return 0


def cmd_show(path: str) -> int:
    print(Path(path).read_text(encoding="utf-8"), end="")
    return 0


def cmd_readout(skill_path: str, context_path: str | None, readouts_path: str | None = None) -> int:
    context = load_context(context_path)
    readout = SkillReadoutBuilder().build(skill_path, context=context)
    if readouts_path:
        ReadoutLedger(readouts_path).append(readout)
    print(dumps_pretty(readout.to_dict()), end="")
    return 0


def parse_json_arg(value: str, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{name} must be JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaError(f"{name} must be a JSON object")
    ensure_no_forbidden_payloads(payload)
    return payload


def cmd_outcome(
    readouts_path: str,
    readout_id: str,
    skill_path: str,
    ledger_path: str,
    outcomes_path: str,
    success: str,
    observed: str,
    executed: str,
    labels: str,
    source: str,
) -> int:
    readout = ReadoutLedger(readouts_path).get(readout_id)
    if readout is None:
        raise SchemaError(f"readout not found: {readout_id}")
    skill = load_skill(skill_path)
    outcome = OutcomeRecord.from_dict(
        {
            "readout_id": readout_id,
            "source": source,
            "success": success == "true",
            "observed_delta_belief": parse_json_arg(observed, "--observed"),
            "executed_relative_parameters": parse_json_arg(executed, "--executed"),
            "verifier_labels": parse_json_arg(labels, "--labels"),
        }
    )
    OutcomeLedger(outcomes_path).append(outcome)
    evidence = evidence_from_outcome(readout, outcome, skill)
    evidence_id = EvidenceLedger(ledger_path).append(evidence)
    print(
        dumps_pretty(
            {
                "outcome_id": outcome.id,
                "readout_id": readout_id,
                "evidence_id": evidence_id,
                "ledger": str(EvidenceLedger(ledger_path).file_path),
                "outcomes": str(OutcomeLedger(outcomes_path).file_path),
            }
        ),
        end="",
    )
    return 0


def cmd_audit(skill_path: str) -> int:
    skill = load_skill(skill_path)
    print(dumps_pretty({"skill_id": skill.id, "key": skill.key, "certificate": skill.Z}), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return cmd_validate(args.path)
        if args.command == "compile-context":
            return cmd_compile_context(args.context, args.out, args.rewrite_slots)
        if args.command == "ingest":
            return cmd_ingest(args.evidence_jsonl, args.ledger)
        if args.command == "draft":
            return cmd_draft(args.ledger, args.drafts)
        if args.command == "promote":
            return cmd_promote(args.drafts, args.bank, args.promotions)
        if args.command == "list":
            return cmd_list(args.bank)
        if args.command == "show":
            return cmd_show(args.path)
        if args.command == "readout":
            return cmd_readout(args.skill, args.context, args.readouts)
        if args.command == "outcome":
            return cmd_outcome(
                args.readouts,
                args.readout_id,
                args.skill,
                args.ledger,
                args.outcomes,
                args.success,
                args.observed,
                args.executed,
                args.labels,
                args.source,
            )
        if args.command == "audit":
            return cmd_audit(args.skill)
    except SchemaError as exc:
        print(f"schema error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
