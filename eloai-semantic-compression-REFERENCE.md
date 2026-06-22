# EloAI Semantic Compression — Portable Reference
> Drop-in context for another Claude Code project that needs to call into,
> consume from, or interoperate with the System 1 compression pipeline.
> Last sync: v0.4.0 — Semantic Facets Layer (STAGED, 2026-06-19);
> dictionary frozen at v0.3.0. Repository:
> github.com/4waymedia/semantic-compression

---

## What this project IS

A lossless, dictionary-based token compression pipeline for arbitrary
UTF-8 text. Maps every surface form (words, phrases, whitespace,
punctuation) to a Base64 ID via LMDB. Designed as **System 1** of a
larger semantic-processing stack — Systems 2-4 (EPA projection,
relational processing, knowledge base) are not built yet.

Two wire formats:
- `.elo`  — text stream, pipe-delimited tokens
- `.eloB` — binary, tier-tagged variable-length (1-4 bytes per ID)

As of **v0.4.0 (staged)** the dictionary also carries a **static semantic facets
layer** — a 4-byte record per entry (`semantic_bucket` + composable
`logic_cue_mask` + `flags`) in two new sub-DBs (`facets`, `meta`). Deterministic,
no inference, never emitted into the stream. See the facets contract below.

**Status:** v0.3.0 dictionary, production. 13/13 test files byte-exact round-trip.
Avg ratio 1.99x on transcripts, 55% stream-token reduction vs v0.2.
v0.4.0 facets layer STAGED (not frozen) — S1 IDs still provisional across
dictionary test groups; facets re-derived per build.

---

## What this project IS NOT

- Not an LLM. No model weights. No inference.
- Not lossy. `decode(encode(bytes)) == bytes` is non-negotiable.
- Not a tokenizer for an existing LLM. It is its own vocabulary.
- Not semantic-aware yet. EPA / process stages are reserved Tier-0 slots
  waiting for System 2.

---

## Public contract (what you can consume from another project)

### 1. Compressed file formats

```
.elo   text wire format    UTF-8, pipe-delimited token stream
.eloB  binary wire format  tier-tagged var-length, no delimiters
```

Header on both: 4-byte little-endian `FORMAT_VERSION` (= 3 at v0.3.0).

### 2. Frozen dictionary artifacts (v0.3 vocabulary contract)

```
token-ids-v1.csv.gz       ID -> surface form mapping
special-tokens-v1.json    system + reserved primitives
byte-fallback-v1.csv      OOV byte-level fallback
profile-cuts-v1.json      5 standard LLM vocab subsets
```

Profile cuts (sized for known model families):

| Profile     | Content tokens | Total slots |
|-------------|---------------:|------------:|
| Tiny        |         32,496 |      32,768 |
| Compact     |         65,264 |      65,536 |
| Standard    |        130,800 |     131,072 |
| Full        |        261,872 |     262,144 |
| Reference   |        373,891 |     374,163 |

### 3. Codec API surface (Python, mirrors planned C API)

```python
# Stateless. Library handle is the only stateful object.
sc_open_library(lmdb_path) -> Library
sc_close_library(lib)
sc_encode(text, lib) -> (stream_bytes, error_code)
sc_decode(stream_bytes, lib) -> (text, error_code)
```

LMDB store has FOUR named DBs in one env (`max_dbs=4`): `forward`
(surface -> ID), `reverse` (ID -> surface), `facets` (ID -> 4-byte record), and
`meta` (versions + identity + fingerprint). ~100ns lookup. C-readable directly.

### 4. Facets layer (v0.4.0, staged) — opt-in semantic annotation

```python
from semantic_compression.facet_reader import get_facet, get_meta, verify_fingerprint
facet = get_facet(env, id_bytes)          # -> (bucket, cue_mask, flags) | None
from semantic_compression.facet_reader import describe_facet
describe_facet(facet)                      # -> {bucket, cues[], flags[], utility}
```

Record (`struct '<BHB'`, 4 bytes): byte0 `semantic_bucket`
(UNKNOWN/TOPIC/METHOD/CONCEPT/RELATION/STRUCTURAL); bytes1-2 `logic_cue_mask`
(uint16 LE, composable: CAUSE/CONTRAST/INFERENCE/CONDITION/…); byte3 `flags`
(MULTIWORD/CLOSED_CLASS/MANUAL/HEURISTIC/AMBIGUOUS + 2-bit UTILITY).

Built deterministically by `facet_builder.py` (no model). `meta` self-describes
`dictionary_release` / `dictionary_status` (`staged`|`frozen`) + a content
fingerprint. **Facets are re-derived per dictionary build**; the fingerprint is a
contract only when `dictionary_status == frozen`. Standard:
`Memory/docs/SEMANTIC_FACETS_SPEC.md`; S1 detail: `docs/compression/spec-facets-db.md`.

---

## Stream format (text wire `.elo`)

```
lowercase in-vocab     <ID>                "T"        -> "the"
cased in-vocab         <cap>:<ID>          "g:T"      -> "The"
phrase atom            <ID>                "wB"       -> "you know"
lowercase OOV          OOV:A:<word>        "OOV:A:foo"-> "foo"
cased OOV              OOV:<cap>:<word>    "OOV:g:Foo"-> "Foo"

token separator        '|' (0x7C)
internal field sep     ':' (0x3A)
```

Forced seed: `'|' -> 'gA'` keeps the pipe addressable as a token without
breaking stream parsing.

---

## ID tier system (length-tagged)

```
Tier 0   1 char    58 used / 64 reserved   words + punctuation + system
Tier 1   2 char    1,280 IDs               top words + 256 high-freq phrases
Tier 2   3 char    81,920 IDs              mid-frequency mix
Tier 3   4 char    5.2M capacity           long tail (~290k used)
```

Tier is detected by ID length — no DB lookup needed.

Tier 0 layout (locked):

```
SYSTEM (5)         STREAM_START, STREAM_END, CHUNK_BOUNDARY,
                   ATTR_DELIMITER '-', CONTINUATION '_'

ESSENTIAL 26 (26)  a be we do he of to have in on for they i and or not
                   all she this the it with will but you that

STRUCTURAL (27)    <space> <newline> <tab>  . , : ; ! ? ' "
                   ( ) [ ] / \ - — &  % $ # @ * + =

RESERVED FOR S2    PERCEPTION NOVELTY GOAL_PLAN ACTION PROGRESS RESULT
(6, unused)
```

---

## Core invariants

```
1.  NO LEMMATIZATION       Surface form IS the key. "ran" != "run".
2.  UNIFIED FREQUENCY      Words, phrases, whitespace, punctuation all
                           compete in one ranking.
3.  WHITESPACE PRESERVED   Required for JSON/YAML/HTML byte-exactness.
4.  CASE VIA caps_codec    Dict stores lowercase; cap-prefix in stream.
5.  PHRASE ATOMS           167,275 multi-word units mined via PMI.
                           Longest-match scan; decoder is identical.
6.  FORMAT VERSIONED       FORMAT_VERSION=3 in every file header.
                           Mismatch = hard error in reader.
7.  C/C++ PORT-READY       struct.pack('<I', n), no pickle, stateless
                           codec functions, pure char-class tokenizer.
```

---

## Format coverage (proven byte-exact)

> Note on "v1": the `-v1` suffix on the frozen artifacts above
> (`token-ids-v1`, …) is the **vocabulary-contract** version, not a release and
> not the dictionary semver (v0.3.0). The format-coverage set below is simply
> the formats proven byte-exact to date — it carries no version number.

```
.txt  .md  .json  .csv  .xml  .html  .yaml  .log  .srt  .vtt
```

Plus 3 YouTube-transcript JSON files (times_now, jocko, julian).

Ratios on natural-language prose hit 1.97-2.02x. JSON-heavy formats
sit at 0.84-0.99x because the JSON scaffolding is the bottleneck —
that's exactly what the v0.4 compression track addresses.

---

## File map (for cross-project navigation)

```
semantic_compression/
  config.py                    charset, primitives, tier map, FORMAT_VERSION
  tokenizer.py                 char-class tokenizer (invariant: ''.join == s)
  caps_codec.py                pure base64 arithmetic for cap-prefix
  format_adapters.py           per-format pre/post hooks (json, html, etc.)
  corpus_scanner.py            YouTube transcript ingest
  word_frequency_counter.py    surface-form frequency counting
  ngram_counter.py             2-6 word n-gram extraction
  phrase_miner.py              PMI + maximal-phrase filter
  dictionary_builder.py        v0.2 builder (words only)
  dictionary_builder_v03.py    v0.3 builder (words + phrases); --with-facets hook
  compressor.py                encode/decode for .elo and .eloB
  normalize.py                 facet surface-normalization contract (NFC+casefold)
  facets.py                      deterministic assign_facet() + load_overrides()
  facet_builder.py               in-place facets+meta build (max_dbs 2->4)
  facet_reader.py                get_facet / get_meta / verify_fingerprint + decoders
  stamp_meta.py                stamp dictionary_release/status into meta (no re-facet)
  verify_*.py                  per-module test harnesses
  verify_facets.py               facets gate suite (T1/T3/T4/T5/T6/T9/T10/T12)
  verify_lossless.py           full round-trip proof harness
  test_facets.py                 facets unit tests (temp-LMDB, corruption, overrides)
  data/
    facet_overrides.tsv          human-editable facet overrides (starter)
  db/
    dictionary.lmdb            production LMDB store (4 named DBs: forward/
                               reverse/facets/meta)
    dict_stats_facets.json       facets build report (histograms, fingerprint)
    canonical.db               legacy SQLite (deprecated, kept for ref)
  samples/                     one byte-perfect test file per covered format
  docs/
    compression/spec-v0.3.md   v0.3 spec
    compression/spec-v0.4.md   v0.4 tracks
    compression/spec-facets-db.md facets layer (v2) — System-1 detail
    compression/benchmark-v0.3.md
    compression/v0.3-analysis.md
    v1/profiles.md             LLM vocabulary contract
  SYSTEM1.md                   current build status (v0.4.0 staged)
  ../Memory/docs/SEMANTIC_FACETS_SPEC