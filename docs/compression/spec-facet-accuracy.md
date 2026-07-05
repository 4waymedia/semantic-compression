# Facet Accuracy — Build, Test & Validation Plan

> How we measure whether facet/meta assignments are *correct* (not just complete),
> the labeling guidelines + desired results, the gold set, and how the check plugs
> into PipelineLab as a release gate. Builds on the structural gates in
> `verify_facets.py` / `test_facets.py` (which prove facets are valid & reproducible)
> by adding the missing axis: **accuracy**. Status: PLAN. Last updated: 2026-06-22.

---

## 1. Why (what's missing today)

`verify_facets.py` proves every entry is faceted, in-enum, deterministic, and
fingerprinted. It does **not** prove the assignment is *right* — only ~a dozen
hand-labeled words (T3) are checked. We have no measured accuracy on real
vocabulary, no confusion matrix, no view of which buckets/cues are weak. The
`meta.db` (now built per dictionary) makes this measurable: it's a queryable
per-surface table we can sample, score against gold, and feed back into overrides.

---

## 2. Use cases (why accuracy matters — rank work by these)

```
U1 Stream operations  — filter FILLER, route by RELATION/cue, find causal claims.
                        Wrong utility/cue = wrong results on the compressed stream.
U2 Verbalizer (S2)    — navigates by bucket + cue; bad buckets mislead the map.
U3 Expert dictionaries— domain tags drive subject-specific builds.
U4 Override targeting  — the harness's misses ARE the override priority list.
U5 Embedding budget    — CONTENT vs FUNCTION weighting in profile cuts.
```

Accuracy targets are set **per dimension by use-case impact**, not uniformly (§5).

---

## 3. Labeling guidelines (the rubric — desired "correct" results)

A facet is an **affordance** (what a machine may do with the token), not a full
analysis. Label by the *dominant, surface-determinable* sense. Rubric per field:

### Bucket (what kind of thing)
```
TOPIC       a concrete entity / nameable thing or subject      rabbit, ocean, money, NASA
CONCEPT     an abstraction or domain idea (often abstract noun
            or multiword)                                       freedom, justice, machine learning
METHOD      an action / process / procedure (typically a verb) analyze, optimize, measure
RELATION    a connective / logical operator / discourse link   because, however, if, due to
STRUCTURAL  punctuation / markup / whitespace / sentinels       ? . , </p>
UNKNOWN     filler or genuinely unclassifiable                  um, uh, erm
```
Tie-breakers: abstraction decides TOPIC vs CONCEPT (concrete→TOPIC, abstract→CONCEPT);
a word usable as both noun and verb is labeled by its **dominant** corpus sense
(note the ambiguity in `rationale`).

### Utility (how to treat it)
```
CONTENT     carries topic meaning (most nouns/verbs/adjs)
FUNCTION    grammatical glue (connectives, determiners, modals, quantifiers)
STRUCTURAL  punctuation / markup
FILLER      speaker noise (um, like, you know)
```

### Logic cues (reasoning role — a set; may be empty)
`CAUSE, CONDITION, INFERENCE, EVIDENCE_CUE, CONTRAST, CONCESSION, NEGATION,
QUANTIFIER, MODAL, QUESTION, TEMPORAL, COMPARISON, DEFINITION_CUE, CONJUNCTION,
CLAIM_CUE`. Label every cue the surface *affords* (e.g. "when" = CONDITION + QUESTION
+ TEMPORAL). Content words usually have none.

### Meta axes (System-1 deterministic)
```
abstraction   concrete | abstract | metaphor(rare; S2)        freedom=abstract, rabbit=concrete
causality     subset of {CAUSE,CONDITION,INFERENCE,EVIDENCE}  because=CAUSE+EVIDENCE
temporality   temporal | (null)                               when, before, after
scope         quantified | (null)                             all, every, some
domain        coding|medical|military|finance|... | (null)    (provenance-fed; often null now)
```

> Golden rule for labelers: **label what the surface affords, not deep semantics.**
> If two labelers would reasonably disagree, set `confidence=low` and add a note —
> those become the calibration discussion, not silent guesses.

---

## 4. The gold set

```
FILE     data/facet_gold.tsv  (TSV; human-authored + reviewed)
COLUMNS  surface, kind, bucket, utility, logic_cues, abstraction,
         causality, temporality, scope, confidence, rationale
SIZE     start ~70 (seed, this PR) -> grow to 300-500 via stratified sampling.
SAMPLING stratified across buckets + the HARD cells (TOPIC↔CONCEPT abstracts,
         multiword RELATIONs, verb/noun ambiguities, fillers) — not random, so the
         weak spots are represented.
PROVENANCE each row tagged manual (this set) or, when scaled, ai-proposed+reviewed
         (Way-2 style: AI proposes, human confirms; never AI-only).
```

The seed set deliberately includes the known failure cases (`freedom`, `democracy`,
`love`, `profit`, `for example`, `on the other hand`, `running`) so the first run
quantifies them.

---

## 5. The harness (build)

```
facet_accuracy.py
  load_gold(gold.tsv)                      -> {surface: gold_record}
  sample(meta_db, strategy)                -> rows to score (all-gold | stratified | low-conf)
  score(predicted_from_meta, gold)         -> per-dimension correctness
  report()                                 -> metrics (§6) + confusion matrix + misses.csv
```

- **Predicted** comes from `meta.db` (and `assign_facet`), so it scores the exact
  build. **Gold** is the rubric labels. Score each dimension independently.
- **Misses** are written to `facet_misses.csv` -> the override-priority queue (U4).
- Deterministic + re-runnable; no writes to the dictionary.

### Metrics (§6)
```
M1  per-dimension accuracy   bucket / utility / abstraction / temporality / scope
M2  cue set F1               precision+recall over the 15-cue multilabel
M3  confusion matrix         per dimension (surfaces the TOPIC↔CONCEPT weak spot)
M4  gold coverage            % of gold present in this build
Guardrails: structural gates (verify_facets) still pass; determinism holds.
```

### Desired results (initial targets — revise after baseline)
```
LAYER-1 RELEASE GATE (hard — these gate a build):
utility        >= 0.95   (coarse, high-impact for U1)
bucket         >= 0.85 on content words (TOPIC/CONCEPT/METHOD)
cue set F1     >= 0.90 on the closed-class function set

PENDING — System-2 (meta_layer=2), NOT gated at layer 1:
abstraction    >= 0.80   ← informational until L7. "concrete" has NO surface signal
                           (it needs EPA), so it is unreachable at layer 1 and must not
                           gate a layer-1 build. `facet_accuracy.py` reports it under a
                           "Pending" section (see PENDING_L2). See DEVELOPMENT_LIST L7.
A miss rate above target on a LAYER-1 dimension -> overrides + (if systematic) a heuristic fix.
```

---

## 6. PipelineLab integration (the release gate)

PipelineLab is the isolated, read-only calibrated-suite platform (pluggable
`TestTarget` adapters; specs injected; throwaway temp copies; never edits project
data). Facet accuracy becomes a **validation check** there:

```
- ADAPTER: a `FacetAccuracyTarget` (pipeline_lab/adapters/) that takes a dictionary
  build's meta.db + the gold set as injected params (isolation preserved — read-only,
  meta.db copied to the session temp dir like dictionary.lmdb already is).
- CHECK: runs the §5 harness; emits the §6 metrics + confusion matrix into the lab's
  checkpoint/verbalizer view, so a reviewer SEES which surfaces missed and why
  (the lab's "find where the system is off or missing a cue" purpose).
- GATE: in the calibrated suite (PROCESS.md §4, high-stakes/release builds), the
  facet-accuracy thresholds (§5) are a release gate alongside round-trip byte-exact
  and verify_facets. A build that regresses a dimension below target fails the gate.
- PROVENANCE: the run records dictionary fingerprint + meta_fingerprint + gold-set
  version (artifact-identity), so an accuracy number is pinned to an exact build.
```

This makes facet quality a *measured, gated* property of a release — not an
assumption — and reuses PipelineLab's stepping/verbalizer to make misses legible.

---

## 7. Build + test phases

```
P1  [this PR] gold set seed (~70) + spec (this doc). Human review.
P2  facet_accuracy.py harness (load/sample/score/report) + facet_misses.csv.
    [HARNESS DONE 2026-06-22] pure-stdlib (sqlite3+csv); reads meta.db + gold;
    per-dim accuracy, 15-cue F1, confusion, coverage, misses CSV; targets gate via
    exit code; `--selftest` green. >>> RUN BASELINE: `python facet_accuracy.py`
    (defaults to db/builds/general_v0.4_char4/meta.db) -> first real numbers + confusion.
P3  Triage misses -> overrides + heuristic fixes (esp. TOPIC↔CONCEPT abstraction).
    Re-run; confirm targets.
P4  Scale gold to 300-500 (stratified; AI-proposed + human-reviewed).
P5  PipelineLab FacetAccuracyTarget + add to the calibrated suite as a release gate.
```

---

## 8. Pointers

```
Gold set        semantic_compression/data/facet_gold.tsv
Predicted from  semantic_compression/meta.db (meta_builder.py) ; facets.py (assign_facet)
Structural gates verify_facets.py ; test_facets.py
Overrides       data/facet_overrides.tsv (the harness feeds its misses here)
Schema          docs/compression/spec-meta-db.md ; spec-facets-db.md
Platform        ../PipelineLab/ (adapters, calibrated suite) ; PROCESS.md §4
```
