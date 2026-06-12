# ELO Compressor v0.4 Specification
### Two Parallel Tracks: Structure-Aware Compression + LLM Retraining Validation

> Status: draft. No code written.
> Predecessor: v0.3.0 (tag `v0.3.0`, commit `0840331`)
> Dictionary: frozen at v0.3 (373,918 entries, 167,275 phrase atoms)
>
> v0.3 achieved 1.99× compression and 55% stream-token reduction.
> The structural ceiling of the fixed-tier byte scheme is now understood
> (see `docs/compression/v0.3-analysis.md`). v0.4 targets the remaining
> headroom via two orthogonal tracks that share the frozen dictionary.

---

## Strategic Intent

v0.4 pursues two independent goals simultaneously:

1. **Compression Track**: Break through the 1.99× ceiling by addressing
   domain-specific redundancies that fixed-tier dictionary encoding
   cannot exploit — JSON field keys, segment templates, timestamp deltas,
   speaker dictionaries. Target ≥ 2.5× on transcript JSON.

2. **LLM Track**: Prove the v0.3 vocabulary contract delivers measurable
   downstream wins in model training. Validate the on-device thesis:
   3B-class models at Tiny/Compact profile fit in 2 GB at int4;
   phrase-atom tokens expand effective context 2-3×.

The two tracks share only the frozen v0.3 dictionary. No code coupling.
Either track can ship independently. Both tracks contribute to the v1.0
production readiness argument.

---

## Context: What v0.3 Proved and What It Couldn't

### Proven (v0.3.0)
- 167,275 phrase atoms mined via PMI + maximal-phrase filtering
- 13/13 files byte-exact round-trip (lossless gate held)
- 55% stream-token reduction (988k → 449k on times_now)
- Decode 6.5 MB/s (2× v0.2)
- 5 frozen LLM vocabulary profiles published (Tiny through Reference)

### Structural Ceiling (identified in v0.3-analysis.md)
- Fixed-tier byte scheme caps per-phrase savings at 1-3 bytes
- High-frequency phrases consist of high-frequency words (already in
  low-cost tiers), so the phrases with the most occurrences save the
  least per occurrence
- Token count drops 55% but average bytes-per-token rises, netting
  only +7% compression
- The 2.5× target is unreachable under the fixed-tier byte format
  without domain-specific structure exploitation

### LLM Track Early Findings (informal, pre-spec)
- Tier 0 WORD_IDS (26 essential words) were not prepended in profile
  construction — fixed by a separate Claude agent
- After fix: ~20% LLM token-count reduction vs BPE (Compact profile),
  **not** 55% (that number was stream-token reduction, a different metric)
- Wire-format bandwidth vs Qwen fixed-width: ~47% reduction
- gzip comparison: ELO 2B fixed is ~5% larger than gzip but stateless,
  semantically searchable, streamable, and deterministic
- The "55% compression" claim in some documents conflates internal
  stream-token count with LLM-token reduction — this spec disambiguates
  permanently

---

## Goals

### Compression Track

```
GC1  Design a structure-aware codec for transcript JSON that exploits
     repeated field keys, segment record templates, timestamp delta
     patterns, speaker labels, and known record shapes

GC2  Implement the codec as a pre-processing layer above the v0.3
     compressor — the compressor sees a transformed byte stream with
     redundancies collapsed

GC3  Achieve ≥ 2.5× average compression ratio on the 3-transcript
     held-out test set (times_now, jocko, julian)

GC4  Preserve byte-exact decode: decode(encode(json_bytes)) ==
     json_bytes for every transcript in the test set

GC5  Measure and report encode/decode throughput; encode ≥ 1.0 MB/s
     at minimum (acknowledging the structure-analysis overhead)

GC6  Document the codec contract so future format readers (C/C++ port)
     can implement the same transform
```

### LLM Track

```
GL1  Formalize the LLM benchmark protocol: define the exact measurement
     methodology for token-count reduction vs baseline tokenizers
     (BPE, SentencePiece, Unigram)

GL2  Build a reproducible HuggingFace-compatible EloTokenizer that wraps
     the v0.3 vocabulary profiles (Tiny, Compact, Standard, Full,
     Reference)

GL3  Retrain a 3B-class model (Qwen2.5-3B or equivalent) against the
     Compact profile vocabulary with frozen embeddings at IDs 0-25
     (the 26 essential Tier 0 words)

GL4  Measure and report:
     a) LLM token-count reduction vs BPE on held-out transcripts
     b) Wire-format bandwidth vs fixed-width token encoding
     c) Downstream perplexity / evaluation benchmark scores
     d) Memory footprint at int4 quantization (embedding + weights + KV)

GL5  Validate the on-device thesis:
     3B Compact profile at int4 fits in ≤ 2 GB total RAM
     7B Compact at int4 fits in ≤ 5 GB (flagship phone)

GL6  Publish the EloTokenizer as a pip-installable package or
     documented script in the repo

GL7  Produce a clear, citation-ready memo on what "55% token reduction"
     actually means — distinguishing internal stream tokens from
     LLM-consumed tokens — to prevent future confusion
```

---

## Non-Goals

```
N1  Variable-length / bit-packed ID encoding (deferred to v0.5;
     evaluated in v0.3-analysis.md, belongs in the wire-format layer,
     not the codec or LLM track)

N2  Arithmetic coding / entropy coding at the token layer (same
     rationale as N1 — wire-format work, not this milestone)

N3  C/C++ port of the codec or tokenizer (post-v0.4 work; this is
     Python-only specification and reference implementation)

N4  Training models larger than 3B (8B/14B are aspirational; the v0.4
     LLM track targets 3B as the minimum viable proof)

N5  General-purpose JSON compression (the structure-aware codec targets
     transcript JSON specifically; generic JSON is a different problem)

N6  Per-channel / per-corpus vocabulary adaptation (v0.3 dictionary
     is frozen)

N7  EPA projection, process-stage encoding, or any System 2 work
     (deferred to System 2 milestone)

N8  Publishing a model on HuggingFace Hub (nice-to-have, not required
     for v0.4 acceptance)
```

---

## Design Decisions

### D1 — Structure-aware codec operates as a pre-processing layer

The codec transforms transcript JSON into an intermediate byte stream
before the v0.3 compressor sees it. The compressor and dictionary are
**unchanged**. This keeps the codec orthogonal to the compression layer
and allows independent testing of each component.

```
encode:  raw JSON bytes → [structure codec] → intermediate bytes
         → [v0.3 compressor] → .elo file

decode:  .elo file → [v0.3 decompressor] → intermediate bytes
         → [structure decoder] → raw JSON bytes
```

### D2 — Codec exploits four categories of JSON redundancy

**Field-key deduplication.** Transcript JSON records repeat the same
keys thousands of times: `"text"`, `"start"`, `"duration"`, `"speaker"`,
`"language"`, `"video_id"`, `"segments"`. The codec maintains a
per-document key table and replaces repeated keys with short integer
references.

**Segment record templates.** Each segment in a transcript JSON has a
fixed shape: `{"text": ..., "start": ..., "duration": ...}`. The codec
encodes the template once and emits per-segment data as positional
fields, skipping the key envelope entirely.

**Timestamp delta encoding.** `"start"` and `"duration"` fields form
monotonically-increasing sequences. Storing deltas (difference from
previous value) instead of absolute values reduces value entropy
significantly.

**Speaker dictionary.** Speaker labels (`"speaker": "JOCKO"`) repeat
in a small closed set per transcript. The codec assigns single-byte
speaker IDs per document.

### D3 — The codec is format-specific, not general-purpose

The structure-aware codec relies on knowing the JSON schema of a
transcript. It is not a general JSON compressor. For v0.4, the schema
is the EloAI transcript format (segments array with text/start/duration
fields). Future schemas would require schema-specific codec variants.

The codec must fail gracefully on non-conforming JSON: detect the
schema mismatch and fall back to pass-through (no structure compression
applied, but still valid v0.3 encoding). No silent corruption.

### D4 — LLM benchmark protocol uses three baselines

To produce defensible numbers, every token-count measurement compares
against:

1. **Qwen2.5 tokenizer** (BPE, the model's native tokenizer) — primary
   baseline
2. **GPT-2 tokenizer** (BPE, widely cited) — secondary baseline
3. **SentencePiece Unigram** (used by LLaMA 3) — tertiary baseline

All measurements use the same 500-document held-out set (the v0.4 LLM
benchmark corpus), separate from the v0.3 3-transcript compression
test set.

The metric reported is: `(baseline_tokens - elo_tokens) / baseline_tokens`
expressed as a percentage reduction. Positive = EloAI uses fewer tokens.

### D5 — EloTokenizer implements the HuggingFace PreTrainedTokenizer interface

For reproducibility and ecosystem compatibility, the tokenizer must
implement `encode()`, `decode()`, `vocab_size`, and `get_vocab()` as
per the HuggingFace `PreTrainedTokenizer` contract.

The tokenizer reads the v0.3 frozen dictionary (`token-ids-v1.csv.gz`)
and a profile selection (`tiny`, `compact`, `standard`, `full`,
`reference`). It uses `caps_codec` logic for case handling and the
byte-fallback table for out-of-profile IDs.

### D6 — LLM retraining uses vocabulary replacement, not from-scratch pretraining

Training a 3B model from scratch is infeasible at this scale. The
approach instead:

1. Take a pre-trained Qwen2.5-3B checkpoint
2. Replace the embedding matrix (input embeddings + output head) with
   EloAI Compact-profile embeddings
3. Initialize EloAI embeddings for IDs 0-25 (essential Tier 0 words)
   from the corresponding Qwen token embeddings where possible, random
   init otherwise
4. Fine-tune on the EloAI transcript corpus with the new vocabulary
5. Evaluate downstream perplexity against the same model with its
   original BPE tokenizer on identical held-out text

This is the standard "vocabulary adaptation" technique from the
literature. The claim being tested is: can a model trained on
semantically-dense phrase-atom tokens achieve comparable or better
quality while using fewer tokens?

### D7 — The "55% confusion" is resolved by document, not by code

Multiple places in the v0.3 docs reference "55% token reduction"
without clearly stating whether this is internal stream-token count
or LLM-consumed token count. The two numbers differ by ~35 percentage
points (~55% stream reduction vs ~20% LLM token reduction).

v0.4 resolves this permanently:

1. `SYSTEM1.md` and `benchmark-v0.3.md` get a clarifying addendum
2. This spec defines the two metrics unambiguously:
   - **Stream-token reduction**: tokens in the v0.3 internal stream
     format vs v0.2. Measures encoding density improvement.
   - **LLM-token reduction**: EloAI tokens produced by the tokenizer
     vs BPE tokens for the same text. Measures context-window
     expansion.
3. All v0.4 output documents cite the correct number for the context

### D8 — The two tracks are independently shippable

Each track has its own acceptance criteria. The compression track can
ship as `v0.4-compression` without waiting for LLM results. The LLM
track can ship as `v0.4-llm` independently. The v0.4.0 tag, if used,
would require both tracks to pass their criteria — but partial tags
(v0.4.0-compression, v0.4.0-llm) are also valid milestones.

### D9 — Metrics glossary (canonical, to be referenced by all docs)

| Metric | Definition | v0.3 measured value |
|---|---|---|
| **Compression ratio** | raw_bytes / encoded_bytes | 1.99× (3-transcript avg) |
| **Stream-token reduction** | 1 − (v0.3_stream_tokens / v0.2_stream_tokens) | 55% |
| **LLM-token reduction vs BPE** | 1 − (Elo_tokens / BPE_tokens) on identical text | ~20% (informal pre-v0.4 measurement) |
| **Wire-format bandwidth reduction** | 1 − (ELO_wire_bytes / BPE_wire_bytes) for fixed-width encoding | ~47% (informal) |
| **Encode throughput** | raw_MB / encode_seconds | 1.4 MB/s |
| **Decode throughput** | raw_MB / decode_seconds | 6.5 MB/s |
| **Context-window multiplier** | 1 / (1 − LLM_token_reduction) — how much more text fits in the same token budget | ~1.25× (at 20% reduction) |

---

## Build Order — Compression Track

```
Step C1  Transcript JSON schema analysis
         Parse the 3 held-out transcripts. Catalogue:
           - exact JSON key set and per-key occurrence counts
           - segment record shape (fields present in every record)
           - timestamp field value distribution (integer range, delta stats)
           - speaker label set and per-speaker segment counts
           - any structural anomalies (missing fields, nested objects)
         Output: docs/compression/v0.4-schema-analysis.md
         Verify: key occurrence counts match manual spot-checks.

Step C2  Structure-aware codec design doc
         Based on schema analysis, specify:
           - key-table format and encoding
           - segment template byte layout
           - timestamp delta encoding scheme (signed varint or fixed)
           - speaker dictionary encoding
           - magic byte / version marker for the intermediate format
           - fallback behavior on schema mismatch
         Output: docs/compression/v0.4-codec-design.md
         Verify: design review against the 3 held-out transcripts
                 confirms every redundancy category is addressed.

Step C3  Codec implementation (encode + decode)
         Implement structure_codec.py with:
           - encode(json_bytes) → intermediate_bytes
           - decode(intermediate_bytes) → json_bytes
           - detect_schema(json_bytes) → schema_id or None
         Wire into compressor pipeline:
           - compressor.encode_bytes(codec.encode(json_bytes))
           - codec.decode(compressor.decode_bytes(elo_bytes))
         Output: structure_codec.py
         Verify: 3/3 transcripts round-trip through codec alone
                 (no compressor): codec.decode(codec.encode(json)) == json
         Verify: 3/3 transcripts round-trip through full pipeline:
                 decode(encode(json_bytes)) == json_bytes

Step C4  Compression benchmark
         Run the full pipeline (codec → v0.3 compressor → .elo) on the
         3 held-out transcripts. Compare against:
           - v0.3 baseline (no codec): 1.99×
           - v0.2 baseline (no phrases, no codec): 1.84×
           - gzip -9 on the raw JSON
         Measure encode + decode throughput.
         Output: docs/compression/benchmark-v0.4-compression.md
         Verify: ratio ≥ 2.5× on transcript average, or document
                 what was achieved and why.

Step C5  Compression milestone
         If GC3 (≥ 2.5×) is met: tag v0.4.0-compression.
         If close but not met: document the shortfall in the benchmark
         report, propose v0.5 directions, tag v0.4.0-compression as a
         partial milestone if GC1 + GC2 + GC4 + GC5 are met.
```

---

## Build Order — LLM Track

```
Step L1  Metrics disambiguation document
         Write a short reference doc that defines every metric
         unambiguously, with worked examples. This becomes the source
         of truth cited by all other documents.
         Output: docs/v1/metrics-glossary.md
         Verify: all existing docs that mention "55%" are listed with
                 their correct interpretation.

Step L2  EloTokenizer implementation
         Build elo_tokenizer.py implementing the HuggingFace
         PreTrainedTokenizer interface:
           - Reads token-ids-v1.csv.gz
           - Profile selection (tiny/compact/standard/full/reference)
           - Case handling via caps_codec logic
           - Byte-fallback for out-of-profile IDs
           - Special token injection (PAD/BOS/EOS/SEP/MASK/...)
         Output: elo_tokenizer.py + tests/test_elo_tokenizer.py
         Verify: encode("The quick brown fox.") round-trips through
                 encode → decode with correct case restoration.
         Verify: token counts on 500-doc benchmark set are reproducible.

Step L3  LLM benchmark protocol execution
         Run EloTokenizer (Compact profile) vs Qwen2.5 BPE tokenizer vs
         GPT-2 tokenizer on the 500-document LLM benchmark corpus.
         Measure:
           a) Token-count reduction per tokenizer pair
           b) Per-channel breakdown (jocko, julian, times_now, etc.)
           c) Wire-format byte comparison (fixed-width 2B, varint)
         Output: docs/compression/benchmark-v0.4-llm-tokens.md
         Verify: numbers are reproducible (seed the tokenizer, log the
                 corpus file list).

Step L4  3B model retraining
         Adapt Qwen2.5-3B to EloAI Compact vocabulary:
           - Build EloAI embedding init from Qwen embeddings
             (IDs 0-25 from matching Qwen token embeddings, rest random)
           - Fine-tune on the EloAI transcript corpus
           - Track training loss, eval perplexity
         Output: llm-training/ directory with scripts + configs
         Verify: training converges (loss decreases, no NaN).
         Verify: model can generate coherent text with EloAI tokenizer.

Step L5  Model evaluation
         Evaluate the adapted model vs baseline Qwen2.5-3B on:
           - Perplexity on held-out transcripts
           - Generation quality (human eval or automated metrics)
           - Memory footprint at int4 quantization
         Output: docs/compression/benchmark-v0.4-llm-model.md
         Verify: EloAI model achieves comparable perplexity to baseline
                 while using ~20% fewer tokens for the same text.

Step L6  On-device memory validation
         Compute exact memory budgets for 3B Compact at int4:
           - Embedding matrix: vocab_size × hidden_dim × 1B
           - Transformer weights: param_count × 0.5B (int4)
           - KV cache: 2 × n_layers × n_kv_heads × head_dim × ctx_len × 0.5B
           - Total: sum of above + overhead
         Verify ≤ 2 GB total for 3B Compact at 4k context.
         Output: add a section to docs/v1/profiles.md or a new
                 docs/v1/on-device-validation.md.

Step L7  LLM milestone
         If GL4-GL5 are met: tag v0.4.0-llm.
         Publish EloTokenizer as a documented script in the repo.
         If training does not converge or perplexity is significantly
         worse than baseline: document the findings honestly (negative
         results are still results), tag v0.4.0-llm as an exploratory
         milestone.
```

---

## Acceptance Criteria

### Compression Track

```
C-A1   3/3 held-out transcripts round-trip byte-exact through
       codec → compressor → decompressor → codec (lossless gate)

C-A2   Average compression ratio on 3 transcripts ≥ 2.5×
       (stretch target; ≥ 2.2× is a partial pass, documented)

C-A3   Encode throughput (codec + compressor combined) ≥ 1.0 MB/s

C-A4   Decode throughput (decompressor + codec combined) ≥ 3.0 MB/s

C-A5   Schema mismatch on non-transcript JSON triggers graceful
       fallback (pass-through), not silent corruption

C-A6   structure_codec.py is self-contained: zero changes to
       compressor.py or dictionary_builder_v03.py

C-A7   Codec design documented in docs/compression/v0.4-codec-design.md
```

### LLM Track

```
L-A1   metrics-glossary.md published and cited by SYSTEM1.md update

L-A2   EloTokenizer passes HuggingFace PreTrainedTokenizer interface
       contract (encode/decode/vocab_size/get_vocab)

L-A3   Token-count benchmark on 500-doc set is reproducible
       (same corpus, same profile, same tokenizer → same numbers)

L-A4   3B model fine-tuning converges (training loss decreases,
       no NaN) with EloAI Compact vocabulary

L-A5   EloAI-adapted model achieves eval perplexity within 5% of
       baseline Qwen2.5-3B on held-out transcripts

L-A6   Memory budget calculation confirms 3B Compact ≤ 2 GB at int4

L-A7   SYSTEM1.md is updated to disambiguate stream-token reduction
       from LLM-token reduction, citing metrics-glossary.md

L-A8   All documents that reference "55%" include a footnote or
       parenthetical clarifying which metric is meant
```

### Shared

```
S-A1   v0.3 dictionary is NOT modified (frozen contract)

S-A2   v0.3 backwards compatibility is preserved: existing v0.3 .elo
       files remain decodable by the v0.3 reader

S-A3   v0.2 backwards compatibility is preserved: existing v0.2 .elo
       files remain decodable by the v0.3+ reader
```

---

## Risks + Mitigations

```
RC1   Structure-aware codec overfits to the 3 held-out transcripts.
      Mitigation: schema analysis includes a fourth transcript (not
      held-out; from the corpus) to validate that the codec's field
      catalog and template shapes generalize. If the schema varies
      significantly across transcripts, the codec must handle all
      observed variants.

RC2   Timestamp delta encoding breaks on input with non-monotonic
      timestamps (editing artifacts, corrupted data).
      Mitigation: codec validates monotonicity at encode time; if
      violated, falls back to absolute timestamps for that segment
      run. Decode must detect the encoding mode per run.

RC3   Codec + compressor combined throughput drops below 1.0 MB/s.
      Mitigation: the codec operates on structured JSON, not raw bytes,
      so parsing is the bottleneck. Use `orjson` for fast JSON parsing
      (3-5× faster than stdlib json). If still too slow, profile and
      document the bottleneck.

RC4   EloTokenizer produces different token counts than the v0.3
      compressor's tokenizer (the character-class tokenizer and the
      LLM tokenizer are different code paths).
      Mitigation: EloTokenizer MUST use the same underlying tokenizer.py
      character-class logic, wrapped for HuggingFace compatibility.
      Token counts from both paths must match for identical input.

RC5   3B model training produces worse perplexity than baseline,
      undermining the "semantically dense tokens" claim.
      Mitigation: negative results are publishable. The hypothesis
      may need architectural changes (e.g., larger embedding dim,
      different initialization strategy). Document honestly.

RC6   The "55% confusion" persists because downstream readers cite
      the old documents without reading the disambiguation memo.
      Mitigation: the disambiguation goes in SYSTEM1.md (the most-read
      status doc) AND in metrics-glossary.md. Both are cited from
      this spec. Old benchmark docs get a retroactive addendum note
      at the top.

RC7   LLM track scope-creeps into full model training pipeline
      engineering (data loading, distributed training, evaluation
      harness).
      Mitigation: the deliverable is a set of scripts in llm-training/,
      not a production training framework. Single-GPU fine-tuning is
      sufficient for the 3B proof. If the scripts are useful, they can
      be productionized later.

RC8   The two tracks diverge in timeline and one blocks the other
      from tagging.
      Mitigation: independent tags (v0.4.0-compression, v0.4.0-llm).
      No joint gate.
```

---

## Metric Clarification: The "55%" Number

This section is included directly in the spec so it cannot be missed.

**v0.3 stream-token reduction: 55%** — The number of tokens in the
internal v0.3 token stream is 55% smaller than in the v0.2 token stream
(on the times_now transcript: 988k → 449k). This measures encoding
density within the `.elo` format. It is NOT a measure of LLM token
reduction vs BPE.

**LLM token reduction vs BPE: ~20%** — When the EloAI Compact-profile
tokenizer tokenizes English text, it produces about 20% fewer tokens
than the Qwen2.5 BPE tokenizer on the same text. This is the number
that matters for context-window expansion and KV-cache savings.

**Do not confuse these.** The 55% number is useful for understanding
why v0.3 decode is faster (fewer stream tokens to walk). The ~20%
number is what matters for LLM training claims. Every document in this
repository that references a percentage token reduction must specify
which metric it is using.

---

## Resolved Questions

```
Q1   Should the two tracks share a single tag?
     RESOLVED: No. Independent tags. Either track can ship without
     the other. A unified v0.4.0 tag would require both to pass,
     but v0.4.0-compression and v0.4.0-llm are the intended tags.

Q2   Does the LLM track require training all three model sizes?
     RESOLVED: No. 3B is the minimum viable proof. 8B and 14B are
     documented as future work but not required for v0.4 acceptance.

Q3   Should the structure-aware codec handle generic JSON?
     RESOLVED: No. Transcript JSON only for v0.4. The schema catalog
     can expand in future versions. See D3.

Q4   Does the compression track require variable-length encoding?
     RESOLVED: No. Variable-length / bit-packed IDs are deferred to
     v0.5. The v0.4 codec works within the existing fixed-tier format
     and achieves its gains by reducing the input byte volume before
     compression, not by changing the wire format.

Q5   Where does the 500-document LLM benchmark corpus come from?
     RESOLVED: The same EloAI transcript corpus used for dictionary
     mining, excluding the 3 held-out compression test files AND
     excluding the training split used for LLM fine-tuning. Exact
     file list to be published in the benchmark doc.

Q6   Should the EloTokenizer be published as a pip package?
     RESOLVED: Not for v0.4. A documented, importable script in the
     repo is sufficient. Packaging is v1.0 work.
```

---

## Open Questions

```
O1   Which specific 500 documents form the LLM benchmark corpus?
     Needs: a deterministic file list, published before L3 begins.

O2   What is the exact training/eval split for the 3B fine-tuning run?
     Needs: defined before L4 begins. Should be documented in the
     training config.

O3   Does the structure-aware codec's intermediate format need to be
     documented as a public contract, or is it internal to the Python
     reference implementation?
     Needs: decision before C2. If the C/C++ port will implement the
     codec, the intermediate format is part of the file format contract.
     If C/C++ port happens after v1.0 with a different codec, it's
     internal.

O4   Should the benchmarks include comparison against zstd (not just gzip)?
     Needs: zstd is the modern baseline for general-purpose compression.
     Including it would make the benchmark more credible to external
     reviewers.

O5   What specific evaluation benchmark suite should the 3B model run?
     Options: MMLU, HellaSwag, GSM8K, HumanEval, or a transcript-
     specific dialogue evaluation. The choice affects training compute
     budget and what claims can be made.
```

---

## References

```
v0.3 spec              docs/compression/spec-v0.3.md
v0.3 benchmark         docs/compression/benchmark-v0.3.md
v0.3 analysis          docs/compression/v0.3-analysis.md
Profile system         docs/v1/profiles.md
SYSTEM1 status         SYSTEM1.md
ELO file format        elo_file_format/ELO_FILE_FORMAT.md
Repository             github.com/4waymedia/semantic-compression
EloAI                  https://eloai.dev
```

---

## Version History

| Version | Date | Author | Changes |
|---|---|---|---|
| v0.4-draft-1 | 2026-06-11 | Cline | Initial draft from v0.3 post-mortem context |