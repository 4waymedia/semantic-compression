# N4 — Gold-Set Scale-Up: Results & Review Handoff

Date: 2026-07-05. Build: `general_v0.4_char4` (meta_layer=2).
Status: measurement done; **human review pending (Paul)** — nothing promoted AI-only.

## What existed

The N4 labeling was already staged: 4 external models (chatgpt, deepseek,
gemini, grok) + the seed 71 → `facet_gold_consensus.tsv` (543 surfaces),
42 true conflicts resolved into `facet_gold_resolved.tsv`. Never validated or
scored. Validation found: 0 dupes, 1 corrupt merge-artifact row (`so word`,
removed), 94% of surfaces in-build, all 71 seed surfaces included.
Confidence tiers: **160 high** (≥2-model agreement), 25 med (resolved
conflicts), 358 low (single-source, unreviewed).

Two harness fixes fell out: `load_gold` needed `csv.QUOTE_NONE` (the set
contains bare `"` surfaces that silently swallowed the file), and the misses
CSV now doubles as the override queue at scale.

## The numbers (the point of N4)

| Gate | n=71 seed | n=507 full | n=155 high-conf | target |
|---|---:|---:|---:|---:|
| utility | 0.986 | 0.830 | **0.923** | 0.95 |
| bucket_content | 0.939 | 0.533 | **0.761** | 0.85 |
| cue_f1_function | 0.986 | 0.588 | **0.833** | 0.90 |
| abstraction (L2, pending) | 0.800 | 0.665 | **0.720** | 0.80 |

The high-conf column is the honest read: **all gates fail at scale.** The
71-row seed overstated assignment quality — it was seeded with cases the
heuristics were built around. The low-conf column (bucket 0.479, cue F1 0.291)
mixes real tail-vocabulary misses with unreviewed single-source gold noise and
cannot be interpreted until reviewed.

**Abstraction: the sign-of-A rule did NOT hold** (0.28 → 0.80 on n=25 was
at-boundary, as flagged). At n=209 it reads 0.665; on high-conf gold 0.720.
The high-conf misses show *why* — the proxy fails in both directions:
calm abstracts read concrete (`strategy`, `ethics`, `beauty`, `knowledge`,
`culture`, `state`) and arousing/animate concretes read abstract (`money`,
`fire`, `dog`, `city`, `hospital`, `nasa`). Affect is not concreteness.
The principled fix: **Brysbaert et al. (2014) concreteness norms** (~40k
lemmas, exactly this axis) as a substrate source, same lexicon-union path
NRC-VAD took. Sign-of-A stays only as fallback for norm-OOV surfaces.

## Deliverables (this pass)

```
data/facet_gold_v2_high.tsv        160 rows, >=2-model agreement -- the promotion candidate
data/facet_gold_review_queue.tsv   577 gold-vs-build disagreements on med/low rows,
                                   sorted by dimension (abstraction 187, bucket 203,
                                   cues 86, utility 74, temporality 18, scope 9)
facet_accuracy.py                  QUOTE_NONE fix in load_gold
```

## Recommendation (decision: Paul)

1. **Promote `facet_gold_v2_high.tsv` to the working gold** (replaces the 71-row
   seed; subsumes it). Gates will fail honestly — that is the harness doing its
   job; the misses feed `facet_overrides.tsv` (U4). Keep `facet_gold.tsv` frozen
   until sign-off.
2. **Review the queue in slices** — bucket + abstraction first (390 rows).
   Per row: fix the gold (model mislabeled) or keep it (real miss → override).
   Way-2: AI proposed, you confirm; med/low rows never enter the release gold
   unreviewed.
3. **Add Brysbaert concreteness norms** to the substrate; re-measure abstraction
   on the reviewed set (new L7 sub-item; the sign-of-A backfill in
   `meta_layer2.py` becomes the fallback tier).
4. **Recalibrate targets after review** (spec §5 said "revise after baseline" —
   the real baseline is the high-conf column, not the seed).
