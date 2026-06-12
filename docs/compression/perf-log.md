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

---

## 2026-06-12 — Int-keyed reverse cache (decode-spec-v02)

| Field | Value |
|---|---|
| **Branch** | `decode-spec-v02` (from `decode-spec-v01`) |
| **Spec** | "Implement integer/list-backed reverse decode cache" (Jipity) |
| **Target** | 30 MB/s pure-Python decode |
| **Owner** | Paul G + Jipity (spec) + Claude Opus 4.7 (impl) |
| **Files touched** | `compressor.py` (+~120/−~10), `test_decoder_cache.py` (+4 tests), `bench_decoder.py` (3-way compare), `bench_decode_results.json` |

### Baseline

Coming in from decode-spec-v01:

| Config | decode_MBps |
|---|---:|
| UNCACHED  (LMDB GET per token) | 6.52 |
| BYTES-CACHE (`dict[bytes,bytes]`) | 9.04 |

### Hypothesis (per spec)

`_read_id_from_binary` still allocates a fresh `bytes` object per token to
build the LMDB-key shape. Even with the bytes cache, that allocation +
hash on a 1–4-byte bytes object costs ~200 ns per token. The stream byte
pattern can be packed directly into an `int` (the tag bits keep tier
ranges non-overlapping), giving an int-keyed cache. Hypothesis: ~3×
further speedup, pushing us toward 30 MB/s.

### Path taken

- **Direct stream→int mapping** without going through `bytes`:
  - Tier 0 (1 B): `int_key = tag`
  - Tier 1 (2 B): `int_key = (tag << 8) | stream[i+1]`
  - Tier 2 (3 B): `int_key = (tag << 16) | (b1 << 8) | b2`
  - Tier 3 (4 B): `int_key = (tag << 24) | (b1 << 16) | (b2 << 8) | b3`
  Tag bits keep the four tier ranges disjoint, so a single
  `dict[int, bytes]` works.
- **Built at init from the same rev-cursor walk** that builds the bytes
  cache (no second LMDB pass). `_build_int_keyed_cache(entries)` decodes
  each LMDB Base64 key string into the equivalent stream int.
- **New fast path** `_consume_stream_binary_int` exists alongside the
  bytes-keyed `_consume_stream_binary`. Selected by `decode_bytes_binary`
  based on which caches are available; falls back through:
  `int cache → bytes cache → LMDB GET`. Zero behavioral change in any
  path.
- **Micro-tuning**: hoisted `id_to_surface.__getitem__` and
  `out_parts.append` to local-variable aliases inside the hot loop —
  CPython resolves `LOAD_FAST` (~30 ns) faster than `LOAD_GLOBAL` or
  attribute lookups (~70 ns).

### Implementation

```python
def _build_int_keyed_cache(entries):
    bi = BASE64_INDEX
    out = {}
    for k, v in entries:
        n = len(k)
        if n == 1:
            int_key = bi[chr(k[0])]
        elif n == 2:
            int_key = ((0x40 | bi[chr(k[0])]) << 8) | bi[chr(k[1])]
        elif n == 3:
            int_key = ((0x80 | bi[chr(k[0])]) << 16) | (bi[chr(k[1])] << 8) | bi[chr(k[2])]
        elif n == 4:
            int_key = ((0xC0 | bi[chr(k[0])]) << 24) | (bi[chr(k[1])] << 16) | (bi[chr(k[2])] << 8) | bi[chr(k[3])]
        out[int_key] = v
    return out
```

### Result

3-sample average on the 3 transcripts (13.16 MB total source):

| Config | decode_MBps | vs UNCACHED | vs BYTES-CACHE |
|---|---:|---:|---:|
| UNCACHED | 6.52 | 1.00× | 0.72× |
| BYTES-CACHE (v01) | 9.04 | 1.39× | 1.00× |
| **INT-CACHE (v02)** | **10.14** | **1.55×** | **1.12×** |

Init cost: 609 ms (was 531 ms for v01) — building both caches in one
LMDB walk. Memory: rev (5.3 MB) + fwd (5.3 MB) + int (6.9 MB) = ~17 MB total.

### Verification

- `verify_lossless.py` — 10/10 PASS byte-exact
- `test_binary_stream.py` — 10/10 + 3/3 PASS byte-exact
- `test_decoder_cache.py` — **11/11 PASS** (added 4 v02-specific tests:
  `test_int_cache_matches_rev_cache_binary`, `test_int_cache_matches_lmdb_binary`,
  `test_int_cache_fallback`, `test_int_cache_telemetry`)

### Lessons / what's still on the table

**Honest assessment vs the 30 MB/s target**

We're at 10 MB/s. The 30 MB/s target appears to be **at or beyond the
pure-Python interpreter ceiling** for this workload:

- Per-token cost breakdown (~300–400 ns total):
  - Bytecode dispatch + frame management: ~150 ns
  - `dict.__getitem__`: ~80 ns
  - Int arithmetic + byte indexing: ~50 ns
  - `list.append` + loop tail: ~80 ns
- At 4-byte avg token length, ~300 ns/token caps throughput at ~13 MB/s.

The bytes-cache → int-cache step gave a real but modest gain (+12%)
because LMDB-key allocation was already not the dominant cost once the
hash was cached.

**What we deliberately didn't do this round**

- **Cython/PyO3 extension.** Would clear the interpreter overhead and
  reach 30+ MB/s easily, but introduces a build step. Held for the
  decision on whether to ship a wheel.
- **Per-tier flat lists for Tier 0/1/2.** Computed memory cost (~8 MB
  for Tier 2 alone, plus dict for Tier 3) for an expected ~10 ns
  per-lookup saving. Not worth the complexity until measured to dominate.
- **Bulk pre-pass with `struct.unpack`.** Variable-length tokens
  defeat fixed-stride unpacks; would require an index-building pass.

**Next likely milestone (if we keep pushing)**

- **decode-spec-v03 (Cython)** — `compressor.pyx` for the hot
  `_consume_stream_binary_int` function. Pure-Python ceiling is ~12
  MB/s; Cython realistic target is 50–100 MB/s without sacrificing the
  current bytes-format or test suite. Build cost: a wheel matrix.
- **OR pivot to "use what we have"** — 10 MB/s is more than enough for
  the Memory module's read-path targets (still sub-100 µs for typical
  memory items). Stop optimizing the decoder and focus on consumers.
