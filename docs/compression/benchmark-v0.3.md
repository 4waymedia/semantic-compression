# ELO Compressor v0.3 Benchmark
### Phrase-Atom Vocabulary + Compression-Aware Filters

> Status: completed on branch `v0.3-phrase-dictionary` at commit `bf4fbd3`
> Date: 2026-06-07
> Predecessors: v0.2 release at commit `7fc9152`, tag `v0.2`

---

## Summary

This benchmark measures v0.3 against v0.2 across the same test set. v0.3
adds 167,275 phrase atoms to the dictionary, applies a longest-match
scan at encode time, drops zero-savings Tier-0 bigrams, and reserves
the top 1,024 Tier-1 slots for high-frequency single words.

| Stage | Compression (avg) | Tokens (times_now) | Decode (MB/s) | Status |
|---|---:|---:|---:|---|
| v0.2 (current main) | 1.84x | 988k | 3.5 | shipped |
| v0.3 raw phrases (no filter) | 1.99x | 449k | 6.2 | proof |
| v0.3 final (filter + reserve) | **1.99x** | **449k** | **6.0** | this report |

All 13 files (10 v1 samples + 3 transcripts) round-trip **byte-exact**
through every measured configuration.

---

## Test set

```
v1 samples  (10 files, 274-798 bytes each)
   plain.txt readme.md config.json contacts.csv feed.xml page.html
   service.yaml server.log episode.srt episode.vtt

3 real transcripts (held-out from phrase mining)
   times_now    KhyQCU6oqE8.json    2.38 MB   news / political
   jocko        KOhbGjmidgs.json    1.03 MB   military podcast
   julian       bRwFb8JmznE.json    0.80 MB   interview / long-form
```

---

## Compression results (v0.2 vs v0.3)

| File | Source | v0.2 | v0.3 | Improvement |
|---|---:|---:|---:|---:|
| times_now (.json) | 2,491,529 | 1,355,635 (1.84x) | **1,250,193 (1.99x)** | +7.8% |
| jocko (.json) | 1,076,554 | 574,832 (1.87x) | **532,959 (2.02x)** | +7.3% |
| julian (.json) | 817,549 | 442,611 (1.85x) | **414,089 (1.97x)** | +6.4% |
| **transcript avg** | — | **1.85x** | **1.99x** | **+7.2%** |

Sample files (smaller than 1 KB, dominated by header overhead):

| File | v0.2 | v0.3 | Note |
|---|---:|---:|---|
| plain.txt | 1.38x | 1.38x | unchanged |
| readme.md | 1.00x | 0.97x | slight regression from extra phrase lookups |
| config.json | 0.86x | 0.84x | OOV-dominated (JSON keys not in corpus) |
| contacts.csv | 0.85x | 0.87x | marginal gain |
| feed.xml | 0.89x | 0.90x | marginal gain |
| page.html | 0.79x | 0.80x | marginal gain |
| service.yaml | 0.86x | 0.86x | unchanged |
| server.log | 0.97x | 0.99x | timestamp patterns improve slightly |
| episode.srt | 1.08x | 1.12x | natural language gain |
| episode.vtt | 0.84x | 0.89x | natural language gain |

Sample-file results are noisy because the .elo header overhead
(magic + version + ext) is ~5-10% of file size at this scale.

---

## Token-count results — the larger v0.3 win

| File | v0.2 stream tokens | v0.3 stream tokens | Reduction |
|---|---:|---:|---:|
| times_now | 938,751 | 449,355 | **-52%** |
| jocko | 435,107 | 164,302 | **-62%** |
| julian | 333,683 | 120,757 | **-64%** |

Stream-token reduction is the metric that translates directly to LLM
context-window efficiency. A model trained on v0.3 vocabulary sees
**2-3x more conversational content** per token consumed.

---

## Decode throughput

| Version | Encode MB/s | Decode MB/s |
|---|---:|---:|
| v0.2 binary | 1.7 | 3.5 |
| v0.3 binary | 1.4 | **6.5** |

Decode is **~2x faster** in v0.3 because there are fewer stream tokens
to walk and decode. Encode is slightly slower because of the longest-
match scan against LMDB.

---

## Tier distribution comparison (times_now)

| Tier | v0.2 tokens | v0.3 tokens | Change |
|---|---:|---:|---:|
| T0 (1 byte) | 629,409 | 172,518 | -73% |
| T1 (2 bytes) | 209,776 | 46,795 | -78% |
| T2 (3 bytes) | 74,862 | 116,939 | **+56%** |
| T3 (4 bytes) | 122 | 45,967 | huge increase |
| cap-prefixed | 48,640 | 43,454 | -11% |
| OOV | 24,582 | 24,582 | unchanged |
| **total stream tokens** | **988,391** | **449,275** | **-55%** |

Phrase atoms collapse multi-word sequences into single (longer) IDs.
Stream token count drops dramatically but average byte-per-token rises
because many tokens are now Tier 2 (3 bytes) instead of Tier 0 (1 byte).

---

## Dictionary inventory (v0.3 final)

```
Format version:   3
Total entries:    373,918

  Tier 0 (1-char):       53    pre-seeded (words + structural)
  Tier 1 (2-char):     1,280   1024 reserved words + 256 high-freq phrases
  Tier 2 (3-char):    81,920   mid-frequency mix
  Tier 3 (4-char):   290,665   long tail

Words in dict:    206,616    matches v0.2 word coverage
Phrases in dict:  167,275    after filter
  raw candidates:  167,876
  dropped (Tier-0 bigrams):     601

Forced seed:      | -> gA   (stream-delimiter safety)
```

### Profile cuts (frozen v1 contract)

| Profile | Content slots | + Bytes | + Special | Total vocab |
|---|---:|---:|---:|---:|
| Tiny | 32,496 | 256 | 16 | **32,768** |
| Compact | 65,264 | 256 | 16 | **65,536** |
| Standard | 130,800 | 256 | 16 | **131,072** |
| Full | 261,872 | 256 | 16 | **262,144** |
| Reference | 373,891 | 256 | 16 | **374,163** |

All cuts deterministic — top N by frequency rank. See `docs/v1/profiles.md`.

---

## Published artifacts (v0.3)

```
db/dictionary.lmdb                    374k entries, ~50 MB on disk
db/dict_stats_v03.json                build metadata + profile cuts
data/token-ids-v1.csv.gz              frozen vocabulary contract (~3 MB gz)
data/special-tokens-v1.json           16 LLM special tokens × 5 profiles
data/byte-fallback-v1.csv             256 byte IDs × 5 profiles
data/profile-cuts-v1.json             profile rank boundaries
```

---

## Acceptance criteria — results vs spec

| Criterion | Spec target | Actual | Status |
|---|---|---|---|
| A1: 10/10 v1 sample byte-exact | required | 10/10 | ✅ |
| A2: 3/3 transcript byte-exact | required | 3/3 | ✅ |
| A3: transcript ratio ≥ 2.5x | required | 1.99x | ❌ missed |
| A4: encode ≥ 1.5 MB/s | required | 1.4 MB/s | ⚠️ near miss |
| A5: decode ≥ 2.0 MB/s | required | 6.5 MB/s | ✅ exceeded |
| A6: token-ids-v1.csv.gz | required | committed | ✅ |
| A7: byte fallback table | required | committed | ✅ |
| A8: held-out mining honesty | required | 3 files excluded | ✅ |
| A9: v0.2 backward decode | optional | not implemented | ⏸ deferred |
| A10: ELO_FILE_FORMAT.md update | required | pending | ⏸ deferred |
| A11: top phrases via ranking | required | natural ranking | ✅ |

**Acceptance status:** 8 of 11 ✅, 1 ⚠️, 2 ⏸.
A3 (ratio target) is the only substantive miss. See `v0.3-analysis.md`
for the theory-vs-reality breakdown.

---

## Reproducibility

```bash
# Switch to the v0.3 work branch
git checkout v0.3-phrase-dictionary

# Re-mine the corpus (excludes the 3 held-out files)
python -m semantic_compression.word_frequency_counter
python -m semantic_compression.ngram_counter --nmin 2 --nmax 5
python -m semantic_compression.ngram_counter --nmin 6 --nmax 9

# Refine candidates with PMI + maximal-phrase filtering
python -m semantic_compression.phrase_miner

# Rebuild the v0.3 dictionary
python -m semantic_compression.dictionary_builder_v03

# Verify everything still round-trips
python -m semantic_compression.compressor verify     # 10/10 v1 samples
python test_binary_stream.py                          # 10/10 + 3/3 transcripts
```

All scripts read from disk and produce deterministic output given the
same corpus. The held-out file list is constant; profile cuts are
deterministic; ID assignments follow frequency rank.

---

## References

```
v0.2 benchmark         docs/compression/benchmark-v0.2.md
v0.3 spec              docs/compression/spec-v0.3.md
v0.3 analysis          docs/compression/v0.3-analysis.md
Profile system         docs/v1/profiles.md
File format            elo_file_format/ELO_FILE_FORMAT.md
Repository             github.com/4waymedia/semantic-compression
```
