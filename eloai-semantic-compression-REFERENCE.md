# EloAI Semantic Compression — Portable Reference
> Drop-in context for another Claude Code project that needs to call into,
> consume from, or interoperate with the System 1 compression pipeline.
> Last sync: v0.3.0 (2026-06-07). Repository:
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

**Status:** v0.3.0 production. 13/13 test files byte-exact round-trip.
Avg ratio 1.99x on transcripts, 55% stream-token reduction vs v0.2.

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

LMDB store has two named DBs in one env: `forward` (surface -> ID) and
`reverse` (ID -> surface). ~100ns lookup. C-readable directly.

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

## v1 format coverage (proven byte-exact)

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
  dictionary_builder_v03.py    v0.3 builder (words + phrases)
  compressor.py                encode/decode for .elo and .eloB
  verify_*.py                  per-module test harnesses
  verify_lossless.py           full round-trip proof harness
  db/
    dictionary.lmdb            production LMDB store (two named DBs)
    canonical.db               legacy SQLite (deprecated, kept for ref)
  samples/                     one byte-perfect test file per v1 format
  docs/
    compression/spec-v0.3.md   current spec
    compression/benchmark-v0.3.md
    compression/v0.3-analysis.md
    v1/profiles.md             LLM vocabulary contract
  SYSTEM1.md                   current build status
```

---

## How to consume from another project

### Just want to compress/decompress files
```python
from semantic_compression.compressor import encode_file, decode_file
encode_file('input.json', 'output.elo', mode='text')   # or 'binary'
decode_file('output.elo', 'roundtrip.json')
assert open('input.json','rb').read() == open('roundtrip.json','rb').read()
```

### Want to use the vocabulary in an LLM project
Pull the four frozen artifacts (`token-ids-v1.csv.gz`,
`special-tokens-v1.json`, `byte-fallback-v1.csv`, `profile-cuts-v1.json`)
and pick a profile cut. The dictionary is frozen — same IDs across all
profiles, smaller profiles are strict prefixes of larger ones.

### Want to extend the dictionary
You can't. v0.3 is frozen. v0.4 compression track adds a structural
codec layer ABOVE the dictionary, not new IDs. v0.4 LLM track retrains
models against the locked vocab. Any future ID changes will bump
`FORMAT_VERSION` and ship a versioned reader.

---

## v0.4 roadmap (open tracks)

```
v0.4 COMPRESSION   Structure-Aware Transcript JSON Codec
                   Target JSON-specific redundancies:
                     repeated keys, segment templates,
                     timestamp deltas, speaker dicts.
                   Goal: 2.5x+ on transcript JSON.

v0.4 LLM           Train 3B/8B/14B against the locked v0.3 vocab.
                   Validate on-device thesis:
                     3B int4 + Tiny/Compact fits 2 GB.
                     55% token reduction -> 2-3x effective context.
```

Orthogonal. Dictionary frozen for both.

---

## Locked design decisions (do not relitigate)

| Decision           | Choice                          | Why                                  |
|--------------------|---------------------------------|--------------------------------------|
| Charset            | URL-safe Base64 `A-Za-z0-9-_`   | Safe in SQLite keys, paths, APIs     |
| Tier detection     | ID length                       | No DB lookup needed                  |
| Lemmatization      | NONE                            | Surface form IS the key              |
| Whitespace         | Preserved                       | Required for structured formats      |
| Case               | caps_codec prefix               | Lowercase canonical, cased on stream |
| Phrase detection   | Longest match first             | Prevents multi-word fragmentation    |
| Storage            | LMDB (two named DBs)            | ~100ns lookup, C-readable            |
| Stream delimiter   | `'|'` (0x7C)                    | Forced seed prevents parse breakage  |

---

## Reference

```
Repository:     github.com/4waymedia/semantic-compression
.elo spec:      github.com/4waymedia/elo-format
Project:        https://eloai.dev
Foundation:     Surov (2022), Quantum Core Affect (frontiersin.org)
EPA basis:      Osgood et al. (1975), Evaluation-Potency-Activity
```
