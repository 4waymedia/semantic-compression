# ELO Compressor v0.2 Benchmark
### Predictive Binary Token Stream

> Status: merged to `main` at commit `7fc9152`
> Date: 2026-06-06
> Tags this artifact: experimental record + first compressing release

---

## Summary

This benchmark compares four branches of the lossless compressor on
identical inputs. Each branch isolates or stacks a specific optimization.
All branches were verified byte-exact lossless before measurement.

| Branch | Mechanism | Tests | Avg Ratio |
|---|---|---:|---:|
| `main` (pre-merge) | baseline lossless text format | 10/10 + 3/3 byte-exact | **0.91x** |
| `experiment/implicit-whitespace` | token-count reduction | 10/10 + 3/3 byte-exact | **1.29x** |
| `experiment/binary-stream` | byte-cost reduction | 10/10 + 3/3 byte-exact | **1.42x** |
| `experiment/combined` (now main) | both stacked | 10/10 + 3/3 byte-exact | **1.84x** |

*Ratio is `source_bytes / encoded_bytes`. Higher is better. Average is over
the 3-file transcript stress test described below.*

---

## Result

The combined branch compresses real transcript JSON by **44–47%** while
preserving **byte-exact** round-trip. This is the first release where the
EloAI compressor actually compresses rather than expanding the input.

### Real-world transcripts (3 files, 800 KB – 2.4 MB each)

| File | Source | main | implicit-ws | binary | **combined** |
|---|---:|---:|---:|---:|---:|
| `times_now` (news) | 2,491,529 B | 0.95x | 1.29x | 1.47x | **1.84x** (-45.6%) |
| `jocko_podcast` | 1,076,554 B | 0.90x | 1.31x | 1.41x | **1.87x** (-46.6%) |
| `julian_dorey` (interview) | 817,549 B | 0.88x | 1.27x | 1.38x | **1.81x** (-44.6%) |

### v1 sample suite (10 file formats)

All 10 v1 formats round-trip byte-exact in both text and binary modes:
`.txt .md .json .csv .xml .html .yaml .log .srt .vtt`.

Sample-file ratios are weaker than transcript ratios because of fixed
header overhead amortizing badly under ~1 KB. The acceptance criterion
for these files is correctness (lossless round-trip), not ratio.

### Throughput

| Mode | Encode | Decode |
|---|---|---|
| Text (`.elo`) | 1.8–2.2 MB/s | 2.8–3.5 MB/s |
| Binary (`.eloB`) | 1.7–2.1 MB/s | 2.9–3.9 MB/s |

LMDB lookups dominate. Wire-format conversion is negligible. A future C
port is expected to be 10–100× faster.

---

## Conclusion

The baseline expansion was **not** caused by the dictionary layer.
The dictionary already covers 100% of the corpus vocabulary and OOV
sits at 2.6% on transcript JSON.

The expansion was caused by two independent overheads:

1. **Serialization overhead** — every token paid for a 1-byte pipe
   delimiter regardless of its actual information density.

2. **Explicit whitespace storage** — the single-space token (47% of
   all tokens in the YouTube corpus) was being serialized in full,
   even though its position is fully predictable between two
   word-class tokens.

The combined v0.2 wire format addresses both:

1. **Implicit single-space prediction** removes the redundant
   whitespace token from the stream and reinserts it at decode time
   based on the classify(prev) == WORD && classify(next) == WORD rule.

2. **Tag-encoded binary stream** packs each in-vocab ID as 1–4 bytes
   with implicit token boundaries (top 2 bits of the first byte encode
   the tier), eliminating delimiters entirely.

Both transforms commute. Option 2 operates on the token list before
encoding; Option 1 operates on the wire format after encoding. Stacking
them multiplies the gains — observed in practice: 1.29x × 1.42x ≈ 1.83x,
matching the measured combined value of 1.84x.

---

## Current Winner

**`experiment/combined`** — merged to `main` at commit `7fc9152`.

The proof branches remain on the remote as a defensible experimental
record:

```
experiment/implicit-whitespace   <- token-count reduction in isolation
experiment/binary-stream         <- byte-cost reduction in isolation
experiment/combined              <- both, equivalent to current main
```

---

## Next Target — v0.3 Structure-Aware Compression

Real-world transcript JSON has heavy structural redundancy that the
current general-purpose tokenizer cannot exploit:

1. **Repeated JSON key atoms** — `"chunk_id"`, `"start_hms"`, `"speaker"`,
   `"end"`, `"text"` appear thousands of times per file. Each currently
   tokenizes as `"` + `chunk_id` + `"` + `:`. A composite atom for the
   key-with-quotes-and-colon would collapse 4 tokens into 1.

2. **Segment record templates** — the chunk schema is fixed. Templating
   would store the schema once and emit only the value sequence per chunk.

3. **Timestamp / delta encoding** — `start_hms` values are monotonically
   increasing within a file. Delta encoding replaces full timestamps
   with deltas; numbers compress much better in that form.

4. **Phrase atoms** — corpus n-gram mining for high-frequency multi-word
   sequences like `"i mean"`, `"you know"`, `"at the end of the day"`.
   Each phrase becomes a single dictionary ID.

5. **Speaker / name dictionaries** — a small per-channel auxiliary
   dictionary covers proper nouns, channel branding, recurring guests.
   Closes most of the remaining OOV gap.

Expected v0.3 target: **2.5x – 3.5x** on transcript JSON.

---

## Reproducibility

```
# Switch to the merged main
git checkout main

# Verify text mode (10 sample files)
python -m semantic_compression.compressor verify

# Verify binary mode (10 samples + 3 transcripts)
python test_binary_stream.py

# Run the v0.1 inline foundation proof (no compressor.py)
python -m semantic_compression.verify_lossless
```

All three should report 100% pass byte-exact.

---

## References

- Repository: `github.com/4waymedia/semantic-compression`
- v0.1 baseline proof: commit `108dc8e`
- v0.2 merged compressor: commit `7fc9152`
- System spec: `SYSTEM1.md`
- Architecture: `../CLAUDE.md`
