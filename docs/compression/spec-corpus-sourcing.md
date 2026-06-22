# Corpus Sourcing — Design

> A **multi-source pooled corpus** for v0.4 vocabulary identification. Replaces the
> single-register (YouTube-ASR-only) input that biases today's frequency-driven
> dictionary. Feeds the counters → builder → test-group harness
> ([spec-dict-testgroups.md](spec-dict-testgroups.md)). The same machinery, with a
> domain-filtered corpus, produces the v0.5 expert dictionaries.
>
> **Status: design — pre-code.** Plan for review. Does not change the locked v0.3
> dictionary. Last updated: 2026-06-19.

---

## 1. Why pool sources

Today's vocab is learned from ~YouTube ASR only — one register (conversational,
no punctuation, filler/discourse-heavy). That over-represents spoken filler and
under-represents encyclopedic, technical, and literary vocabulary. Pooling
diverse registers is what "better vocabulary" means: broader coverage, less
register bias, and a frequency signal robust across many sources.

---

## 2. Two classes of source (used differently)

```
FREQUENCY sources    "what is common, and in which register"  -> pooled + counted.
AUTHORITY/COVERAGE   "what is important even if rare"          -> guarantee
sources              inclusion + de-bias frequency; NOT counted as raw frequency.
```

This split is the heart of the design. Raw counts alone will never surface a
correct-but-rare domain term; an authority list alone has no usage weighting.
We use both: pool frequency sources for the signal, consult authority sources for
inclusion + baselines.

---

## 3. Source inventory (v0.4 general pool — all tiers in scope)

| Source | Register / contributes | Class | License (review) |
|---|---|---|---|
| YouTube transcripts (have) | conversational, fillers, ASR | frequency | per-channel; derived stats |
| Podcast / lecture / interview transcripts | spoken, long-form | frequency | varies |
| Subtitles (OpenSubtitles) | dialogue at scale | frequency | OpenSubtitles terms |
| Wikipedia + Simple Wikipedia | encyclopedic, entities, domain nouns | frequency | CC-BY-SA |
| Wiktionary | lexical breadth (headwords) | authority | CC-BY-SA |
| News (CC-News / GDELT) | current events, named entities | frequency | source-varies |
| Books (Project Gutenberg) | rich literary prose, long-range | frequency | public domain |
| arXiv / PubMed abstracts | technical/scientific jargon | frequency | per-paper / varies |
| StackExchange | technical Q&A, code-adjacent | frequency | CC-BY-SA |
| GitHub READMEs/docs | tech tokens (css, regex, tcp…) | frequency | per-repo |
| Domain glossaries (UMLS, MDN, finance) | domain canon | authority | source-varies (UMLS restricted) |
| Reddit / forums | slang, emerging terms | frequency | reddit terms |
| WordNet / ConceptNet | lexical authority / inclusion | authority | WordNet / CC-BY-SA |
| SUBTLEX / Google Books Ngrams | general frequency baseline (de-bias) | authority | academic / Google |

> Licensing note (not legal advice): the dictionary stores **derived n-gram
> surfaces + counts**, not redistributed source text — generally lower-risk than
> republishing corpora, but share-alike (CC-BY-SA) and restricted sources (UMLS)
> need a deliberate review before a *shipped* dictionary. Tag every source with
> its license in the manifest so this is auditable, and keep the option to build
> a "clean-license-only" variant.

---

## 4. Source-adapter architecture

Each source becomes a **SourceAdapter** that yields a uniform record, exactly
mirroring today's `corpus_scanner.py` (which is the YouTube adapter):

```python
# adapter contract (generalizes corpus_scanner.ChunkRecord)
SourceRecord = (source_id: str, doc_id: str, clean_text: str, meta: dict)

class SourceAdapter:
    source_id: str          # "youtube" | "wikipedia" | "news" | "gutenberg" | …
    license: str            # tagged into the manifest
    def scan(self) -> Iterator[SourceRecord]: ...   # clean text, deduped per source
```

```
adapters/                       one per source (youtube = today's corpus_scanner)
   youtube.py  wikipedia.py  news.py  gutenberg.py  arxiv.py  stackexchange.py …
        │  each: raw → cleaned token stream (+ per-source dedup)
        ▼
pool_counter   →  per-(surface, source) counts  +  per-(phrase, source) counts
        │        (extends word_frequency_counter.py / ngram_counter.py)
        ▼
weight + merge →  word_frequencies.txt  +  phrase_candidates.txt   (+ source columns)
        ▼
builder / test-group harness
```

`corpus_scanner.py` stays as the `youtube` adapter; new sources are new adapters
implementing the same contract. The counters extend to record the **source** of
each count (one extra dimension), which everything downstream needs (§6).

---

## 5. Pooling: weight, don't concatenate

Raw concatenation lets the largest source dominate (Common Crawl / Wikipedia
would swamp transcripts). Instead:

```
- Per-source TOKEN BUDGET / cap: each source contributes up to a target token
  count (or a weight multiplier) so the register MIX is intentional, not an
  accident of which corpus is biggest.
- Store BOTH raw per-source counts and the weighted merged count. The merged
  frequency = Σ_source ( weight_source × count_source ).
- Case folding for counting (lowercase), consistent with the dict storing
  lowercase + caps_codec cap-prefix at compression time.
- Mixed registers (ASR no-punct vs clean punctuated) are fine: the tokenizer is
  byte-class based; counting keys on the tokenized surface.
```

The weights are a tunable — and become a **test-group axis**: a group can be a
sourcing recipe ("transcripts+wiki" vs "+news" vs "+books") compared on
ratio/coverage/OOV in the harness.

---

## 6. Per-source counts → dispersion (the quality signal)

Tracking counts per source is not bookkeeping — it powers selection quality:

```
dispersion(surface) = # distinct sources containing it
  - high dispersion  → robust general vocabulary (keep, favor for scarce tiers)
  - one-source spike → register artifact / spam (down-weight)
```

This is exactly the harness's `S4 dispersion` strategy input. It also gives
**provenance**: each dictionary entry can record which sources/domains attest it
(audit trail the roadmap's sourcing strategy wants, and the basis for expert
dictionaries).

---

## 7. Dedup

```
- Per-source, at scan time: boilerplate (wiki markup/nav, news syndication
  furniture), and ASR boundary-repeats (already handled in corpus_scanner).
- Cross-source near-dup: minhash/simhash on documents to drop the same article
  syndicated across news, or a transcript that is also a published article.
- Goal: counts reflect distinct usage, not copy-paste amplification.
```

---

## 8. Authority/coverage usage (not raw frequency)

```
- INCLUSION lists (Wiktionary/WordNet/glossaries): guarantee a correct-but-rare
  term gets a dictionary entry even if its pooled frequency is low (assigned to
  the appropriate tier by its real frequency / a floor).
- FREQUENCY BASELINES (SUBTLEX/Ngrams): compare pooled frequency to a general
  baseline to detect + correct corpus bias (e.g. our corpus over-weights a topic).
- These never inflate counts; they gate inclusion and sanity-check the signal.
```

---

## 9. Integration points

```
- Counters: extend word_frequency_counter.py + ngram_counter.py to emit a SOURCE
  dimension (per-source counts) and a merged weighted total. phrase_miner.py PMI
  is computed on the pooled stream.
- Inputs: word_frequencies.txt / phrase_candidates.txt gain source columns
  (back-compatible: builder can ignore them; harness strategies use them).
- Builder: unchanged contract; consumes the merged frequency (the test-group
  harness's bytes_saved / pmi / dispersion strategies consume the extra columns).
- Harness: source-mix + weights become a test-group axis (spec-dict-testgroups.md).
- v0.5 experts: the same adapters + counters over a domain-filtered source set
  produce a domain corpus → an expert dictionary.
```

---

## 10. Build order (when we implement)

```
C1  [DONE 2026-06-19] SourceAdapter contract + refactor corpus_scanner.py to be the `youtube` adapter
    (no behavior change). Gate: youtube counts reproduce today's.
C2  [DONE 2026-06-19] Counter extension: pool_counter.py — per-source counts +
    weighted merge + dispersion. Gate: youtube-only pool reproduces the counter
    (test_pool_counter, 2/2). CLI defaults to a safe dev file; --canonical to
    write the builder input.
C3  [DONE 2026-06-19] Wikipedia adapter end-to-end (wikipedia_adapter.py:
    clean_wikitext + bundled sample) + license tag. Gate: two-source pool
    (youtube+wikipedia) dispersion populated; cleaner strips markup
    (test_wikipedia_adapter, 3/3). Real dump acquisition + full license manifest
    still to wire for a production pooled build.
C4  Add news + books + technical adapters incrementally; each gated on dedup +
    license tag + a sanity diff of the pooled vocab.
C5  [PARTIAL 2026-06-19] Authority/coverage hookup. DONE: Wiktionary category
    word-base harvester (wiktionary_categories.py) — walks Category:en:* into a
    topic-labeled inclusion list (sample mode offline; --live MediaWiki path for a
    network env). TODO: builder inclusion hook (guarantee-include at floor freq +
    topic provenance) + SUBTLEX baseline de-bias report.
C6  Feed the pooled inputs into the test-group harness; compare source-mix groups.
```

C1–C2 are the load-bearing prerequisites (everything else is "add an adapter").

---

## 11. Open decisions

```
O1  Per-source weighting: equal token budgets, or register-tuned weights? Start
    equal-budget caps, then tune via harness source-mix groups.
O2  Adapter acquisition: bulk dumps (Wikipedia/Gutenberg/CC-News) vs APIs vs the
    existing web_fetch path — per source. Recommend offline dumps for scale +
    reproducibility.
O3  Clean-license-only variant: build it in parallel from C3 so a shippable
    dictionary always exists? Recommend yes.
O4  Dispersion granularity: per-source, or per-source-AND-domain (finer, feeds
    experts)? Recommend per-source now; add domain when experts start.
O5  Target total corpus size / per-source caps for the first pooled build.
```

---

## 12. Pointers

```
Current scanner    semantic_compression/corpus_scanner.py  (the youtube adapter)
Counters           word_frequency_counter.py ; ngram_counter.py ; phrase_miner.py
Builder            dictionary_builder_v03.py
Test-group harness docs/compression/spec-dict-testgroups.md
Process            PROCESS.md (step 1 INGEST, step 4 SELECT)
v0.5 experts       docs/ROADMAP-SEMANTIC-COMPRESSION.md §4–§5.6 (authority sourcing)
```
