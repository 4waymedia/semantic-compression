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
