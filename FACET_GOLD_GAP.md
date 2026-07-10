# Facet gold blind spot: the `FUNCTION_WORDS` reclassification is unmeasured

*Finding recorded 2026-07-09. Status: verified by measurement; gold rows proposed, not applied.*

## TL;DR

`facets.py` now classifies `the` / `of` / `a` / `it` / `was` as `FUNCTION` instead of
`CONTENT`. That change is **real** but **invisible to every test in this repo**. The
facet-accuracy release gate reports `utility = 1.000` both before and after, because the
396-row gold set **does not contain any of the words the change flips**. The gate passes
vacuously with respect to this change and cannot fail on a regression here.

The one place the change *does* propagate is not the utility axis at all — it is the
**dictionary fingerprint**, which invalidates 06's surface inverted index.

## What changed, and how big it actually is

`facets.py` sets `utility = FUNCTION` when a key is in `FUNCTION_WORDS`, else `CONTENT`
(the `is_function_word` branch). After `facet_builder.py` re-derived all 373,918 forward
entries:

```
Utility histogram:  CONTENT 373,586 · FUNCTION 252 · FILLER 50 · STRUCTURAL 30
```

**252 of 373,918 entries = 0.07%.** Two framings, both true, with very different blast radii:

* **Type level** (dictionary entries): a rounding error.
* **Token level** (running text): `the/of/a/it/was/and` dominate occurrences — "roughly half
  of all tokens" is fair.

Consumers split along exactly that line. Nothing in 06/07 filters running text by `utility`,
which is why nothing moved.

## The blind spot (measured)

Every word the change flips is **absent** from `data/facet_gold.tsv`:

```
the -> ABSENT   of -> ABSENT   a  -> ABSENT   it   -> ABSENT   was -> ABSENT
is  -> ABSENT   to -> ABSENT   in -> ABSENT   that -> ABSENT
```

Yet the accuracy report shows `FUNCTION -> FUNCTION: 134`. Those 134 rows are **relational
connectives** (`after`, `all`, `although`, `because`, `when`, `therefore`, …), which the gold
buckets as `RELATION` (gold bucket distribution: RELATION 139, CONCEPT 99, TOPIC 74,
METHOD 38, STRUCTURAL 26, UNKNOWN 20).

Crucially, those words get `utility = FUNCTION` from a **different, independent rule**:

```python
# facets.py (two sites)
if bucket == BUCKET['RELATION'] and utility == UTILITY['CONTENT']:
    utility = UTILITY['FUNCTION']
```

That rule has nothing to do with `FUNCTION_WORDS` membership. So the gold exercises the
RELATION rule, never the `FUNCTION_WORDS` path.

Corroborating evidence: the only 3 misses in the whole run are content words —
`god` (bucket), `microsoft` (abstraction), `nasa` (abstraction). **Zero utility misses.**

## Why `utility = 1.000` is vacuous here

| Consumer | Reads the `utility` axis? | Can this change move it? |
|---|---|---|
| `facet_accuracy.py` | yes (34 refs) | **No** — gold omits the flipped words |
| `06/recal_engine/facet_recall.py` | **no** (`FACET_AXES = ('agency','direction','temporal')`) | No |
| `06/recal_engine/seed_surfaces.py` | filters on tokenizer `CLASS_WORD`, not utility | No |
| `semantic_compression/verbalizer.py` | no (0 refs) | No |
| `07-ContextAssembly` (whole package) | no facet usage at all | No |
| PipelineLab adapters | none read utility | No |

Verified test totals, unchanged by the re-derive: 06 → `141 passed, 7 skipped, 1 xfailed`
(149); 06 facet subset → `13 passed, 2 skipped`; 07 → `53 passed`.

## The one real coupling: the dictionary fingerprint

`facet_builder.py` computes the fingerprint **from the facet records**:

```
# "dictionary_fingerprint is written in a second pass (depends on facet records)"
fingerprint = compute_fingerprint(env)
```

06 reads exactly that key (`read_dict_fingerprint()` → `meta.dictionary_fingerprint`) and
enforces it via `InvIndexStore.assert_fingerprint()` / `recall_seeds(check_fingerprint=True)`
(the D4 guard).

**Consequence:** re-deriving facets changes the fingerprint (this run: `cf7b56e3…`), so any
existing `recal_invindex.lmdb` is stale and the D4 guard will trip. Rebuild it:

```python
RecalEngine(index_surfaces=True).rebuild_invindex()
```

The same applies to anything else pinned to a dictionary version (`.eloB` payloads, the
browser's `facets.bin`).

## Methodological note (a check that cannot fail)

`git diff -- facet_accuracy_report.md` returns empty — but that proves nothing here:
`fatal: not a git repository: .../.git/modules/semantic_compression`. In a submodule that
empty diff is a **false negative**. "No movement" was established instead by comparing the
report's values before and after the re-run (`utility 1.000 · bucket_content 0.995 ·
396/391/99%`, identical). Prefer explicit value comparison over `git diff` for this report.

## Recommendations

1. **Add gold coverage for the flipped closed-class words** (below). Until then the release
   gate is blind to `FUNCTION_WORDS`. Coverage reads "99%" of a gold set that omits the most
   frequent tokens in running text.
2. **Resolve the bucket taxonomy for determiners/pronouns/auxiliaries.** The build currently
   emits `the -> TOPIC / FUNCTION [HEURISTIC]`. `TOPIC` is wrong, and there is no honest slot
   (`STRUCTURAL` is used for punctuation). This is plausibly *why* these words were never
   added to gold — the utility label is obvious, the bucket label is not.
3. **Rebuild the surface inverted index** after any facet re-derive (fingerprint changes).
   Consider a test asserting the D4 guard trips on a stale index.
4. Nit: the gold header still claims "160 rows"; the file carries **396**.

## Proposed gold rows — NOT applied

Gold rows carry provenance (`src=3 bucket=RELATIONx3 utility=FUNCTIONx3`) and require
**>=2-model agreement**. These are candidates for that process, not a patch.

Columns: `surface  kind  bucket  utility  logic_cues  abstraction  causality  temporality  scope  confidence  rationale`

Uncontroversial (prepositions — consistent with the existing `after` row):

```
of	word	RELATION	FUNCTION	-	-	-	-	-	pending	PROPOSED: needs >=2-model agreement
to	word	RELATION	FUNCTION	-	-	-	-	-	pending	PROPOSED: needs >=2-model agreement
in	word	RELATION	FUNCTION	-	-	-	-	-	pending	PROPOSED: needs >=2-model agreement
```

Utility clear, **bucket unresolved** (blocked on recommendation 2 — do not paste as-is):

```
the	word	TBD	FUNCTION	-	-	-	-	-	pending	PROPOSED: determiner; build says TOPIC (wrong)
a	word	TBD	FUNCTION	-	-	-	-	-	pending	PROPOSED: determiner
it	word	TBD	FUNCTION	-	-	-	-	-	pending	PROPOSED: pronoun
was	word	TBD	FUNCTION	-	-	-	temporal	-	pending	PROPOSED: copula/auxiliary (past)
is	word	TBD	FUNCTION	-	-	-	-	-	pending	PROPOSED: copula/auxiliary
that	word	TBD	FUNCTION	-	-	-	-	-	pending	PROPOSED: determiner/complementizer
```

## Open questions

* Should determiners/pronouns/auxiliaries get a dedicated bucket (e.g. `CLOSED_CLASS`), or
  fold into `STRUCTURAL`? Until answered, they cannot be scored on the `bucket` dimension.
* Was `facet_accuracy_report.md` generated before or after the `FUNCTION_WORDS` change? The
  numbers are identical either way, because the gold cannot see the change — but pinning the
  provenance would close the loop.
