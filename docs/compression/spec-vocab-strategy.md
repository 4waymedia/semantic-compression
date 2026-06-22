# Vocabulary Strategy — Compression-Aware Selection + Expansion

> How we decide **which surfaces earn dictionary entries** (and the scarce short
> IDs), and **how we grow the candidate pool** so the right surfaces exist to be
> chosen. Two coupled levers feeding the builder + the test-group harness
> ([spec-dict-testgroups.md](spec-dict-testgroups.md)) and the multi-source pool
> ([spec-corpus-sourcing.md](spec-corpus-sourcing.md)).
>
> **Status: design — pre-code for the new pieces.** The byte-cost facts below are
> VALIDATED against the current code; the new strategy/expansion work is a plan.
> Does not change any locked dictionary. Last updated: 2026-06-20.

---

## 0. The two levers (don't conflate them)

```
EXPANSION   what is even in the candidate pool        (Part B)
            -> grows COVERAGE: more correct surfaces available to pick from
SELECTION   which candidates get an entry / short ID  (Part A)
            -> spends a FIXED tier budget for maximum compression
```

A char-N build has fixed capacity (Tier 1 = 1,280; Tier 2 = 81,920; Tier 3 =
5,242,880 — `config.py`). Expansion decides *what could go in*; selection decides
*what actually goes in, and at which tier*. They meet in the builder: the pool is
ordered by a `select_strategy`, then sliced top-down into the tiers up to
`max_tier`. Optimizing one without the other is wasted: a richer pool with a
frequency-only ranking still spends short IDs on short common words; a smart
ranking over a thin pool still can't include terms that were never mined.

---

# PART A — Compression-Aware Selection

## A1. The problem with frequency-only ranking

Today the builder competes words and phrases and orders the pool by **raw
frequency** (`score_by_frequency`, the S0 baseline). Frequency ignores **surface
length**, but compression payoff depends on both:

- A short common word (`the`, 3 bytes) is high-frequency but saves little per
  occurrence — its ID is already 2 bytes, so the win is ~1 byte each.
- A long domain word (`mitochondrion`, 13 bytes) is lower-frequency but saves
  ~9–11 bytes per occurrence when promoted to a 3–4-byte ID.

Frequency-rank selection systematically over-pays short words into the scarce
char-2/char-3 tiers and under-includes long, compressible terms. The phrase
miner already knows this — it ranks phrases by **bytes saved**, not frequency
(`phrase_miner.score_candidates`). Words should be ranked the same way.

## A2. The byte-cost model (VALIDATED against code)

The wire cost of a token is its tier ID width; an out-of-dictionary token falls
back to byte-fallback (one fallback token per UTF-8 byte). From
`ngram_counter.py` / `phrase_miner.py` / `config.py`:

```
Tier 0 (1-char ID)   1 byte
Tier 1 (2-char ID)   2 bytes
Tier 2 (3-char ID)   3 bytes
Tier 3 (4-char ID)   4 bytes
OOV surface          len(utf8 bytes)         (byte-fallback path)
```

The phrase miner's current model (`WORD_AVG_BYTES = 1.8`, `PHRASE_COST_BIN = 3`):

```
savings_each(phrase)  = n * 1.8 - 3            # n words, conservative Tier-2 ID
savings_total(phrase) = freq * savings_each
score(phrase)         = savings_total * (1 + max(0, pmi)/10)
```

And the existing zero-savings guard (`drop_tier0_bigrams`): a 2-gram of two
Tier-0 words costs 1+1 = 2 bytes today and a Tier-1 phrase ID also costs 2 — net
zero, so it is dropped. That guard is a **special case of the general objective
below.**

## A3. The objective: bytes_saved (S1)

Score every candidate — word OR phrase — by the bytes it removes from the corpus
when promoted to its prospective ID:

```
bytes_saved(surface, tier) = freq(surface) * ( cost_now(surface) - id_width(tier) )

  words:    cost_now = len(utf8(surface))           # its byte-fallback cost
  phrases:  cost_now = sum over constituent words of their CURRENT encoded cost
                       (under a frequency baseline build), not raw utf8
  id_width: Tier 1 = 2, Tier 2 = 3, Tier 3 = 4
```

Properties that make this the compression-true objective:

- **Length-aware:** rewards long surfaces the frequency model ignores.
- **Tier-aware:** the same surface saves less at a deeper (wider-ID) tier, which
  correctly discourages pushing a marginal surface into Tier 3.
- **Self-pruning:** if `cost_now <= id_width(tier)`, `bytes_saved <= 0` — the
  surface should NOT get an entry at that tier. This subsumes
  `drop_tier0_bigrams` and also blocks promoting 1–2 char words into Tier 3
  (a 4-byte ID for a 2-byte word is a net loss).

> **Open (O3 in spec-dict-testgroups):** the phrase `cost_now` should price
> constituents at their cost under a reference (frequency-baseline) build, so the
> delta is realistic rather than raw-utf8. Needs one reference build to price
> against.

## A4. Strategy ladder (the test-group axis)

Each is a pure, deterministic scorer plugged into `build(select_strategy=…)`.
S0 is the control; S1 is the hypothesis; S2–S4 layer quality signals.

```
S0  frequency        -freq                                    (current baseline)
S1  bytes_saved      freq * (cost_now - id_width)             THE objective (A3)
S2  pmi_weighted     bytes_saved * f(pmi)                     favor cohesive phrases
S3  facet_aware      bytes_saved * utility_weight             down-weight FILLER, etc.
S4  dispersion       bytes_saved adjusted by #sources/#docs   penalize 1-source spikes
```

S3 reads the facets `utility` field (CONTENT/FUNCTION/FILLER/STRUCTURAL); S4
reads per-source dispersion from `pool_counter` (spec-corpus-sourcing §6).

## A5. Guardrails (must hold, not optimized)

```
G-neg   no entry with bytes_saved <= 0 at its assigned tier (generalized drop_tier0_bigrams)
G-rt    round-trip byte-exact regardless of selection (verify_lossless)
G-cap   tier capacities respected (Tier 1 <= 1,280; Tier 2 <= 81,920)
G-det   deterministic: same inputs + strategy -> identical fingerprint
G-res   reserves honored at every size (tier1_word_reserve; forced seeds)
```

The Tier-1 word reserve stays: even under bytes_saved, we keep the top-N single
words in char-2 so the most common words never get demoted by greedy phrases.
The reserve size becomes a per-group parameter to sweep.

## A6. Implementation

```
1. Add score_by_bytes_saved(surface, freq, kind, n, pmi) to dictionary_builder_v03.
   (Builder already accepts select_strategy; this is a new scorer + the tier-aware
    cost. Expose pmi to the scorer signature — currently (surface, freq, kind, n).)
2. Make id_width tier-aware inside _assign so the score reflects the tier a
   surface would actually land in (two-pass: provisional tier -> rescore -> assign),
   or approximate with the shallowest legal tier for a first cut.
3. Encode G-neg as a hard filter in build() (drop_tier0_bigrams becomes its n==2
   special case).
4. Run S0 vs S1 at char-2 + char-3 in the harness; rank on M1 (ratio), with
   M2/M3 (char-2/char-3 yield) and M4 (OOV) as the tie-breakers (spec-dict-testgroups §7).
```

Adoption is deliberate: the harness recommends, a human rebuilds the real dict
with the chosen scorer, then the PROCESS.md §7 cascade (facets → profiles → retrain).

---

# PART B — Vocabulary Expansion

## B1. Why expand (and how it differs from selection)

The corpus is YouTube-ASR-dominated: one register, filler-heavy, thin on
encyclopedic/technical/literary vocabulary (spec-corpus-sourcing §1). Expansion
raises **coverage** so OOV (M4) drops and the selector has correct surfaces to
choose from. Two source classes, used differently:

```
FREQUENCY sources    pooled + counted -> the usage signal (transcripts, wiki, news, books…)
AUTHORITY sources    guarantee inclusion of correct-but-rare terms; NOT counted
                     as raw frequency (Wiktionary, WordNet, glossaries)
```

Selection still applies after expansion: an authority term enters the pool, but
whether it earns a short ID is decided by bytes_saved (Part A) at its real /
floor frequency.

## B2. Wiktionary as an authority/coverage source — full forest

`wiktionary_categories.py` already walks a category tree from one `--root` into a
topic-labeled word base (`harvest_sample` offline; `harvest_live` via the
MediaWiki API). Today it is run per-root (e.g. `en:Sciences`). The expansion is to
harvest the **whole `Category:en:*` forest** and use **the category and
sub-category names themselves**, not only the leaf terms.

### B2a. Harvest every category, not one root

```
- Enumerate all top-level lexical roots under Category:en:* (e.g. en:Sciences,
  en:Humanities, en:Law, en:Medicine, en:Mathematics, en:Linguistics,
  en:Chemistry, en:Botany, en:Computing, en:Finance, …) and walk each with the
  existing cycle-safe walk_categories(); union the results.
- Keep the per-term topic set (a term reached via multiple paths accrues several
  topics) — already the harvester's behavior.
- LIVE mode acquires it for real; SAMPLE mode stays the offline gate. Run live in
  a network env, snapshot the headword list + a date + license tag, then build
  offline from the snapshot (reproducibility).
```

> Scale is UNVERIFIED until a live walk: en.wiktionary has on the order of 1e6
> English headwords across thousands of categories. Treat the first live harvest
> as a measurement, then decide caps. This is exactly the kind of external/assumed
> number the project policy says to validate against a real run before quoting.

### B2b. Category and sub-category NAMES as vocabulary + labels

Category names are themselves valuable, often multi-word, domain surfaces:
`molecular biology`, `organic chemistry`, `set theory`, `civil procedure`. Use
them two ways:

```
1. As TOPIC LABELS (provenance): the topic path on each term is the basis for
   facet/domain tags and for v0.5 expert-dictionary selection (already produced;
   wire into the builder inclusion hook).
2. As CANDIDATE HEADWORDS: feed normalized category/sub-category names into the
   candidate pool (as authority phrases). Many are high-value multi-word units a
   transcript pool would never surface. They still pass through selection
   (bytes_saved) like any phrase — names that don't save bytes simply don't earn
   an entry.
```

Normalization for names: strip the `en:` / `Category:` prefix (the harvester's
`_normalize_cat`), lowercase, drop administrative categories (e.g. `…by language`,
`Requests…`, `Terms with …`, maintenance trees) via a stop-prefix list so we keep
*lexical* categories, not Wiktionary bookkeeping.

## B3. Inclusion contract (authority → builder)

The mechanism the harvester documents, with the builder hook still TODO
(spec-corpus-sourcing C5):

```
- If an authority term appears in the pooled counts -> it enters at its REAL freq.
- If absent -> it enters at a FLOOR frequency so it still gets an entry at the
  deepest appropriate tier (char-4 in a full build).
- Provenance: source="wiktionary" + topic path stored on the entry (facets/domain).
- Authority inclusion NEVER inflates frequency counts (spec §8) — it gates
  inclusion and sets a floor, it does not add to usage.
```

Consequence for tier placement: floor-frequency terms have tiny `bytes_saved`,
so under S1 they land in the deepest tier (char-4) — correct: rare correct terms
get an entry (coverage) without stealing scarce char-2/char-3 slots from
high-payoff surfaces.

## B4. Quality signals on expanded vocab

```
DISPERSION   #distinct sources a surface appears in (pool_counter). High = robust
             general vocab (favor); one-source spike = register artifact/spam
             (down-weight). Feeds S4.
DE-BIAS      compare pooled frequency to a general baseline (SUBTLEX / Google
             Ngrams, authority) to detect corpus skew; report, don't silently
             reweight. (spec-corpus-sourcing §8)
DEDUP        per-source boilerplate + cross-source near-dup (minhash) so counts
             reflect distinct usage, not syndication. (spec-corpus-sourcing §7)
LICENSE      tag every source (CC-BY-SA share-alike; UMLS restricted) in the
             manifest; keep a clean-license-only variant buildable.
```

## B5. Implementation

```
1. wiktionary_categories: add --all-roots (enumerate Category:en:* lexical roots,
   walk + union) and an administrative stop-prefix filter; emit category NAMES as
   authority phrases alongside leaf terms. Snapshot live output + date + license.
2. Builder inclusion hook: accept a word base (term, topics, source); guarantee-
   include at real-or-floor freq; stamp source + topic provenance into the entry
   (and into facets). (spec-corpus-sourcing C5 TODO)
3. Add the SUBTLEX baseline de-bias report.
4. Re-run the harness: sourcing/expansion recipe becomes a test-group axis next to
   the selection strategy (Part A). Compare on coverage/OOV + ratio.
```

---

## C. How this answers "135k / 235k words"

Expansion supplies the candidate surfaces; selection + build depth decide which
get IDs and where. Cumulative entry capacity: char-2 ≈ 1,333; char-3 ≈ 83,253;
char-4 up to ≈ 5.25M. So:

- 135,000 and 235,000 entries both exceed char-3 (83,253) → they are **char-4
  builds** (a shallow slice of Tier 3: ~52k / ~152k of Tier 3's 5.24M slots).
- Those counts are **entries (words + phrases combined)**. For that many *words*
  specifically, use a word-favoring reserve/strategy; and 235k words exceeds the
  ~206k unique words in the current corpus, so it needs B2 expansion first.
- Under S1 (bytes_saved), the long technical terms B2 adds are exactly the
  high-payoff-per-occurrence surfaces that justify their char-4 IDs, while short
  common words stay in char-2/char-3 where their IDs are narrowest.

---

## D. Pointers

```
Selection      dictionary_builder_v03.py (build(), select_strategy, _assign, TIER_CAPACITY)
Byte cost      phrase_miner.py (WORD_AVG_BYTES, PHRASE_COST_BIN, score_candidates),
               ngram_counter.py (byte-cost model), config.py (tier widths)
Expansion      wiktionary_categories.py (walk_categories, harvest_live/_sample),
               pool_counter.py (dispersion), source_adapters.py / wikipedia_adapter.py
Specs          spec-dict-testgroups.md (harness, S0–S4, M1–M4), spec-corpus-sourcing.md
Process        PROCESS.md (steps 2–5 + the §7 adoption cascade)
```
