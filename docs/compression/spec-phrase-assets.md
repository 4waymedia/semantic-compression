# Spec — Phrase Assets & Compositionality Routing (FUTURE / direction)

> **Status: FUTURE, evidence-driven.** Written after measuring phrase compositionality
> on `elo-browser-v01a` (`phrase_compositionality.py`). It records *why we did NOT
> build phrase denotative assets for this build*, the method for when we do (domain
> builds), and the phrase-miner hygiene that has to come first. Companion:
> [`spec-build-family.md`](spec-build-family.md) (phrase assets are a derived layer),
> [`spec-markup-semantics.md`](spec-markup-semantics.md) (role similarity, a different
> channel).

---

## 1. The measurement (why this spec exists)

We asked: can a phrase's assets be *composed from its words*, or does it need its own?
The test is compositionality = `cos( compose(word_vectors), embed(phrase_directly) )`.
Run on `elo-browser-v01a` (160,121 phrases, 258k word vectors):

- Only **11,818** phrases are **fully-composable** (every constituent is a content word
  in the index). The other ~92% contain function words → not cleanly composable.
- On that clean subset: **median 0.72**, **idiomatic (<0.5) just 3.0%**, compositional
  (≥0.8) 19.9%.
- **The examples are the finding:**
  - Lowest cos ("idiomatic"): `thank god`, `good luck`, `can't wait`, `i'm pretty
    sure`, `pretty much` — **pragmatic/discourse formulas**, not lexical idioms.
  - Highest cos ("compositional"): `jd vance`, `jocko willink`, `medicare medicaid`
    (**named entities**) and `china china`, `conversation conversation` (**ASR
    stutter-repeats**).

**Conclusion for a conversational corpus:** the phrase inventory is discourse
fragments + names + ASR noise, *not* lexical idioms. Phrase **denotation is low-value
here** — we deliberately did not build phrase `neighbours`. The value is elsewhere
(§4 hygiene, §3 routing to facets).

---

## 2. The metric & tool

`phrase_compositionality.py --build <pkg> --device cuda` reuses the built word vectors
(no re-embed), embeds phrases directly, and reports the distribution split into
**all-composable** (confounded by partial coverage) and **fully-composable** (the real
signal — every word a content word). It writes `phrase_compositionality.csv`
(`phrase,n_words,n_found,compositionality`) so thresholds are chosen from data. The
partial-coverage confound is real: a phrase with `n_found=1` scores ~0 as an artifact
(one word vs the whole phrase), NOT because it is idiomatic — always read the
fully-composable subset.

---

## 3. Three-lane routing (when a build DOES have lexical phrases)

Phrases are not one thing; route by profile, don't asset them uniformly:

| lane | signal | asset treatment |
|---|---|---|
| **Discourse / pragmatic** | function-heavy, low content density; or a known pragmatic formula (`good luck`, `you know`) | **No denotation.** Route to facets / the Tier-6 pragmatic layer — their meaning is pragmatic, not referential. |
| **Compositional lexical** | fully-composable, high cos (≥ ~0.8) | **Compose** the vector from word vectors (free, no re-embed) — `garbage collection`, `red wine`. |
| **Idiomatic lexical** | fully-composable, low cos (< ~0.5) | **Direct embed** the phrase string (mpnet captures the compound) — `cast iron`, `hot dog`. |

The threshold is per-corpus (read it off the CSV histogram), not a constant. This is
the "try both, then customize" the measurement made concrete.

### Multi-channel phrase profile

Denotation is one axis; profile across all channels and let the profile decide:

- **Denotation** — `cos(composed, direct)`: is the *meaning* compositional?
- **EPA** — does the phrase's affect equal the composition of its words'? (`epa_match`
  already distinguishes `composed_all` vs `composed_partial` — the same idea for affect.)
- **Facet/role** — does the phrase's bucket/role match its head word, or shift
  (`due to` ≠ `due` + `to`)?

A phrase that diverges on *all* axes is a true idiom; one that diverges only on affect
is a pragmatic formula; agreement everywhere means compose freely.

---

## 4. Prerequisite — phrase-miner hygiene (do this FIRST)

The measurement exposed that the inventory itself is noisy. `phrase_miner.py` today
computes PMI and does maximal-phrase filtering, but **PMI is only a score *weight*
(`MIN_PMI_DEFAULT=0.0`), not a filter**, and score is byte-savings-dominant — so
high-frequency function n-grams and ASR repeats survive. Add these gates (each cheap,
deterministic, in `phrase_miner.py`):

1. **Repeat-drop** — reject n-grams with adjacent identical tokens (`china china`,
   `conversation conversation`) — ASR stutters. Not phrases.
2. **PMI floor** — make PMI a *filter*, not just a weight (e.g. `--min-pmi 1.5` for
   lexical builds). Drops chance-level co-occurrences (`how to deal with it`).
3. **Content density** — require ≥ K content words (or content ratio ≥ τ), using
   `config.FUNCTION_WORDS` + utility. Drops discourse fragments dominated by `to/the/
   that/i/you`.
4. **Edge-function trim** — a lexical phrase shouldn't start/end with a function word
   (`to deal with the` → trailing `the`); trim or reject.
5. **Length sanity** — lexical phrases are ~2–4 words; the 6–9-gram band is almost all
   discourse — mine it separately (or not at all) for lexical builds.

Effect: shrinks ~160k → a much smaller, genuinely lexical set, on which §3 routing
actually pays off. Keep the current "no editorial filtering" stance — this is
linguistic hygiene, not topic filtering.

### 4a. STATUS — built and run, 2026-07-27 (measured)

> Claims below carry `VALIDATED` / `REFUTED` / `UNVERIFIED` per CLAUDE.md's
> validation loop ("the same loop applies to our own assumptions"). Every
> `VALIDATED` line names the run that settled it; reproduce with
> `python -m semantic_compression.phrase_miner --selftest` and the audit
> scripts referenced inline.

All five gates are implemented in `phrase_miner.py` (`passes_hygiene`, preset
`--lexical`), OPT-IN so the frozen v0.3 `phrase_candidates.txt` contract is
preserved. `--selftest` pins the behaviour.

**A defect found on the first real run, recorded so it is not reintroduced.**
Gates 3 and 4 originally tested raw `w in config.FUNCTION_WORDS`. That set
**deliberately omits conjunctions, wh-words and negation** (see its own
docstring — they are already `FUNCTION` via a LOGIC_SEED cue, so facets has no
need to list them). A raw membership test has no access to that cue path, so
`and`, `but`, `or`, `so`, `if`, `because`, `not` all read as CONTENT, and the
edge gate could not reject `and you know`, `and then`, `if you want`,
`but you know`.

`VALIDATED` — measured on the resulting candidate file: **18,536 of 50,347
entries (36.8%)** carried one of those 40 words on an edge. The gate logic was
right; its membership test was blind to roughly two-thirds of the function
vocabulary. Re-mining with the fix: 0 function-word edges.

**Fix:** the gates now ask `facets.assign_facet()` for the UTILITY axis — the
authoritative answer, covering both the explicit list and the cue path.
`config.FUNCTION_WORDS` is **not** modified, so facets output and every
dictionary build are unchanged. (Note for future callers: `facets.py` uses
bare imports, so it is only importable with `semantic_compression/` itself on
`sys.path`; `from semantic_compression.facets import …` fails and falls back
silently unless guarded. `mine()` now warns loudly if the fallback is hit —
that warning is what caught this.)

**Outcome on `elo-browser-v01a` inputs (232,983 raw n-grams):**

| stage | kept |
|---|---|
| after hygiene gates | 28,372 |
| after PMI ≥ 1.5 | 26,730 |
| after maximal-phrase absorption | **23,918** |

vs 50,347 from the pre-fix gate. Function-word edges in the output: **0**
(was 18,536). The top of the list moved from `and you know` / `and then` /
`if you want` to `lot of people` / `united states` / `little bit`.
`phrase_candidates.lexical.txt` was regenerated with the fixed gate on
2026-07-27, replacing the pre-fix file (the old one is not in git — `data/*`
is gitignored — so it was preserved out-of-tree before overwriting).

### 4b. What §4 does NOT fix (measured, so it is not re-attempted)

Hygiene fixes the *inventory*. It does **not** improve non-compositionality as
a detector of lexical compounds, because the two failure modes are not the
same thing. Measured against `wonder/compound_learning.py`'s discovery
population (2-word, fully-composable, content-word pairs scoring < 0.50):

| population | asks | genuine compounds | precision |
|---|---|---|---|
| unrestricted | 198 | 28 | 14.1% |
| ∩ pre-fix lexical set | 191 | 28 | 14.7% |
| ∩ fixed lexical set | 191 | 28 | **14.7%** |

Hygiene removes only 7 of 198. **Compositionality measures idiomaticity, and
pragmatic formulas are idiomatic:** `good luck`, `sounds good`, `can't wait`
are legitimate content-word bigrams with genuinely low cos — they pass every
hygiene gate correctly. Nothing in this signal separates them from `hard
drive`.

> **Caveat on these numbers.** "Genuine compound" is a hand-label over the
> 198-phrase population. That labelling was demonstrably incomplete — a later
> pass found `monetary policy`, `patriot act`, `side effects`, `significant
> other`, `free time`, `high end`, `pro life`, `wait times` sitting in the
> "false positive" pile. **Read 14.1% as a floor, not a point estimate;** the
> true value is nearer 20%. The *relative* comparison across rows is sound
> (same labels throughout), which is what the row-to-row conclusion rests on.

### 4c. Why syntax is necessary but NOT sufficient (measured 2026-07-27)

An earlier draft of this section asserted that the discriminator "is syntactic
(noun-headed vs verb-headed), i.e. POS, which this codebase does not carry."
That was measured afterwards and is **only partly right**. Recording the
correction, because the sharper version changes what a fix would have to be.

**Head type alone is a marginal signal.** Rejecting any pair whose first word
is a verb (`w in CONTENT_VERBS` or `assign_facet` bucket `METHOD`):

| | asks | real | precision | recall |
|---|---|---|---|---|
| no head rule | 198 | 28 | 14.1% | 100% |
| reject verb-initial | 171 | 28 | **16.4%** | **100%** |

Free in recall terms — it discards 27 phrases and loses no labelled compound
— but it moves precision 2.3 points. It cleanly catches the `get`/`go`/`come`
family (`get along`, `go ahead`, `come true`, `close enough`) and little else.

**The false positives are four families, not one.** Of 167:

| family | n | share | examples |
|---|---|---|---|
| contraction | 55 | 32.9% | `can't find`, `aren't true`, `anytime you're` |
| evaluative-adjective initial | 34 | 20.4% | `good call`, `big deal`, `damn good` |
| verb-initial (detected) | 27 | 16.2% | `get along`, `come true`, `go ahead` |
| other | 51 | 30.5% | `sounds good`, `looks great`, `too late`, `passed away` |

**Why the verb check underperforms here — three concrete gaps.** Much of
"other" *is* verb-headed (`sounds good`, `looks good`, `goes wrong`, `passed
away`, `stay tuned`, `takes time`, `keeps coming`, `making sure`); the code
simply cannot see it:

1. `facets.assign_facet`'s `METHOD` bucket is documented "assigned
   conservatively" and measurably is — not one common verb lands there:
   `sounds`→`TOPIC`, `make`→`CONCEPT`, `looks`→`CONCEPT`, `wait`→`CONCEPT`,
   `go`→`TOPIC`.
2. `word_classifier.CONTENT_VERBS` is a ~150-entry hand-curated list carrying
   only some inflections — it has `look`/`looked` but not `looks`, `make`/`made`
   but not `makes`, and none of `sounds`, `takes`, `keeps`, `goes`.
3. `meta.db` has **no POS column**, and the meta_layer2 fields that might have
   proxied for one are empty on this build: `agency` 0.0%, `method` 0.0%,
   `directionality` 0.0%, `temporal_stage` 0.0%, `complement` 0.1% populated
   (of 263,362 word rows). Only `abstraction` is meaningfully filled (29.5%).

`UNVERIFIED` — the 45-55% ceiling estimate below is reasoning from the
category counts, not a built-and-measured result. The category counts
themselves and the 16.4% verb-rule figure are `VALIDATED`.

**And even perfect POS would not finish the job.** The
evaluative-adjective family is the counterexample: `good call`, `big deal`,
`great news` are ADJ+NOUN — *the same syntactic shape* as `hard drive`,
`solar system`, `red tape`. No part-of-speech tagger separates them, because
the difference is not syntactic at all. It is whether the phrase has been
**conventionalized as a name for a thing** — lexical/encyclopedic knowledge,
not grammar. That is precisely what a dictionary or a person supplies, and
precisely what this corpus cannot.

So the accurate statement is: POS would recover roughly the 16% + much of the
30% "other" band (call it a plausible ceiling near 45-55% precision), the
contraction family is a cheap orthographic rule, and the ~20%
evaluative-adjective family is **not reachable by any automatic signal
available here**. Which is the same conclusion §1 reached by a different
road, and the reason `wonder/compound_learning.py` treats direct teaching as
the primary path and discovery as a narrow supplement.

This is the concrete, measured form of §1's conclusion. Treat it as settled:
do not re-run hygiene expecting compound-detection precision to move, and do
not expect a POS tagger alone to close the gap either.

### 4d. The hygiened set must NOT replace the dictionary's phrase source

`VALIDATED` (2026-07-27). An obvious-looking follow-up to §4a is to rebuild the
dictionary from `phrase_candidates.lexical.txt` instead of
`phrase_candidates.txt`. **Do not.** Measured against the byte-savings the
miner itself computes:

| set | entries | total savings |
|---|---:|---:|
| `phrase_candidates.txt` | 167,876 | 138,323,073 B |
| `phrase_candidates.lexical.txt` | 23,918 | 8,048,854 B |
| **dropped by hygiene** | 143,958 | **130,274,219 B (94.2%)** |

And the loss concentrates exactly where the dictionary promotes from — the top
of the list by savings:

| promotion cut | survive hygiene | savings retained |
|---|---:|---:|
| top 10,000 | 381 (3.8%) | 3.3% |
| top 50,000 | 2,854 (5.7%) | 4.3% |
| top 167,275 (the shipped cut) | 17,901 (10.7%) | 5.8% |

The reason is structural, not a tuning artifact: a phrase's byte savings is
`freq × (n·1.8 − 3)`, so **the highest-value compression atoms are the most
frequent fragments** — `and you know` (83,529 B), `and then` (93,873 B) — which
are precisely what hygiene is designed to remove. Compression rewards
frequency; lexical hygiene rewards referentiality. On a conversational corpus
those two objectives are close to orthogonal.

**Consequence — these are two parallel assets, not two versions of one.**
`phrase_candidates.txt` remains correct for the dictionary's actual job
(v0.3's 1.99× ratio rests on those 167,275 atoms). The hygiened set is the
right input for *semantic* work — phrase denotation assets (§3), compound
detection, anything asking "is this a name for a thing". The miner's existing
"SAFETY: any hygiene run writes to a SEPARATE file" behaviour is therefore
load-bearing, not caution — the two files are different artifacts serving
different consumers, and merging them would trade a measured 1.99× for a
lexical cleanliness the compressor does not benefit from.

---

## 5. Coupling & fit

- **Per-build, `build_id`-bound**, `n`-keyed like every asset (composed/direct phrase
  vectors go into the same denotative index; neighbours then cover phrases too).
- **Selective coverage** — most builds won't asset every phrase; `covered_ids`
  declares what got denotation (§4 of the asset-pipeline spec).
- **Composition is the family pattern** — EPA already composes phrase affect from words
  (`composed: 147,881`). This spec extends the same "words first, phrases derived"
  principle to denotation, *conditioned on compositionality*.

---

## 6. Increments (when picked up, likely on a domain build)

1. Add the §4 miner hygiene gates; re-mine; confirm the inventory is lexical.
2. Re-run `phrase_compositionality.py`; set per-corpus thresholds from the CSV.
3. Route: compose the high-cos subset into the index; direct-embed the low-cos idioms;
   send pragmatic formulas to facets.
4. Re-export `neighbours.bin` — now covering lexical phrases `n`-for-`n`.

**Not now for conversational/browser builds** — the data says the payoff isn't there.
Revisit on medical/legal/programming builds, where lexical multi-word terms are real.

---

## Pointers
- Measurement tool: `phrase_compositionality.py`; data: `<pkg>/phrase_compositionality.csv`.
- Miner: `phrase_miner.py` (PMI, maximal-phrase, scoring).
- Build family / derived-asset model: [`spec-build-family.md`](spec-build-family.md).
- Role similarity (distinct channel): [`spec-markup-semantics.md`](spec-markup-semantics.md).
