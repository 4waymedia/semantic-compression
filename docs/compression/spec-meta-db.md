# Semantic Meta DB — Specification (DRAFT)

> The per-entry **meta** layer: a wide, queryable row for every dictionary surface
> that extends the compact 4-byte facet without widening it. System-1 columns are
> populated **now** (deterministic, no model); System-2 columns are **reserved**
> and filled after the EPA layer (`semantic-meanings`) is finalized.
>
> **Status: DRAFT — design for after EPA.** The System-1 subset is implemented
> (`meta_fields.derive_meta`). System-2 columns are specified but null until EPA
> lands. Builds on: [`spec-facets-db.md`](spec-facets-db.md) (the 4-byte record),
> [`spec-vocab-strategy.md`](spec-vocab-strategy.md), the cross-dictionary STANDARD
> (`../../Memory/docs/SEMANTIC_FACETS_SPEC.md`). Last updated: 2026-06-20.

---

## 1. Why a meta DB, not a wider facet

A verbalizer (System 2/Engine layer, reads from `semantic_compression`) wants ~9
**orthogonal, multi-valued** semantic axes per ID. Those cannot live in the
facet: even at 2–4 bits each plus the EPA floats they blow past 4–8 bytes, and the
facet is deliberately the **fixed, C-readable hot-path subset** consumed by the
compressor. So the record stays frozen at 4 bytes and the richer attributes become
**columns in a side table keyed by the same ID** — exactly the pattern the STANDARD
spec already uses for `epa` (12 B) and `templates` (33 B).

```
facet record (4B, LMDB)   = compiled hot-path subset  (bucket+cue_mask+flags)
meta row     (this spec)  = full per-surface attributes (projected from one pass)
epa / templates (S2)      = affect + 4D vectors, separate id-keyed tables
                            ── all joined by the same id_bytes / surface ──
```

The facet is a *projection of* the meta row, so they can never diverge.

---

## 2. Key & lifecycle

```
KEY            normalized SURFACE (stable). The dictionary ID is a COLUMN,
               re-mapped every build — IDs are provisional until char-4 stabilization.
RE-DERIVED     per build, idempotent (like facets). Same inputs -> same rows.
FINGERPRINT    meta_fingerprint over the DETERMINISTIC subset only (excludes S2
               nulls, provenance method, confidence) — reproducible contract.
LAYER          meta_layer: 1 = System-1 fields only; 2 = EPA/System-2 filled.
```

Because the key is the surface, the meta DB survives re-builds and re-selection;
nothing downstream persists raw provisional IDs.

---

## 3. Column schema

Layer tag: `S1` = populated now (deterministic); `S2` = reserved, null until EPA.

```
COLUMN              TYPE        VALUES / RANGE                         LAYER  SOURCE/METHOD
-- identity / structural ------------------------------------------------------------------
surface             text (key)  normalized surface                     S1    tokenizer/normalize
id                  text        Base64 ID this build                    S1    builder (re-mapped)
kind                enum        word | phrase                           S1    structural
token_count         int         words in the unit                       S1    structural
tier                int         0..3                                    S1    builder
frequency           int         corpus count                            S1    counters
-- facet mirror (the 4-byte record, unpacked) --------------------------------------------
bucket              enum        UNKNOWN|TOPIC|METHOD|CONCEPT|RELATION|STRUCTURAL  S1  facets
utility             enum        CONTENT|FUNCTION|STRUCTURAL|FILLER       S1    facets
logic_cues          set         15 composable cues                      S1    facets
flags               set         MULTIWORD|CLOSED_CLASS|MANUAL|HEURISTIC|AMBIGUOUS S1 facets
-- System-1 richer axes ------------------------------------------------------------------
abstraction         enum        concrete | abstract | (metaphor=S2)     S1    suffix tell (partial)
causality           set         CAUSE|CONDITION|INFERENCE|EVIDENCE_CUE   S1    surfaced from cues
temporality         enum        temporal | null  (fine stage = S2)      S1    surfaced from cues
scope               enum        quantified | null  (fine scope = S2)    S1    surfaced from cues
domain              text        coding|medical|military|culinary|…      S1*   Wiktionary topic / source provenance
register            enum        formal|technical|colloquial|literary    S1*   source provenance
-- System-2 RESERVED (null until EPA finalized) ------------------------------------------
polarity            enum        positive|negative|neutral|bipolar       S2    sign/bin of EPA-E
epa_e, epa_p, epa_a float       -4.0 .. +4.0                            S2    EPA (Warriner) lookup
agency              enum        self|other|system|environment           S2    EPA-P + role inference
directionality      enum        toward|away|increase|decrease           S2    embedding/inference
temporal_stage      dist        Surov 6-stage affinity (a–f)            S2    stage projection
relations           list        nearest semantic-neighbor IDs           S2    FAISS
-- provenance (not fingerprinted) --------------------------------------------------------
_method             map         per-field: cue|suffix|heuristic|override|provenance  S1
confidence          float       0..1 per assigned field                 S1
needs_review        bool        ambiguous / low-confidence -> override queue   S1
```

`S1*` = System-1-legal but only populated when the build supplies provenance
(Wiktionary categories / source kind); otherwise null, still deterministic.

---

## 4. The nine verbalizer dimensions — where each lives

The external requirements list, reconciled against the architecture:

| Dimension | Layer | Home / derivation |
|---|---|---|
| Causality (cause/effect/condition/result) | **S1 now** | `causality`, surfaced from `CAUSE/CONDITION/INFERENCE` cues |
| Temporality (state/process/event/outcome) | S1 coarse / **S2 fine** | `temporality` (cue) now; fine stage ladder = Surov `a–f` (S2) |
| Scope (individual/group/system/universal) | S1 coarse / **S2 fine** | `scope` from `QUANTIFIER` now; fine = S2 |
| Abstraction (concrete/abstract/metaphor) | S1 partial / **S2 metaphor** | suffix tell now; metaphor needs context (S2) |
| Domain (coding/medical/military/…) | **S1 now*** | `domain` from Wiktionary topic + source provenance |
| Register (formal/technical/colloquial) | **S1 now*** | `register` from source kind |
| Polarity (pos/neg/neutral/bipolar) | **S2** | sign of EPA Evaluation |
| Directionality (toward/away, ±) | **S2** | embedding/inference (small curated lexicon possible) |
| Agency (self/other/system/environment) | **S2** | EPA potency + actor inference |

So 1 axis is fully present, 3 partially (cues/bucket), 2 buildable now from
provenance, and 3 are EPA/System-2.

---

## 5. EPA projection contract (the System-2 bridge)

This is what the EPA finalization (`semantic-meanings`) must deliver so the meta
DB can flip to `meta_layer = 2`. Documenting it now, while EPA is being finished:

```
INPUT      a surface (lowercased token). NOT an embedding — this is a lexicon lookup.
SOURCE     Warriner et al. (2013) norms, 13,915 English lemmas. Unrated tokens ->
           difflib morphological fallback (cutoff 0.72, best of 3); neutral (0,0,0)
           if no close match. No ML dependency.
OUTPUT     epa = (E, P, A), each float in [-4.0, +4.0]
             E = valence - 5,  P = dominance - 5,  A = arousal - 5   (Warriner 1-9 scale)
STORAGE    struct '<fff>' (12 B), little-endian, C-readable. Today keyed by token
           UTF-8 bytes in dictionary.lmdb b'epa'.  (Reconcile: the STANDARD spec
           says key by Base64 id_bytes — see Improvements O-EPA1.)
DERIVED    polarity = bin(E)  (neg < -t, neutral, pos > +t; bipolar if |E| low + |A| high).
           agency / directionality / temporal_stage are NOT produced by the EPA
           projector — they are separate System-2 components (word_classifier,
           templates, stage projection), keyed off EPA + role.
IMPL       Memory/mneme/substrate/epa_projector.py  (build_epa_db ; EPAProjector.get_with_source)
DETERMINISM deterministic lexicon lookup; reproducible given the Warriner CSV +
           fallback params. (Not yet version-stamped/fingerprinted — Improvement O-EPA2.)
PAIRING    an EPA table is valid only for the dictionary it was projected against.
```

Until this lands: `epa_*`, `polarity`, `agency`, `directionality`, `temporal_stage`
stay null and `meta_layer = 1`. When it lands, a layer-2 pass fills them, bumps
`meta_format_version`, and recomputes the (separate) S2 fingerprint — the S1
deterministic fingerprint is unchanged.

---

## 6. Storage & build integration

```
- Store: SQLite meta.db in the build package (queryable; the human/test/override
  surface). Hot columns MAY later be projected into an LMDB sub-db (deferred).
- Build step: build_from_spec -> dictionary -> facets -> META (derive_meta over
  every surface, + provenance from corpus sources / Wiktionary) -> meta.db.
- Manifest: record meta_fingerprint, meta_layer, column coverage % alongside the
  corpus + dictionary fingerprints. A dictionary then declares its meta provenance
  exactly as it declares its corpus.
- Re-run every build (idempotent). Keyed by surface, so re-selection is safe.
```

---

## 7. Why this also unblocks testing

`meta.db` is the prerequisite for the facet/meta **accuracy harness**: it lets you
stratified-sample (e.g. low-confidence CONCEPT-vs-RELATION, or `domain=null`
content words), attach gold labels, score bucket/cue/abstraction/domain against
them, and route misses straight into `facet_overrides.tsv` via the `needs_review`
queue. Build the meta methods first (this spec), then the test.

---

## 8. Open decisions

```
O1  Store: SQLite vs an LMDB binary sub-db for the meta row. Recommend SQLite for
    the queryable analysis/test layer; project hot columns to binary only if a
    runtime path needs them.
O2  Domain taxonomy: adopt Wiktionary category roots as the domain vocabulary, or
    a curated short list (coding/medical/military/legal/finance/culinary/general)?
O3  Confidence scale + needs_review threshold (drives the override queue size).
O4  Abstraction: add a positive 'concrete' tell (e.g. NAMED_ENTITY / physical-noun
    lexicon) or leave non-abstract as null (current: honest null).
O5  Agency/directionality: pure EPA-derived, or a small curated lexicon seed in S1?
    Defer until EPA so we don't duplicate signal.
```

---

## 9. Pointers

```
S1 derivation     semantic_compression/meta_fields.py (derive_meta)
Facet record      semantic_compression/facets.py ; docs/compression/spec-facets-db.md
EPA / vectors     semantic-meanings (System 2, separate package) ; epa '<fff>' table
Build hook        semantic_compression/build_from_spec.py (add META step)
Cross-dict STD    ../../Memory/docs/SEMANTIC_FACETS_SPEC.md (epa/templates conventions)
Create guide      docs/compression/GUIDE-create-dictionary.md
```
