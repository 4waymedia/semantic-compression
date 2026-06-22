# Dictionary Test-Group Harness — Design

> A harness to build **N candidate dictionaries** from the same corpus under
> different selection criteria/params, evaluate each on the metrics that matter,
> and **pick the strongest char-2 (Tier 1) / char-3 (Tier 2)** build. Realizes
> PROCESS.md step 4 ("SELECT VOCABULARY — test groups").
>
> **Status: design — pre-code.** Plan for review. Does not touch the production
> or any locked dictionary. Last updated: 2026-06-19.

---

## 1. Why

The current builder selects vocabulary by **raw frequency** and competes words vs
phrases on that single axis. "Strongest char-2/char-3" is an *optimization*
question — which entries should occupy the scarce 1,280 two-char and 81,920
three-char slots to maximize value — and we have no way to compare alternative
selection strategies today. The harness makes that comparison systematic and
repeatable (the "5–10 test groups" from the v0.4 plan).

It is a **measurement tool**, not a new builder: it drives the existing builder
with different configs and scores the results. The winning config is then applied
deliberately (not auto-adopted).

---

## 2. Scope

```
IN   - Build K candidate dictionaries (scratch LMDBs) from one corpus snapshot.
     - Each candidate = a named "test group" (a selection strategy + params).
     - Evaluate every candidate on a fixed held-out eval set.
     - Rank + report; recommend a config. Human chooses whether to adopt.

OUT  - Corpus expansion (upstream scanner/counters — separate work).
     - The char-4 (Tier 3) stabilization build (deferred by project policy).
     - Auto-adopting a winner or touching production/locked dictionaries.
     - Re-training (downstream; gated by the dictionary↔model lock).
```

---

## 2b. Dictionary sizes = tier build depth (char-2 / char-3 / char-4)

A dictionary's **size is how deep into the ID tiers it is built** — that is the
scale-by-use-case feature. Each tier is a char-width with a fixed max capacity and
its reserved lists (`config.py`):

```
size     tiers built       char-width  max capacity (cumulative)   use case
char-2   Tier 0 + Tier 1   1–2 char    ~1,333  (53 + 1,280)        tiny: app / keyboard / edge
char-3   + Tier 2          3 char      ~83,253 (+ 81,920)          medium: on-device, mid apps
char-4   + Tier 3          4 char      up to ~5.25M (+ 5,242,880)  large: LLM / max atomicity
```

Reserved lists are honored at **every** size (never reassigned):

```
Tier 0   58 active primitives (words + structural + system) + 6 RESERVED a–f (System 2 stages)
Tier 1   1,024 of 1,280 slots reserved for single words (tier1_word_reserve); forced seed '|'→gA
```

Building a size = **stopping tier assignment at the target depth**; surfaces past
the depth are out-of-dictionary **for that build** and encode as byte-fallback.
char-2 ships ~1.3k entries; char-4 is the deepest build.

> There is **no universal "full dictionary."** "Full" is whatever the build
> decisions (size + corpus + vocabulary selection) produced — each build *is* its
> own dictionary. The file codec is **byte-exact at every size** (byte-fallback
> reconstructs OOV bytes exactly — `verify_lossless` holds regardless of depth);
> "lossy" only ever refers to the *LLM embedding layer* (an ID the model has no
> row for), never the codec. An `.elo` must be decoded with the **same dictionary
> it was encoded under**, identified by fingerprint — the same pairing rule as the
> dictionary↔model lock (`llm-training/RETRAINING.md`).

> Builder requirement (second small refactor, alongside `select_strategy` in §5):
> a `max_tier` / build-depth parameter. Today `build()` always overflows into
> Tier 3; add `max_tier ∈ {1,2,3}` to stop at char-2 / char-3 / char-4. Default
> = 3 (behavior-identical).
>
> Relationship to LLM profiles (`docs/v1/profiles.md`): **profiles** are
> frequency-rank cuts of one dictionary for an LLM's *embedding budget*; **sizes**
> here are the physical *tier depth* of the built dictionary artifact. A char-N
> build can still be profile-cut for an LLM. v0.4 optimizes the strongest **char-2
> + char-3**; the **char-4** stabilization build is deferred (project policy).

---

## 3. What a "test group" is

A test group is a named, fully-specified recipe:

```
TestGroup:
  name                e.g. "G3_bytes_saved_char3_reserve512"
  corpus_snapshot     id/path of the frozen frequency+phrase inputs used
  target_size         char-2 | char-3 | char-4  (max_tier build depth, §2b)
  select_strategy     how the competing pool is scored + ordered (see §5)
  params              tier1_word_reserve, min_freq, drop_tier0_bigrams,
                      phrase n-gram range, per-strategy knobs
  seed                (determinism; strategies must be deterministic anyway)
```

All groups in a run share **one corpus snapshot** so differences are attributable
to selection, not data drift.

---

## 4. Architecture — build → evaluate → compare

```
inputs (frozen snapshot)        word_frequencies.txt + phrase_candidates.txt
        │                       (+ a held-out EVAL corpus, §6)
        ▼
[for each TestGroup]
   build_candidate()            builder.build(select_strategy=…, params…,
                                  lmdb_path=scratch/<group>/dictionary.lmdb)
        ▼
   evaluate_candidate()         metrics (§7) on the EVAL corpus, via the
                                  real compressor + the candidate dictionary
        ▼
   record CandidateResult       {group, fingerprint, metrics, tier occupancy}
        ▼
compare_and_rank()             one table; rank by the chosen objective (§7);
                                emit report + recommended config
```

Each candidate is independent and parallelizable. Nothing writes outside its
scratch dir.

---

## 5. Selection strategies (the axis under test)

The harness needs the builder's ranking to be **pluggable**. Today `build()`
hard-codes `pool.sort(key=lambda e: -freq)`. Minimal prerequisite refactor:

```python
# builder change (backward compatible): accept a strategy, default = current
def build(..., select_strategy: Callable[[Entry], float] = score_by_frequency): ...
# Entry = (surface, freq, kind, n, pmi)  # expose pmi to the scorer
# the competing pool is ordered by descending select_strategy(entry)
```

Strategies to compare (each a pure scorer; deterministic):

```
S0  frequency          -freq                                  (current baseline)
S1  bytes_saved        freq × (current_byte_cost − promoted_id_cost)
                       i.e. expected bytes removed from the corpus by giving this
                       surface a short ID. Words: cost≈len(utf8); phrases: sum of
                       constituent costs. THE compression-true objective.
S2  pmi_weighted       bytes_saved × f(pmi)   (favor cohesive phrases)
S3  facet_aware        bytes_saved × utility_weight (down-weight FILLER; optional)
S4  coverage/dispersion bytes_saved adjusted by #docs/channels a term appears in
                       (penalize one-channel spikes)  [needs doc-freq from counters]
```

S0 is the control. S1 is the hypothesis (value, not frequency). S2–S4 layer
quality signals. Groups = strategies × key params (reserve size, min_freq).

---

## 6. The evaluation corpus (held-out)

- A **fixed held-out split** of transcripts not reducible to the frequency
  counts (so coverage/OOV is honest). Recommend a deterministic ~5–10% slice by
  `video_id` hash, frozen for the whole comparison.
- Stored as a manifest (list of files) + a cached token stream so every group is
  scored on the identical bytes.

---

## 7. Metrics (what "strongest" means)

Primary objective (rank on this), all measured on the eval corpus:

```
M1  compression_ratio      raw_bytes / encoded_bytes  (.elo or .eloB; pick one, fixed)
M2  char2_yield            bytes saved attributable to Tier-1 IDs ÷ total saved
M3  char3_yield            bytes saved attributable to Tier-2 IDs ÷ total saved
M4  oov_rate               fraction of eval tokens with no dictionary ID
```

Guardrails (must hold, not optimized):

```
G1  round-trip byte-exact  decode(encode(x)) == x on the eval set (hard gate)
G2  tier capacity respected no overflow; Tier-1 ≤ 1,280, Tier-2 ≤ 81,920
G3  determinism            same group → identical fingerprint across two runs
```

Diagnostics (reported, not ranked): tier occupancy (words vs phrases per tier),
mean ID length per encoded token, phrase-atom hit rate, per-strategy build time.

"Strongest char-2/char-3" = the group that maximizes the value packed into Tiers
1–2 (M2+M3) without hurting M1/M4 and passing G1–G3.

---

## 8. Isolation & safety

```
- Scratch only: builds write to a per-run temp tree (e.g. db/_testgroups/<run>/<group>/).
- Never opens the production db/dictionary.lmdb for write; never re-faces or
  re-stamps it. The locked v0.3 dictionary + its model are untouched.
- Read-only inputs (frequency/phrase files + eval manifest) hashed into the run
  record so a run is reproducible.
```

---

## 9. Outputs

```
db/_testgroups/<run>/
  run.json              corpus snapshot id, eval manifest hash, all group configs
  <group>/dictionary.lmdb        the candidate (scratch)
  <group>/stats.json             tier occupancy + build provenance
  comparison.csv                 one row per group: M1–M4 + guardrail pass/fail
  REPORT.md                      ranked table + the recommended config + caveats
```

The REPORT recommends a config; **adoption is a separate, deliberate step**
(rebuild the real dictionary with the chosen config, then the cascade in
PROCESS.md §7).

---

## 10. Build order (when we implement)

```
T1  [DONE 2026-06-19] builder refactor: pluggable select_strategy + max_tier
    build-depth (§2b) added to dictionary_builder_v03.build() (defaults = S0
    score_by_frequency, max_tier=3 → behavior-identical) + --max-tier CLI +
    stats. Gate: synthetic test_builder_strategy (max_tier gating + custom
    strategy promotion) verified. Full v0.3 byte-for-byte reproduction is a
    full-corpus run (deferred; not run here to avoid rebuilding the real dict).
T2  strategies S0,S1 as pure scorers + unit tests (toy pool → expected order);
    max_tier unit test (char-2/char-3 stop at capacity, reserves honored).
T3  eval corpus: deterministic held-out split + cached token stream + manifest.
T4  evaluate_candidate(): metrics M1–M4 + guardrails G1–G3 on the eval set,
    reported PER target_size (char-2 / char-3 / char-4).
T5  harness driver: run K groups (strategy × target_size), write scratch builds
    + comparison.csv + REPORT.
T6  add S2 (pmi), S3 (facet-aware), S4 (dispersion) once S0/S1 compare cleanly.
T7  first real run: 5–10 groups at char-2 + char-3; review REPORT; pick the
    strongest char-2 / char-3 config. (char-4 deferred — project policy.)
```

---

## 11. Open decisions

```
O1  Wire format for M1: .elo (text) or .eloB (binary)? Recommend .eloB (true
    bytes-on-the-wire; matches the LLM/file-format story).
O2  Eval split size + selector: ~5–10% by video_id hash — confirm the fraction.
O3  bytes_saved cost model for phrases: sum of constituent CURRENT ids vs raw
    UTF-8? Recommend "current encoded cost under a frequency baseline" so the
    delta is realistic. Needs one reference build to price against.
O4  Do we sweep min_freq / reserve as part of groups now, or fix them and vary
    only the strategy first? Recommend: fix params, vary strategy (S0 vs S1)
    for the first run to isolate the selection effect.
O5  Facet-aware (S3) utility weights — defer until S0/S1/S2 are understood.
O6  Sizes for the first run: char-2 + char-3 only (char-4 deferred) — confirm.
    Each size is built + scored independently (a char-3 build is not required to
    be a superset of the winning char-2 build unless we add that constraint).
O7  Superset constraint across sizes: should char-3 ⊇ char-2 ⊇ ... (nested, like
    LLM profiles) so one artifact serves multiple sizes, or are sizes independent
    optimizations? Recommend independent for the first run; revisit nesting if we
    want a single shippable multi-size artifact.
```

---

## 12. Pointers

```
Builder            semantic_compression/dictionary_builder_v03.py (build(), _assign)
Process            semantic_compression/PROCESS.md (step 4; §7 cascade + lock)
Counters (inputs)  word_frequency_counter.py, ngram_counter.py, phrase_miner.py
Compressor (eval)  compressor.py (encode/decode) ; verify_lossless.py (round-trip)
Tiers/capacity     config.py (TIER_CAPACITY: T1=1,280 ; T2=81,920)
Roadmap context    docs/ROADMAP-SEMANTIC-COMPRESSION.md ; SYSTEM1.md
```
