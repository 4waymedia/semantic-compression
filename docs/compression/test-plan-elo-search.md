# Test Plan — ELO File Search + Summary Benchmarks

### Comparing search/summary on ELO-packaged corpora vs raw text and general compressors, across dictionaries and experts

> Status: draft instructions. Reference harness exists
> (`semantic_compression/bench_elo_search.py`); full 1,000-file run and the
> per-expert matrix are to be built out. Preliminary numbers below are from
> completed 40- and 200-file runs.

---

## 1. What these tests prove

The thesis: **the codebook is the index.** Because every token already carries
a compact Base64 ID in the frozen dictionary, two operations that normally
require a model or a separate index become near-free:

- **Search** — translate a query term to its dictionary ID once, then look it up
  in a tiny ID inverted index. No text reconstruction, no model. Search time is
  ~constant in corpus size, where text-scanning methods grow with it.
- **Summary** — pull the salient tokens (rare-by-tier + phrase atoms) straight
  out of the compressed `.elo` stream. Zero inference. **No general compressor
  (gzip/zstd) can summarize from compressed bytes at all** — they must fully
  decompress first and then run NLP.

The tests quantify both against fair external baselines, and verify correctness
against plaintext ground truth.

---

## 2. Metrics

```
Disk footprint      raw .txt vs .elo (text) vs .eloB (binary) vs .gz vs .zst
Conversion/index    one-time cost: raw -> .elo, plus building the ID index
Search latency      per-query and total, each method, over the corpus
Correctness         precision/recall of each method vs whole-word, case-
                    insensitive plaintext ground truth (incl. OOV reporting)
Summary throughput  ms/file to produce the zero-inference extractive summary
```

Reported per run with the corpus size N, the dictionary identity + fingerprint,
and the exact term set (so any run is reproducible and attributable to a
specific codebook).

---

## 3. Methods compared (search)

```
raw_python    regex whole-word scan over the .txt files            (baseline)
ripgrep       rg -w -i -F over the .txt corpus                     (fast native baseline)
gzip_decode   decompress each .gz, then regex scan                 (general compressor)
zstd_decode   decompress each .zst, then regex scan                (general compressor)
elo_decode    decode each .elo to text, then regex scan            (naive ELO; proves not-slower + lossless)
elo_index     term -> ID, look up the ID inverted index            (HEADLINE: search in ID space, no decode)
```

`elo_index` is the claim under test. The decode/decompress methods all share the
plaintext correctness by construction; the research question is whether
`elo_index` matches plaintext recall while being dramatically faster.

---

## 4. Data preparation (the important part)

Each test run is defined by a **(dictionary, corpus, term set)** triple. To
compare dictionaries or experts you vary one axis and hold the others fixed.

### 4.1 One-time corpus conversion

For a chosen dictionary D and a chosen set of source transcripts:

```
for each transcript:
    extract plain text   = concat(chunk.text for chunk in chunks)   -> data/txt/<id>.txt
    encode with D        = Compressor(D).encode_text(text)          -> data/elo/<id>.elo
    gzip / zstd the text                                            -> data/gz, data/zst
    parse the .elo stream, record token IDs                         -> data/index.json (ID -> [file])
```

Notes that make the comparison fair and correct:

- **Search the same content in every form.** Use the transcript's concatenated
  chunk text as the canonical document, so raw / gz / zst / elo all search
  identical content. (Encoding the full JSON would inject keys/timestamps and
  muddy term search.)
- **Whole-word ground truth.** ELO tokens are word units, so ground truth is a
  whole-word, case-insensitive match (`\bterm\b`). Substring matching inside a
  token (`cat` in `category`) is a different operation ELO does not do without a
  decode pass — document it, don't hide it.
- **Phrase expansion in the index.** Phrase atoms absorb their constituent
  words. When indexing, for every phrase ID present also add its constituent
  word IDs (decode phrase surface -> tokenize -> forward-lookup each word).
  This keeps word-level recall aligned with plaintext.
- **OOV terms are reported, not dropped.** A query term not in D has no ID; mark
  it as a decode-fallback case in the results rather than silently failing.

### 4.2 Term set design

Every run uses a balanced term set so results are not cherry-picked:

```
common in-vocab     freedom, market, government        (short IDs, dense postings)
rarer in-vocab      infrastructure, accountability     (long IDs, sparse postings)
multiword / phrase  wall street, the thing is          (exercises phrase expansion)
out-of-vocabulary   one nonsense token                 (exercises fallback path)
domain-specific     (per expert) e.g. cooking: mise en place, cast iron
```

### 4.3 Preparing data for DIFFERENT dictionaries / experts

This is the cross-dictionary matrix. An **expert** is a fully separate
dictionary built over a domain-filtered corpus (see `spec-tags-db.md` §Experts).
Two comparison axes:

**Axis A — same corpus, different dictionaries.** Hold the corpus fixed; rebuild
the `.elo` + index with each dictionary. Answers: *does the expert dictionary
search its own domain faster / with better recall / smaller index than the
general dictionary?*

```
corpus_cooking/          (held-out cooking transcripts, fixed)
  run with D=general  -> results_general.json
  run with D=cooking  -> results_cooking.json
compare: latency, index size, recall, OOV rate, summary quality
```

**Axis B — each expert on its own domain vs general baseline.** Each expert gets
a domain-matched held-out corpus and a domain term set; the general dictionary
is the common baseline across all of them.

```
experts/cooking/   corpus=cooking held-out   terms=general + cooking-specific
experts/military/  corpus=military held-out  terms=general + military-specific
baseline:          general dictionary on every expert's corpus
```

Data-prep checklist per dictionary/expert under test:

```
[ ] the dictionary LMDB, frozen and version-tagged (see §6)
[ ] a held-out corpus subset matched to that dictionary's domain
    (held out = NOT in the dictionary's mining corpus, to avoid train/test leak)
[ ] a term set: general terms + domain-specific terms + an OOV term
[ ] plaintext ground truth generated from the held-out corpus
[ ] record dictionary_id + fingerprint with every results file
```

Keep one directory per (dictionary, corpus) so runs never cross-contaminate:

```
bench/
  general__newscorpus/   txt/ elo/ gz/ zst/ index.json results.json
  cooking__cookingheld/  txt/ elo/ gz/ zst/ index.json results.json
  military__milheld/     ...
```

---

## 5. Running the harness

The reference script is staged so each stage is independent and re-runnable;
artifacts persist on disk between stages.

```
python -m semantic_compression.bench_elo_search build   --n 1000 --data bench/general__news
python -m semantic_compression.bench_elo_search search  --data bench/general__news
python -m semantic_compression.bench_elo_search summary --data bench/general__news --show 5
python -m semantic_compression.bench_elo_search report  --data bench/general__news
```

To run a different dictionary/expert, point the harness at that dictionary's
LMDB and a domain-matched corpus, writing to a separate `--data` directory.
(Adding a `--dict <lmdb_path>` and `--src <transcript_glob>` flag to the build
stage is a small build-out item — see §8.)

Operational note: the build is the long stage (encoding scales with text
volume). It is resumable — re-run `build` to continue — and flushes progress
periodically, so it can be driven in chunks under a time budget.

---

## 6. Dictionary versioning for testing (frozen-per-version)

"The dictionary is frozen" has meant one immutable artifact, which has been
friction during active development. Reframe it as **frozen *per version*** —
each build is immutable and tagged, and new versions are created freely for
testing without disturbing any version already referenced by encoded files.

```
Rule 1  Every dictionary build is immutable once tagged. Never mutate in place.
Rule 2  A new build = a new version + a new content fingerprint, side by side.
        e.g. db/dictionary.lmdb              -> general / v0.3 (production-frozen)
             db/experts/cooking/dictionary.lmdb -> cooking / v1
             db/dev/general-v0.4-test/        -> a throwaway test version
Rule 3  Encoded artifacts carry the (dictionary_id, version, fingerprint) that
        produced them, so any .elo decodes against exactly its codebook
        (see spec-tags-db.md §Identity). Test versions never endanger v0.3 files.
Rule 4  A test version is promoted to "frozen production" only by tagging; until
        then it lives under db/dev/ and is fair game to rebuild or delete.
```

This is what makes the cross-dictionary tests above safe: spinning up a v0.4
test dictionary, or a new expert, is just another versioned build under
`db/dev/` or `db/experts/` — the production v0.3 contract is untouched.

> Real-time vector inspection: the new testing pipeline can view vectors live,
> which pairs with this — a test dictionary version can be inspected (EPA /
> embedding space) before it is ever promoted, and the same `(id, version,
> fingerprint)` identity ties a vector view back to the exact codebook.

---

## 7. Preliminary results (honest, partial)

From completed runs on the general v0.3 dictionary. The full 1,000-file run and
the per-expert matrix are still to be completed.

**Disk footprint (200 files, 6.14 MB raw text):**

```
  txt   1.00x      elo (text) 0.51x      .eloB (binary) 0.38x
  gz    0.21x      zst 0.19x             elo ID-index 0.33x
```

ELO is *not* the smallest on disk — gzip/zstd are entropy coders and win on raw
bytes. ELO's value is being lossless **and** queryable **and** summarizable
without decompression; the ID index (0.33x) is a searchable structure, not just
stored bytes.

**Search latency + correctness (200 files, 13 terms):**

```
  method        ms/query   precision  recall   vs raw
  raw_python      2.36       1.00      1.00     1.0x
  ripgrep        ~30 (subprocess startup-bound on small corpora)
  gzip_decode     4.57       1.00      1.00     0.5x
  elo_decode      7.26       1.00      1.00     0.3x
  elo_index     ~0.00        1.00      0.95     instant (postings lookup)
```

`elo_index` returns essentially instantly and is independent of corpus size,
where every text-scanning method grows with it — the gap widens as N grows.
Recall 0.95 (not 1.00) is fully explained: the only misses were `government`
(12/13 files) and `wall street` (2/3), both phrase/tokenization-boundary
effects. Precision is perfect.

**Summary (zero-inference, from the compressed stream):** ~1.5 ms/file; output
is coherent — e.g. a Gaza-flotilla transcript surfaced "fleet of, on a mission
to, to gaza, was carrying, the raid" with no model in the loop.

---

## 8. Build-out TODO

```
[ ] Complete the 1,000-file general run (build is resumable; finish + record).
[ ] Add build flags: --dict <lmdb> and --src <glob> so any dictionary/expert
    and corpus can be benchmarked without code edits.
[ ] Implement the cross-dictionary matrix (Axis A + Axis B) with one --data dir
    per (dictionary, corpus) and a roll-up comparison report.
[ ] Stamp dictionary_id + version + fingerprint into every results.json.
[ ] Close the phrase-recall gap: conjunctive multi-ID lookup for multiword
    queries + finer phrase decomposition; re-measure recall.
[ ] Pack the ID index (it is currently JSON for readability; a packed postings
    file would shrink it well below the 0.33x raw shown above).
[ ] Add a fixed seed/term-set file per domain so runs are byte-reproducible.
[ ] Investigate the one transcript that stalled text-mode encode (a per-file
    timeout guard is in the harness; root-cause the input separately).
```

---

## 9. Semantic search track (concept-level retrieval)

Beyond exact-term search, ELO supports *semantic* retrieval — find files by
meaning, not exact word — and there is a System-1-native form that is cheaper
than standard dense retrieval because the codebook already carries a vector per
ID. `library_builder.py` embeds every dictionary entry (word + phrase) with
all-mpnet-base-v2 (768-d, L2-normalized), projects EPA, and builds a FAISS
`IndexFlatIP`; each entry's `vector_id` is its FAISS row. So the codebook is
`surface ↔ ID ↔ vector ↔ EPA`. (CLAUDE.md lists the FAISS index as a System 1
deliverable.)

### Tracks (mark the System 1/2 boundary)

```
L0_lexical    exact term -> ID -> postings                         System 1 (already tested)
L1_codebook   query -> embed -> dictionary-FAISS kNN (cosine) ->   System 1: uses DELIVERED dictionary
              expanded ID union -> postings                         vectors; NO document embedding
L1_tags       L1_codebook + tag bucket/cue filter or boost +       System 1, but needs the tags DB
              expert routing                                        (spec-tags-db.md) — deferred until built
L2_dense      embed each doc/chunk -> FAISS, OR aggregate a         System 2 (DEFERRED): document vectors /
              doc's token vectors into a cheap doc-vector           EPA-relational retrieval
```

L1_codebook is the headline novelty and is testable now: embed only the short
query (one forward pass), search the dictionary FAISS for the nearest IDs
(synonyms/related concepts above a cosine threshold), union those IDs' postings
from the same index used for exact search. "car" pulls in vehicle/automobile/
truck because their codebook vectors are near — no per-document embedding, no
model at index time. The "documents" searched in vector space are the codebook
entries themselves (words + phrase atoms), not the transcripts — that is the
entire cost asymmetry vs ordinary dense retrieval.

### L1 implementation notes

```
Pipeline    query -> normalize (lowercase + tokenize, optional phrase detect)
            -> embed (all-mpnet-base-v2, normalize_embeddings=True)
            -> dictionary FAISS top-k -> threshold filter -> map vector_id->ID
            -> union postings -> (optional) tag/EPA re-rank -> rank
Similarity  library_builder uses IndexFlatIP over L2-normalized vectors, so the
            FAISS score IS cosine already — NO conversion. Results come back in
            DESCENDING similarity, so break once score < threshold.
Threshold   start cosine >= 0.70 (sane band 0.65-0.75); make it configurable.
Cap         top-20..50 neighbors to prevent query drift / precision collapse.
Phrase      a CONCEPT (Tier-4) neighbor: expand to constituents or keep atomic
            depending on the query; record which policy was used.
Expert      reuse the §4 router; the cooking expert's FAISS returns better
            culinary neighbors than general — that IS the cross-expert test.
Mapping     expose vector_id <-> dictionary-ID from library_builder (FAISS row i
            == the entry with vector_id i; build the reverse array once at load).
```

> **Critical integration gotcha — ONE frozen version across BOTH artifacts.**
> There are two dictionary artifacts: the **LMDB** (`forward`/`reverse`) the
> postings index is built from, and **`canonical.db` + `faiss.index`** (vectors /
> `vector_id`) that L1 searches. L1 only works if an ID means the same token in
> both — i.e. both were built from the SAME frozen dictionary version. In the
> current repo they were built on different dates and almost certainly do NOT
> align; assuming they do yields plausible-looking garbage. The §6
> frozen-per-version + fingerprint rule is the guard: pin one version, build the
> LMDB and the FAISS from it, and record the fingerprint with every L1 result.

### Baselines

```
BM25 / exact-term         lexical floor (the L0 elo_index)
Dense retrieval (SOTA)    sentence-transformers embed query AND all docs -> FAISS
                          over document vectors — the reference everyone ships
ELO L1 / ELO L2           the methods under test
```

### Metrics + ground truth

Semantic relevance has no regex ground truth, so use one or more of:

- **Synonym-group proxy.** A query with a known related-term group; "relevant" =
  files containing any group member (whole-word). Measures whether L1 recovers
  the group's files that exact search misses.
- **Agreement-with-SOTA.** Treat full dense retrieval's top-k as the reference;
  measure L1's recall@k / overlap against it. Claim under test: L1 recovers a
  large fraction of dense-retrieval's hits at a fraction of the cost (no document
  embedding, tiny index).
- **Curated judgments.** A small hand-labeled query→relevant-files set for
  defensible absolute precision/recall@k.

Plus, for every method: query latency (embed + FAISS + postings), index build
time, index storage — the cost contrast is part of the result. For L1
specifically also record:

- **Expansion factor** — average number of IDs the query expands to. Ties
  directly to the precision/recall trade and to drift.
- **Precision drop vs L0** — expansion buys recall at the cost of precision
  (e.g. "bank" pulling in both finance and river senses). This is the number a
  skeptic will probe; report it, don't bury it.
- **Cross-expert neighborhood delta** — how the expanded ID set shifts between
  general and the domain expert for the same query (the cross-expert test).

### Data prep additions (on top of §4)

```
[ ] Build the FULL dictionary FAISS for the dictionary under test (db currently
    holds a small test_faiss.index; a full library_builder run is the prereq).
[ ] A semantic query set: concept queries each with a related-term group and/or
    curated relevant files — general + domain-specific groups.
[ ] For the SOTA/L2 baseline only: embed the corpus once. This is the expensive
    step L1 avoids — time and store it so the cost contrast is explicit.
```

### Cross-expert semantic test (the strong one)

Polysemy makes this vivid: the same query resolves to different concept
neighborhoods in different expert dictionaries because each expert's vectors are
domain-tuned.

```
"cell"          biology expert  -> neuron, tissue, membrane
                telecom expert   -> tower, coverage, signal
"bank"          finance expert   -> account, deposit, lender
                geography expert -> river, shore, embankment
"mise en place" cooking expert   -> rich culinary neighborhood
                general          -> sparse / near-OOV
```

Run L1 for each query across general + each expert dictionary and compare the
retrieved concept set and files. This tests, in one shot, that experts deliver
domain-correct *semantics*, not just domain compression — the polysemy cases
(`cell`, `bank`) are the clearest demonstrations.

### Hook to the live vector pipeline

The real-time vector viewer pairs with L1 directly: visualize the query point,
its FAISS neighborhood, and the retrieved concept cluster, each tagged with the
dictionary `(id, version, fingerprint)` so the view is attributable to an exact
codebook. Useful for tuning the cosine threshold and for showing *why* a file
was retrieved.

### Build-out TODO (semantic)

```
[ ] Pin ONE frozen dictionary version and build BOTH the postings LMDB and the
    FAISS/canonical.db from it; verify ID alignment + record the fingerprint
    (the §9 critical gotcha — do this first or everything below is invalid).
[ ] Expose the vector_id <-> dictionary-ID map from library_builder (reverse
    array, loaded once).
[ ] Build full dictionary FAISS per tested dictionary/expert.
[ ] Add a `semantic` stage: query -> embed -> dictionary-FAISS top-k -> threshold
    filter -> union postings, with configurable cosine threshold (~0.70) and a
    top-20..50 cap.
[ ] Add the L1_tags variant once the tags DB exists (bucket/cue filter + boost,
    expert routing) — deferred to the tags build.
[ ] Implement the L2 cheap doc-vector (aggregate token vectors by tier/rarity)
    and compare to full dense retrieval.
[ ] Assemble the semantic query set (incl. cell/bank polysemy + domain terms)
    + proxy/curated ground truth.
[ ] Report L1 recall@k vs the dense-retrieval reference AND precision drop vs L0,
    with the cost contrast and the per-query expansion factor.
```

---

## 10. Caveats (state these in every results writeup)

```
- ELO ID-search is WHOLE-WORD; it does not do mid-token substring matching
  without a decode pass.
- gzip/zstd beat ELO on raw on-disk size; ELO's edge is lossless + queryable +
  summarizable without decompression, not byte size.
- Recall < 1.0 for elo_index comes from phrase/tokenization boundaries, not
  random error — report which terms missed and why.
- ripgrep is subprocess-startup-bound on small corpora; it is the right
  baseline to watch as N scales to tens of thousands of files.
- Numbers are dictionary-version specific. Always record the dictionary id +
  fingerprint with the result.
```

---

## 11. References

```
Harness         semantic_compression/bench_elo_search.py
Tags + experts  docs/compression/spec-tags-db.md
Compressor API  semantic_compression/compressor.py (encode_text/decode_text, .elo)
Vectors/FAISS   semantic_compression/library_builder.py (embed + EPA + IndexFlatIP)
v0.4 spec       docs/compression/spec-v0.4.md
```
