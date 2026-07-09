from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from connor_writer.bank import CertifiedSkillBank
from connor_writer.context import canonicalize_context
from connor_writer.draft import SkillDraftBuilder
from connor_writer.gate import PromotionGate
from connor_writer.ledger import EvidenceLedger, load_jsonl_records
from connor_writer.readout import SkillReadoutBuilder
from connor_writer.readout_store import (
    OutcomeLedger,
    ReadoutLedger,
    evidence_from_outcome,
)
from connor_writer.schema import (
    EvidenceRecord,
    OutcomeRecord,
    STATE_CERTIFIED,
    STATE_DRAFT,
    STATE_SEED,
    SchemaError,
)
from connor_writer.scoring import success_lcb, trust_score


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_EVENTS = ROOT / "evidence" / "sample-events.jsonl"
SAMPLE_CONTEXT = ROOT / "examples" / "context.json"


class LifecycleTests(unittest.TestCase):
    def tempdir(self) -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory()

    def test_forbidden_payloads_are_rejected(self) -> None:
        payload = {
            "source": "simulator",
            "vlm_branch_contract": {"skill_name": "BadSkill"},
            "raw_image": "/tmp/frame.png",
        }
        with self.assertRaisesRegex(SchemaError, "forbidden"):
            EvidenceRecord.from_dict(payload)

    def test_context_canonicalization_is_stable(self) -> None:
        first = {
            "object_slots": [
                {
                    "slot": "obj_case_1",
                    "label": "Huawei earbud case",
                    "family": "goal_object",
                },
                {
                    "slot": "obj_bottle_1",
                    "label": "amber pump bottle",
                    "family": "context_object",
                },
            ],
            "object_bindings": {
                "target": {"slot": "obj_case_1", "label": "Huawei earbud case"},
                "anchor": {"slot": "obj_bottle_1", "label": "amber pump bottle"},
            },
            "schema": {
                "task": "tidy desktop by preparing a clear approach",
                "candidate_skill": "ClearApproach",
            },
            "provenance": {
                "source": "agent_visual_observation_from_user_message",
                "relation_evidence_policy": "do not infer active relation from the photo",
            },
            "relation_evidence": [
                {
                    "target": "obj_case_1",
                    "anchor": "obj_bottle_1",
                    "relation_type": "blocks",
                    "status": "observed",
                }
            ],
        }
        second = {
            "provenance": {"source": "agent_visual_observation_from_user_message"},
            "schema": {
                "candidate_skill": "ClearApproach",
                "task": "tidy desktop by preparing a clear approach",
            },
            "relation_evidence": [
                {
                    "relation_type": "blocks",
                    "anchor": "obj_bottle_1",
                    "target": "obj_case_1",
                    "status": "observed",
                }
            ],
            "object_bindings": {
                "anchor": {"slot": "obj_bottle_1", "label": "amber pump bottle"},
                "target": {"slot": "obj_case_1", "label": "Huawei earbud case"},
            },
            "object_slots": list(reversed(first["object_slots"])),
        }

        canonical_first = canonicalize_context(first)
        canonical_second = canonicalize_context(second)

        self.assertEqual(canonical_first, canonical_second)
        self.assertNotIn("candidate_skill", canonical_first["schema"])
        self.assertNotIn("provenance", canonical_first)
        self.assertTrue(canonical_first["relation_evidence"][0]["evidence_id"].startswith("rel_"))
        self.assertEqual(
            canonical_first["object_bindings"]["anchor"]["family"],
            "context_object",
        )
        rewritten = canonicalize_context(first, rewrite_slots=True)
        self.assertEqual(
            rewritten["object_bindings"]["anchor"]["slot"],
            "context_object.amber_pump_bottle_1",
        )
        self.assertEqual(
            rewritten["relation_evidence"][0]["anchor"],
            "context_object.amber_pump_bottle_1",
        )

    def test_ledger_append_is_idempotent_and_stable(self) -> None:
        with self.tempdir() as tmp:
            tmp_path = Path(tmp)
            record = load_jsonl_records(SAMPLE_EVENTS)[0]
            ledger = EvidenceLedger(tmp_path / "ledger")
            first_id = ledger.append(record)
            second_id = ledger.append(record)
            self.assertEqual(first_id, second_id)
            lines = (tmp_path / "ledger" / "evidence.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(ledger.load_all()[0].id, record.id)

    def test_repeated_compatible_evidence_updates_one_draft(self) -> None:
        with self.tempdir() as tmp:
            tmp_path = Path(tmp)
            ledger = EvidenceLedger(tmp_path / "ledger")
            for record in load_jsonl_records(SAMPLE_EVENTS):
                ledger.append(record)

            drafts = SkillDraftBuilder().build(ledger)
            names = {draft.C["name"]: draft for draft in drafts}

            self.assertEqual(set(names), {"AlignAndRelease", "ClearApproach", "RecoverGrasp"})
            clear = names["ClearApproach"]
            self.assertEqual(clear.state, STATE_DRAFT)
            self.assertNotIn("G", clear.to_dict())
            self.assertEqual(clear.P["support_n"], 3)
            self.assertEqual(len(clear.Z["evidence_ids"]), 4)

    def test_vlm_only_evidence_creates_seed_not_certified(self) -> None:
        seed = load_jsonl_records(SAMPLE_EVENTS)[0]
        drafts = SkillDraftBuilder().build([seed])
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].state, STATE_SEED)

        promotion = PromotionGate().evaluate(drafts[0])
        self.assertFalse(promotion.passed)
        self.assertEqual(promotion.state, STATE_SEED)

    def test_promotion_and_scope_rules(self) -> None:
        with self.tempdir() as tmp:
            tmp_path = Path(tmp)
            ledger = EvidenceLedger(tmp_path / "ledger")
            for record in load_jsonl_records(SAMPLE_EVENTS):
                ledger.append(record)
            drafts = SkillDraftBuilder().build(ledger)
            clear = next(draft for draft in drafts if draft.C["name"] == "ClearApproach")
            align = next(draft for draft in drafts if draft.C["name"] == "AlignAndRelease")
            recover = next(draft for draft in drafts if draft.C["name"] == "RecoverGrasp")

            gate = PromotionGate()
            clear_promotion = gate.evaluate(clear)
            align_promotion = gate.evaluate(align)
            recover_promotion = gate.evaluate(recover)

            self.assertTrue(clear_promotion.passed)
            self.assertEqual(clear_promotion.state, STATE_CERTIFIED)
            self.assertEqual(clear_promotion.scope, "role_general")
            self.assertFalse(align_promotion.passed)
            self.assertEqual(align_promotion.state, STATE_DRAFT)
            self.assertFalse(recover_promotion.passed)
            self.assertEqual(recover_promotion.state, "suppressed")

    def test_bank_round_trip_and_readout(self) -> None:
        with self.tempdir() as tmp:
            tmp_path = Path(tmp)
            ledger = EvidenceLedger(tmp_path / "ledger")
            for record in load_jsonl_records(SAMPLE_EVENTS):
                ledger.append(record)
            clear = next(
                draft for draft in SkillDraftBuilder().build(ledger) if draft.C["name"] == "ClearApproach"
            )
            promotion = PromotionGate().evaluate(clear)
            bank = CertifiedSkillBank(tmp_path / "skills")
            skill = bank.commit(clear, promotion)

            loaded = bank.get(skill.key)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.to_dict(), skill.to_dict())

            context = json.loads(SAMPLE_CONTEXT.read_text(encoding="utf-8"))
            readout = SkillReadoutBuilder().build(
                loaded, context=context, now="2026-07-09T00:03:30+00:00"
            )
            payload = readout.to_dict()
            self.assertEqual(
                set(payload),
                {
                    "readout_id",
                    "generated_at",
                    "lifecycle_state",
                    "context_signature",
                    "relation_evidence_signature",
                    "skill_id",
                    "key",
                    "status",
                    "binding",
                    "relation_type",
                    "activation_predicate",
                    "geometric_readout",
                    "semantic_token",
                    "option_prior",
                    "expected_belief_effect",
                    "trust_score",
                    "safety_metadata",
                    "audit_pointer",
                },
            )
            self.assertEqual(payload["status"], "active")
            self.assertTrue(payload["readout_id"].startswith("ro_"))
            self.assertEqual(payload["lifecycle_state"], "generated")
            self.assertTrue(payload["context_signature"].startswith("ctx_"))
            self.assertTrue(payload["relation_evidence_signature"].startswith("rel_"))
            self.assertEqual(payload["relation_type"], "blocks")
            self.assertEqual(payload["semantic_token"]["skill_name"], "ClearApproach")
            self.assertEqual(payload["semantic_token"]["relation_type"], "blocks")
            self.assertEqual(payload["binding"]["anchor"]["label"], "tray")
            self.assertEqual(payload["geometric_readout"]["anchor_slot"], "obj_3")
            self.assertEqual(payload["geometric_readout"]["target_slot"], "obj_7")
            self.assertEqual(payload["geometric_readout"]["relation_type"], "blocks")
            self.assertIn("relation_kernel", payload["geometric_readout"])
            self.assertEqual(
                payload["geometric_readout"]["confidence_features"]["relation_evidence"][
                    "source"
                ],
                "example_fixture",
            )
            self.assertEqual(payload["option_prior"]["option_family"], "displace_context")
            self.assertGreater(payload["expected_belief_effect"]["progress_delta"], 0)
            self.assertGreater(payload["trust_score"], 0)
            self.assertTrue(payload["audit_pointer"]["evidence_ids"])

            null_readout = SkillReadoutBuilder().build(loaded, context={}, now="2026-07-09T00:03:30+00:00")
            null_payload = null_readout.to_dict()
            self.assertEqual(null_payload["status"], "null")
            self.assertTrue(null_payload["readout_id"].startswith("ro_"))
            self.assertIn("missing bound anchor", null_payload["reason"])

            manual_context = {
                "object_bindings": {
                    "anchor": {"slot": "obj_manual_anchor", "label": "manual anchor"},
                    "target": {"slot": "obj_manual_target", "label": "manual target"},
                },
                "schema": {"task": "manual binding smoke test"},
            }
            manual_readout = SkillReadoutBuilder().build(
                loaded, context=manual_context, now="2026-07-09T00:03:30+00:00"
            ).to_dict()
            self.assertEqual(manual_readout["status"], "null")
            self.assertIn("missing relation evidence", manual_readout["reason"])

            strict_context = {
                "object_bindings": {
                    "anchor": {"slot": "obj_manual_anchor", "label": "manual anchor"},
                    "target": {"slot": "obj_manual_target", "label": "manual target"},
                },
                "relation_evidence": [
                    {
                        "relation_type": "blocks",
                        "anchor": "obj_manual_anchor",
                        "target": "obj_manual_target",
                        "source": "manual_test_fixture",
                        "status": "observed",
                    }
                ],
                "schema": {"task": "manual binding smoke test"},
            }
            manual_readout = SkillReadoutBuilder().build(
                loaded, context=strict_context, now="2026-07-09T00:03:30+00:00"
            ).to_dict()
            self.assertEqual(manual_readout["status"], "active")
            self.assertEqual(
                manual_readout["geometric_readout"]["confidence_features"][
                    "binding_confidence_source"
                ],
                "unavailable",
            )
            self.assertIsNone(
                manual_readout["geometric_readout"]["confidence_features"][
                    "binding_quality_used"
                ]
            )

    def test_readout_persistence_and_outcome_update(self) -> None:
        with self.tempdir() as tmp:
            tmp_path = Path(tmp)
            ledger = EvidenceLedger(tmp_path / "ledger")
            for record in load_jsonl_records(SAMPLE_EVENTS):
                ledger.append(record)
            clear = next(
                draft for draft in SkillDraftBuilder().build(ledger) if draft.C["name"] == "ClearApproach"
            )
            promotion = PromotionGate().evaluate(clear)
            skill = CertifiedSkillBank(tmp_path / "skills").commit(clear, promotion)
            context = json.loads(SAMPLE_CONTEXT.read_text(encoding="utf-8"))
            readout = SkillReadoutBuilder().build(
                skill, context=context, now="2026-07-09T00:03:30+00:00"
            )

            readouts = ReadoutLedger(tmp_path / "readouts")
            first_id = readouts.append(readout)
            second_id = readouts.append(readout)
            self.assertEqual(first_id, readout.readout_id)
            self.assertEqual(first_id, second_id)
            self.assertIsNotNone(readouts.get(first_id))

            outcome = OutcomeRecord.from_dict(
                {
                    "readout_id": first_id,
                    "timestamp": "2026-07-09T00:04:00+00:00",
                    "source": "verifier",
                    "success": True,
                    "observed_delta_belief": {
                        "blocks": -1.0,
                        "reachable": 1.0,
                        "progress_delta": 0.9,
                    },
                    "executed_relative_parameters": {
                        "relative_frame": "anchor_to_target",
                        "push_distance": 0.2,
                    },
                    "verifier_labels": {"progress_delta": 0.9},
                }
            )
            outcomes = OutcomeLedger(tmp_path / "outcomes")
            outcomes.append(outcome)
            evidence = evidence_from_outcome(readout, outcome, skill)
            self.assertEqual(evidence.metadata["outcome_id"], outcome.id)
            ledger.append(evidence)

            updated_clear = next(
                draft for draft in SkillDraftBuilder().build(ledger) if draft.C["name"] == "ClearApproach"
            )
            self.assertEqual(updated_clear.P["support_n"], 4)

    def test_suppressed_skill_cannot_produce_readout(self) -> None:
        with self.tempdir() as tmp:
            tmp_path = Path(tmp)
            ledger = EvidenceLedger(tmp_path / "ledger")
            for record in load_jsonl_records(SAMPLE_EVENTS):
                ledger.append(record)
            recover = next(
                draft for draft in SkillDraftBuilder().build(ledger) if draft.C["name"] == "RecoverGrasp"
            )
            promotion = PromotionGate().evaluate(recover)
            self.assertEqual(promotion.state, "suppressed")
            with self.assertRaises(SchemaError):
                CertifiedSkillBank(tmp_path / "skills").commit(recover, promotion)

    def test_scoring_is_deterministic(self) -> None:
        self.assertEqual(round(success_lcb(4, 1), 4), round(success_lcb(4, 1), 4))
        posterior = {
            "alpha_success": 4,
            "beta_failure": 1,
            "last_verified": "2026-07-09T00:00:00+00:00",
            "freshness_decay": 86400.0,
            "contradiction_count": 0,
            "calibration_error": 0.1,
        }
        self.assertGreater(trust_score(posterior, now="2026-07-09T00:01:00+00:00"), 0)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {"PYTHONPATH": str(ROOT / "src")}
        return subprocess.run(
            [sys.executable, "-m", "connor_writer", *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_cli_lifecycle_end_to_end(self) -> None:
        with self.tempdir() as tmp:
            tmp_path = Path(tmp)
            ledger = tmp_path / "ledger"
            drafts = tmp_path / "drafts"
            skills = tmp_path / "skills"

            self.run_cli("validate", str(ROOT / "evidence"))
            self.run_cli("ingest", str(SAMPLE_EVENTS), "--ledger", str(ledger))
            self.run_cli("draft", "--ledger", str(ledger), "--drafts", str(drafts))
            self.run_cli("promote", str(drafts), "--bank", str(skills))

            skill_path = skills / "clearapproach.skill.json"
            self.assertTrue(skill_path.exists())

            listing = self.run_cli("list", "--bank", str(skills))
            self.assertIn("ClearApproach", listing.stdout)

            readout = self.run_cli(
                "readout",
                str(skill_path),
                "--context",
                str(SAMPLE_CONTEXT),
            )
            payload = json.loads(readout.stdout)
            self.assertEqual(payload["status"], "active")
            self.assertEqual(payload["semantic_token"]["relation_type"], "blocks")
            self.assertEqual(payload["geometric_readout"]["relation_type"], "blocks")

            compiled = self.run_cli("compile-context", str(SAMPLE_CONTEXT))
            compiled_payload = json.loads(compiled.stdout)
            self.assertIn("relation_evidence", compiled_payload)
            self.assertEqual(compiled_payload["relation_evidence"][0]["status"], "observed")
            self.assertNotIn("active_relation", compiled_payload["schema"])


if __name__ == "__main__":
    unittest.main()
