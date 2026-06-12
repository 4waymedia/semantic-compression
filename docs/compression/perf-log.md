# Compression Performance Log

> Append-only record of measured throughput optimizations on the
> `.elo` / `.eloB` encode and decode paths. Each entry documents the
> change made, the path taken to identify and apply it, the measured
> before/after numbers, and the verification of byte-exact correctness.
>
> Purpose: when revisiting the codec in 6 months, know which knobs have
> been turned, what was tried, and what the current ceiling is — without
> re-deriving everything from git history.

---

## Format of entries

Each entry has the same shape:

1. **Date / branch / spec** — when, where, and which sponsoring spec
2. **Baseline** — the numbers we started from
3. **Hypothesis** — what we thought was wrong / could be improved
4. **Path taken** — the diagnostic + design decisions, briefly
5. **Implementation** — what landed (one paragraph + key files)
6. **Result** — measured after-numbers + speedup factor
7. **Verification** — which test suites confirmed no behavioral regression
8. **Lessons / what's still on the table** — what's next, what we explicitly chose not to do

Entries append to the bottom. Old entries are not edited.

---

## 2026-06-12 — Decoder hot-path cache + bytearray accumulator

| Field | Value |
|---|---|
| **Branch** | `decode-spec-v01` |
| **Spec** | "Optimize decoder hot path without changing the ELO binary format or public API" (Jipity) |
| **Owner** | Paul G + Jipity (spec) + Claude Opus 4.7 (impl) |
| **Files touched** | `compressor.py` (+214/−39), `test_decoder_cache.py` (new), `bench_decoder.py` (new), `bench_decode_results.json` (new) |

### Baseline

| Metric | Value |
|---|---:|
| `decode_MBps` (transcripts) | ~7.0 MB/s |
| `encode_MBps` (transcripts) | ~1.4 MB/s |
| `compression_ratio` (transcripts) | ~2.0× |

Measured via `test_binary_stream.py` on `times_now`, `jocko`, `julian`
transcripts. Decode throughput was LMDB-GET-bound — every token required
a round-trip into the embedded key-value store, even though the same
~374k entries were hit over and over across documents.

### Hypothesis

Profiling pointed at three bottlenecks:

1. **LMDB GET per token.** The dictionary is ~5 MB total — fits easily
   in RAM. Loading it once into a Python dict trades a one-time 300–500ms
   startup cost for ~50 ns lookups vs ~200–500 ns LMDB GETs.
2. **str round-trip per token.** `_read_id_from_binary` built a Python
   `str` for the LMDB key, which then had to be `.encode()`'d to bytes.
   The byte form is reachable directly from `BASE64_CHARS_BYTES`.
3. **`out_parts: list[str]` → `''.join` → `.encode()`.** Two passes over
   the full decoded text. The LMDB values are already UTF-8 bytes — we
   can accumulate bytes and `b''.join` in one pass.

### Path taken

- **Started with profiling notes**, not blind optimization. Confirmed the
  3-way split (~50% LMDB, ~30% dispatch, ~20% accumulation).
- **Picked Tier 1 changes** (cache + bytes-returning helpers + bytearray
  accumulator). Held off on Cython, Rust, SIMD — the cheap wins should
  prove out the substrate before paying the language-boundary cost.
- **Made the cache fully optional.** The constructor exposes
  `preload_rev_cache` and `preload_fwd_cache`, both default `True`. Pass
  `False` for memory-constrained environments; correctness is unchanged.
- **Symmetric.** Forward cache exists too, even though encode wasn't the
  primary target — keeps the codebase consistent and gets a modest
  speedup there for free.
- **Wrote equivalence tests first** (7 tests covering empty input, all
  tier ranges, cap-prefix, OOV, encoder symmetry, telemetry, lifecycle).
  Confirmed cached and uncached paths produce byte-identical output
  before pushing the bench.

### Implementation

`Compressor.__init__` gains `preload_rev_cache` / `preload_fwd_cache`
kwargs. `open()` calls `_build_caches()` which walks the LMDB cursor
once per enabled cache and stores `dict[bytes, bytes]`. Hot-path lookups
in `_consume_stream_binary`, `_emit_token_binary`, `_decode_token`,
`_encode_token` check the cache first and fall back to `txn.get` when
absent.

`_read_id_from_binary` now returns `bytes` directly (sliced/constructed
from `BASE64_CHARS_BYTES`). `_consume_stream_binary` accumulates into
`list[bytes]`, `b''.join`-ed at the end. Implicit-space restoration moved
to `_restore_implicit_spaces_bytes`, which uses `_classify_bytes_token`
to inspect token classes without per-token UTF-8 decode (ASCII fast path
covers ~99% of English content).

Telemetry exposed as `Compressor.init_ms`, `Compressor.rev_cache_bytes`,
`Compressor.fwd_cache_bytes`.

### Result

Bench across 10 v1 samples + 3 transcripts (4.39 MB source):

| Metric | UNCACHED | CACHED | Δ |
|---|---:|---:|---:|
| `decode_MBps` | **6.57** | **10.32** | **+57.1%  (1.57×)** |
| `encode_MBps` | 1.50 | 1.71 | +14.0%  (1.14×) |
| `compression_ratio` | 1.993 | 1.993 | unchanged |
| `round_trip` | PASS | PASS | preserved |
| `init_ms` | 0 | 453 | one-time at open() |
| `rev_cache_bytes` | 0 | 5,280,227 | ~5.3 MB resident |
| `fwd_cache_bytes` | 0 | 5,280,227 | ~5.3 MB resident |

Largest transcript decode (jocko, 1.08 MB): **7.40 → 10.68 MB/s, 1.44×**.

Full per-payload metrics in `bench_decode_results.json` (sibling of this
file in the repo).

### Verification

- `verify_lossless.py` — **10/10 PASS** byte-exact round-trip on v1 format samples
- `test_binary_stream.py` — **10/10 + 3/3 PASS** binary round-trip on v1 + transcripts
- `test_decoder_cache.py` — **7/7 PASS** new cached/uncached equivalence tests

The format is unchanged: `ELO_BIN_VERSION = 2` and all token tag values
constant. A binary file produced by this branch is decoded identically by
an unmodified `main` reader.

### Lessons / what's still on the table

**What worked**

- Caches alone get most of the gain. The bytes-returning `_read_id_from_binary`
  and `bytearray` accumulation add another ~5–8 percentage points on top.
- Cap-prefix and OOV slow paths can stay as-is (str round-trip) because
  they're rare; optimizing them would add complexity for marginal gain.

**What we deliberately didn't do**

- **Cython/Rust extension.** Next tier of speedup, but ties us to a build
  step and a per-platform wheel matrix. Hold for when 10 MB/s isn't enough.
- **Class-cache for inline implicit-space restoration.** Considered, but
  the bytes-aware `_classify_bytes_token` first-byte ASCII fast path is
  already cheap enough that an explicit class cache adds memory without
  measurable speedup.
- **Mmap'd flat reverse dictionary.** Pointer math wins another ~20–30% per
  lookup but doubles the storage footprint (LMDB + mmap blob). Defer until
  the Memory module's working set is the bottleneck.
- **SIMD bulk tag dispatch.** Real Tier 3 work, irrelevant until we're on
  Rust anyway.

**Likely next milestone**

- When this lands on `main`, re-baseline the Memory module's design
  assumptions in `Memory/Memory.md` (read-path latency targets) against
  the new ~10 MB/s decode floor. Some sub-100 µs targets there were
  conservative against the 7 MB/s number; we now have meaningful margin.
