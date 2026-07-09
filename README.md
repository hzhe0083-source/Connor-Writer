# Connor-Writer

Connor-Writer v1 is a deterministic skill distillation and certification lifecycle for the Connor Certified Skill Bank.

It is not a neural self-improvement system, not an LLM judge, and not a memory logger. It turns validated execution traces into certified branch-level skill operators that can emit current-scene DCEA-groundable subskill readouts.

```text
Execution Trace
  -> EvidenceRecord
  -> SkillDraft
  -> PromotionRecord
  -> CertifiedSkill
  -> ActiveSubskillReadout / NullSubskillReadout
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
CertifiedSkill S_k = (C_k, G_k, O_k, P_k, Z_k)

C_k: contract
  roles / preconditions / intended effect / stop / safety / invariance

G_k: DCEA grounding interface
  relation signature / activation predicate / roles / grounding requirements / relation kernel / null policy

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
G_k = DCEA grounding interface
O_k = Omega_k
P_k = eta_k
Z_k = promotion certificate
```

## Lifecycle

Run from the repository root:

```bash
PYTHONPATH=src python3 -m connor_writer validate evidence/
PYTHONPATH=src python3 -m connor_writer ingest evidence/sample-events.jsonl --ledger ledger/
PYTHONPATH=src python3 -m connor_writer draft --ledger ledger/ --drafts drafts/
PYTHONPATH=src python3 -m connor_writer promote drafts/ --bank skills/
PYTHONPATH=src python3 -m connor_writer list --bank skills/
PYTHONPATH=src python3 -m connor_writer show skills/clearapproach.skill.json
PYTHONPATH=src python3 -m connor_writer readout skills/clearapproach.skill.json --context examples/context.json
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

Certified skills are not passed to DCEA as whole long-term records. At read time, a certified skill is resolved against the current object bindings and emits either:

```text
ActiveSubskillReadout
  dcea_input
  semantic_token
  option_prior
  expected_belief_effect
  trust_score
  safety_metadata
  audit_pointer

NullSubskillReadout
  reason
  binding
  trust_score = 0
  audit_pointer
```

The active readout preserves the two functional surfaces of Connor-0 long-term memory:

```text
dcea_input              -> DCEA geometric route and transient spatial evidence H_t
semantic_token          -> VLM proposer / BSWM semantic route U^S_t
option_prior            -> schema compiler relative-parameter prior
expected_belief_effect  -> BSWM conditioning/check
trust_score             -> posterior/freshness/grounding/contradiction summary
safety_metadata         -> Q selector / compiler constraints
audit_pointer           -> skill version, promotion record, and evidence traceability
```

If required roles cannot be grounded in the current scene, the skill emits a `NullSubskillReadout` rather than stale spatial evidence.

## Non-goals for v1

- no neural skill induction
- no LLM judge for certification
- no dense vector retrieval
- no automatic discovery of new skill families
- no raw image, crop, feature vector, absolute coordinate, or trajectory storage
- no direct mutation of certified skills from a single trace
- no claim of universal skill writing
