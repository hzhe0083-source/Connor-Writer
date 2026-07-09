# Connor-Writer

Connor-Writer v1 is a deterministic skill distillation and certification lifecycle for the Connor Certified Skill Bank.

It is not a neural self-improvement system, not an LLM judge, and not a memory logger. It turns validated execution traces into certified branch-level skill operators. The long-term skill files are interface-agnostic; current-scene subskill readouts are generated only at read time.

```text
Execution Trace
  -> EvidenceRecord
  -> SkillDraft
  -> PromotionRecord
  -> CertifiedSkill
  -> persisted ActiveSubskillReadout / NullSubskillReadout
  -> OutcomeRecord
  -> EvidenceRecord
```

Core rule:

```text
No trace directly mutates a certified skill.
Evidence updates the ledger.
Drafts aggregate evidence.
Promotion gates decide runtime eligibility.
```

## Certified Skill

```text
CertifiedSkill S_k = (C_k, O_k, P_k, Z_k)

C_k: contract
  roles / preconditions / intended effect / stop / safety / invariance

O_k: option-effect operator
  applicability -> relative option prior -> expected belief change

P_k: competence posterior
  support / success / progress / failure / uncertainty / calibration / freshness

Z_k: certificate
  evidence ids / promotion tests / scope / state / version / audit trail
```

Connor-0 notation mapping:

```text
C_k = tau_k
O_k = Omega_k
P_k = eta_k
Z_k = promotion certificate
```

## Lifecycle

Run from the repository root:

```bash
PYTHONPATH=src python3 -m connor_writer validate evidence/
PYTHONPATH=src python3 -m connor_writer compile-context examples/context.json --out /tmp/connor-context.json
PYTHONPATH=src python3 -m connor_writer ingest evidence/sample-events.jsonl --ledger ledger/
PYTHONPATH=src python3 -m connor_writer draft --ledger ledger/ --drafts drafts/
PYTHONPATH=src python3 -m connor_writer promote drafts/ --bank skills/
PYTHONPATH=src python3 -m connor_writer list --bank skills/
PYTHONPATH=src python3 -m connor_writer show skills/clearapproach.skill.json
PYTHONPATH=src python3 -m connor_writer readout skills/clearapproach.skill.json --context /tmp/connor-context.json --readouts readouts/
PYTHONPATH=src python3 -m connor_writer outcome readouts/ --readout-id <ro_id> --skill skills/clearapproach.skill.json --ledger ledger/ --outcomes outcomes/ --success true --observed '{"progress_delta": 0.8}' --executed '{}' --labels '{}'
PYTHONPATH=src python3 -m connor_writer audit skills/clearapproach.skill.json
```

If installed as a package, the same commands are available through `connor-writer`.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
```

The tests are written with `unittest` so the repository stays dependency-free. If `pytest` is installed in the environment, `python3 -m pytest` can collect the same tests.

## Promotion Gate

A draft is certified only when all deterministic checks pass:

- schema is valid
- forbidden payloads are absent
- execution evidence exists
- effective support meets `N_min`
- success posterior lower confidence bound meets the threshold
- mean progress/effect is positive
- contradiction count is below limit
- no critical safety failure exists
- readout contract is valid
- audit trail is complete

## Subskill Readout

Certified skills are not subskills, and they are not tied to any one downstream module. At read time, a certified skill is resolved against the current object bindings and emits either:

```text
ActiveSubskillReadout
  readout_id
  generated_at
  lifecycle_state
  context_signature
  relation_evidence_signature
  geometric_readout
  semantic_token
  option_prior
  expected_belief_effect
  trust_score
  safety_metadata
  audit_pointer

NullSubskillReadout
  readout_id
  generated_at
  lifecycle_state
  context_signature
  relation_evidence_signature
  reason
  binding
  trust_score = 0
  audit_pointer
```

The active readout is the runtime object that can replace Connor-0 long-term memory rows:

```text
geometric_readout       -> current-scene relation/geometric evidence surface
semantic_token          -> compact semantic memory-token surface
option_prior            -> schema compiler relative-parameter prior
expected_belief_effect  -> BSWM conditioning/check
trust_score             -> posterior/freshness/grounding/contradiction summary
safety_metadata         -> Q selector / compiler constraints
audit_pointer           -> skill version, promotion record, and evidence traceability
```

If required roles cannot be bound in the current scene, the skill emits a `NullSubskillReadout` rather than stale spatial evidence. Optional `binding_confidence` values may be supplied by an external current-scene interface; Connor-Writer does not compute them from images.

Active readouts require explicit current-scene relation evidence:

```json
{
  "relation_evidence": [
    {
      "relation_type": "blocks",
      "anchor": "obj_3",
      "target": "obj_7",
      "source": "example_fixture",
      "status": "observed"
    }
  ]
}
```

If relation evidence is absent, mismatched, or lacks a source, readout returns `NullSubskillReadout`. This prevents a manually guessed binding from becoming an active subskill.

## Context Canonicalization

Unstable agent-written context should be compiled before readout:

```bash
PYTHONPATH=src python3 -m connor_writer compile-context raw-context.json --out canonical-context.json
PYTHONPATH=src python3 -m connor_writer readout skills/clearapproach.skill.json --context canonical-context.json
```

The context compiler is deterministic and schema-first:

- accepts `object_slots` or `objects`, then emits sorted `objects`
- normalizes `object_bindings`
- requires explicit `relation_evidence` for active readouts
- fills relation `source` from `schema.source` or `provenance.source` when present
- generates stable relation `evidence_id`
- drops free-form policy fields such as `candidate_skill` and `relation_evidence_policy`
- rejects raw images, image paths, crops, features, absolute coordinates, and trajectories

If upstream object slot names are unstable, use:

```bash
PYTHONPATH=src python3 -m connor_writer compile-context raw-context.json --rewrite-slots --out canonical-context.json
```

This rewrites object slots into deterministic family/class ids and remaps bindings and relation evidence accordingly.

This separation is intentional:

```text
CertifiedSkill file:
  durable, downstream-agnostic operator = (C, O, P, Z)

SubskillReadout:
  persisted current-scene projection generated from the skill and context
```

## Readout Persistence And Update

Readouts are durable runtime records. They are numbered by a deterministic content id:

```text
readout_id = hash(skill_id, skill_version, context_signature, relation_evidence_signature, binding, status)
```

Readout persistence follows a Hermes-style memory discipline: generated records are appended, later execution outcomes are appended separately, and only then are outcomes converted into new evidence. A certified skill is never mutated directly from a readout.

```text
CertifiedSkill + current context
  -> SubskillReadout(readout_id)
  -> ReadoutLedger
  -> OutcomeRecord(readout_id)
  -> EvidenceRecord
  -> EvidenceLedger
  -> draft/promote
  -> CertifiedSkill version update
```

## Non-goals for v1

- no neural skill induction
- no LLM judge for certification
- no dense vector retrieval
- no automatic discovery of new skill families
- no raw image, crop, feature vector, absolute coordinate, or trajectory storage
- no direct mutation of certified skills from a single trace
- no claim of universal skill writing
