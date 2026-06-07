# EloAI — System 1: Base64 Canonical Library
### Status Document
> Reflects actual code at github.com/4waymedia/semantic-compression
> Last verified: 2026-06-07 -- v0.3.0 released; 13/13 round-trip byte-exact

---

## Status: v0.3.0 RELEASED -- Phrase Dictionary and LLM Vocabulary Contract

System 1 has shipped two production milestones and is ready for the
parallel tracks defined in v0.4.

| Tag | Date | Headline | Avg ratio | Stream tokens |
|---|---|---|---:|---:|
| `v0.2` | 2026-06-06 | Predictive Binary Token Stream | 1.84x | 988k |
| **`v0.3.0`** | **2026-06-07** | **Phrase Dictionary + LLM Vocab Contract** | **1.99x** | **449k (-55%)** |

The v0.3 milestone was reframed from a compression target to a
vocabulary target after measured results revealed the structural ceiling
of the fixed-tier byte scheme. See `docs/compression/v0.3-analysis.md`
for the theory-vs-practice retrospective.

---

## Core Design Decisions — Locked

```
1. NO LEMMATIZATION
   Surface form = dictionary key. Direct lookup. No NLP processing.
   "running", "ran", "run" each get their own ID.

2. UNIFIED FREQUENCY MODEL
   All tokens compete in one frequency ranking -- words, phrases,
   contractions, punctuation, whitespace runs.

3. UNIVERSAL CHARACTER-CLASS TOKENIZER
   tokenizer.tokenize(text) -> list[str]
   Invariant: ''.join(tokenize(s)) == s for any UTF-8 string.

4. WHITESPACE PRESERVED, NOT COLLAPSED
   Required for byte-exact round-trip on .json .yaml .html .xml.

5. CASE PRESERVED VIA caps_codec
   Dictionary stores lowercase canonical forms.
   In-vocab cased tokens emit '<cap>:<ID>' in the stream.
   OOV tokens emit 'OOV:<cap>:<lower>' (caps_codec.encode_oov).

6. PHRASE ATOMS (v0.3)
   Common multi-word sequences ('you know', 'i don't know',
   'at the end of the day') get single dictionary IDs.
   167,275 phrases mined via PMI + maximal-phrase filtering.
   Longest-match scan in encoder; decoder unchanged.

7. TIER 0 = 58 LOSSLESS POINTERS
   26 words + 27 structural + 5 system.
   ~67% of corpus token coverage, dominated by single-space.

8. 4-CHAR (TIER 3) IS THE PRODUCTION BOUNDARY
   Tier 1 (2-char): 1,280 IDs    -- top words + 256 high-freq phrases
   Tier 2 (3-char): 81,920 IDs   -- mid-frequency mix
   Tier 3 (4-char): 5.2M IDs     -- long tail (~290k used in v0.3)

9. LMDB IS PRODUCTION STORAGE
   ~100ns lookup. Memory-mapped. C-readable directly.
   Two named DBs in one env: forward + reverse.

10. 100% LOSSLESS -- PROVEN
    decode(encode(file_bytes)) == file_bytes for every v1 format.
    13/13 test files (10 v1 samples + 3 transcripts) byte-exact.

11. STANDARDIZED LLM VOCAB PROFILES (v0.3)
    Five frozen subsets of the dictionary published as the
    public LLM-training contract. See docs/v1/profiles.md.

12. C/C++ PORTABILITY ENFORCED
    FORMAT_VERSION + STREAM_ENCODING locked in config.py.
    struct.pack('<I', n) for stored integers.
    Stateless codec functions, pure character-class tokenizer.
```

---

## v1 Format Coverage -- PROVEN

```
Format   Round-trip   v0.3 ratio  Notes
-------- ----------   ----------- ------------------------------------
.txt        PASS      1.38x       natural language baseline
.md         PASS      0.97x       markdown with code blocks
.json       PASS      0.84x       structured (OOV-dominated at <1 KB)
.csv        PASS      0.87x       tabular with quoted fields
.xml        PASS      0.90x       RSS feed sample
.html       PASS      0.80x       full document with tags + entities
.yaml       PASS      0.86x       indentation preserved exactly
.log        PASS      0.99x       timestamps + levels
.srt        PASS      1.12x       subtitles with timestamps
.vtt        PASS      0.89x       WEBVTT with cue blocks

3 transcript stress test:
times_now   PASS      1.99x       2.4 MB news transcript
jocko       PASS      2.02x       1.0 MB military podcast
julian      PASS      1.97x       0.8 MB interview
```

Small-sample sub-unity ratios are header-overhead amortisation, not
correctness issues. Sample files are <1 KB.

---

## Tier 0 Layout (64 slots)

```
SLOT  TOKEN         ROLE
----  -----------   -----------------------------------------
-- SYSTEM (5) --
0     STREAM_START
1     STREAM_END
2     CHUNK_BOUNDARY
-     ATTR_DELIMITER         (cap-prefix separator)
_     CONTINUATION           (reserved)

-- ESSENTIAL 26 WORDS (26) --
A 'a'    B 'be'   C 'we'   D 'do'   E 'he'   F 'of'   G 'to'
H 'have' I 'in'   J 'on'   K 'for'  L 'they' M 'i'    N 'and'
O 'or'   P 'not'  Q 'all'  R 'she'  S 'this' T 'the'  U 'it'
V 'with' W 'will' X 'but'  Y 'you'  Z 'that'

-- STRUCTURAL (27) -- whitespace + punctuation + symbols --
g <space>  h <newline>  i <tab>
j '.'  k ','  l ':'  m ';'  n '!'  o '?'  p "'"  q '"'
r '('  s ')'  t '['  u ']'  v '/'  w '\\'  x '-'  y '—'  z '&'
3 '%'  4 '$'  5 '#'  6 '@'  7 '*'  8 '+'  9 '='

-- RESERVED FOR SYSTEM 2 PROCESS STAGES (6) --
a PERCEPTION   b NOVELTY   c GOAL_PLAN
d ACTION       e PROGRESS  f RESULT
```

Tier detection by LENGTH (1 = Tier 0, 2 = Tier 1, 3 = Tier 2, 4 = Tier 3).
g-z double-duty as both Tier 0 single-char IDs AND Tier 1/2/3 first chars
without ambiguity because length resolves the disambiguation.

---

## Stream Format

```
lowercase in-vocab:   <ID>                  e.g. "T"           -> "the"
cased in-vocab:       <cap>:<ID>            e.g. "g:T"         -> "The"
phrase atom:          <ID>                  e.g. "wB"          -> "you know"
lowercase OOV:        OOV:A:<word>          e.g. "OOV:A:foo"   -> "foo"
cased OOV:            OOV:<cap>:<word>      e.g. "OOV:g:foo"   -> "Foo"

tokens separated by   '|'                   (PIPE_BYTE = 0x7C)
internal field sep    ':'                   (OOV_SEP_BYTE = 0x3A)

Forced dictionary seeds:
   '|'  -> 'gA'   (must be in dictionary to prevent stream-parsing breakage)
```

Binary wire format (`.eloB`): tier-tagged variable-length encoding,
1-4 bytes per ID, no delimiters. See `compressor.py` for the byte spec.

---

## Components

```
File                          Status         Verification
----                          ------         -----------------------
config.py                     v0.3 LOCKED    verify_config.py
corpus_scanner.py             v0.2 LOCKED    (YouTube-specific adapter)
tokenizer.py                  v0.2 LOCKED    verify_tokenizer.py
format_adapters.py            v0.2 LOCKED    verify_adapters.py
caps_codec.py                 v0.2 LOCKED    verify_caps.py
word_frequency_counter.py     v0.2 LOCKED    -- 186M tokens, 207k forms
ngram_counter.py              v0.3 LOCKED    -- 233k phrase candidates
phrase_miner.py               v0.3 LOCKED    -- PMI + maximal filter
dictionary_builder.py         v0.2 LOCKED    -- words only, reproducible
dictionary_builder_v03.py     v0.3 LOCKED    -- words + 167k phrases
compressor.py                 v0.3 LOCKED    verify_compressor.py
verify_lossless.py            v0.2 LOCKED    -- inline proof harness
samples/ (10 files)           v0.2 LOCKED    -- one per v1 format
```

---

## v0.3 Dictionary Stats (current production)

```
Format version:   3
Total entries:    373,918

  Tier 0 (1-char):       53    pre-seeded (words + structural)
  Tier 1 (2-char):     1,280   1,024 reserved words + 256 high-freq phrases
  Tier 2 (3-char):    81,920   mid-frequency mix
  Tier 3 (4-char):   290,665   long tail

Words in dict:    206,616
Phrases in dict:  167,275    (after dropping 601 zero-savings Tier-0 bigrams)

Profile cuts (LLM vocabulary subsets):
  Tiny       content= 32,496   total= 32,768   (LLaMA 2 size)
  Compact    content= 65,264   total= 65,536   (GPT-2 size)
  Standard   content=130,800   total=131,072   (LLaMA 3 size)
  Full       content=261,872   total=262,144   (research scale)
  Reference  content=373,891   total=374,163   (full dictionary)

Forced seed:  '|' -> 'gA'   (stream-delimiter safety)
```

See `docs/v1/profiles.md` for the full profile contract.

---

## What Remains -- v0.4 Tracks (parallel)

The v0.3 milestone closes System 1's vocabulary work. Two parallel
v0.4 tracks open:

```
v0.4 -- COMPRESSION TRACK
        Structure-Aware Transcript JSON Codec.
        Target the JSON-specific redundancies that fixed-tier
        dictionary compression cannot reach:
          - repeated JSON field keys
          - segment record templates
          - timestamp delta encoding
          - speaker dictionaries
          - known record shapes
        Goal: file ratio 2.5x+ on transcript JSON.

v0.4 -- LLM TRACK
        Retrain 3B/8B/14B models against the locked v0.3 vocabulary
        contract. Validate the on-device thesis:
          - 3B-class fits in 2 GB at int4 + Tiny/Compact profile
          - 55% token reduction translates to 2-3x effective context
          - Phrase-atom training improves semantic atomicity
        Goal: prove the vocabulary contract delivers measurable
        downstream model quality + memory wins.
```

The two tracks are orthogonal. The dictionary is frozen for both.

---

## Critical Requirement: 100% Lossless

```
For each v1 format and each transcript:
    decode(encode(file_bytes)) == file_bytes   # byte-exact

VERIFIED at v0.3.0:
    10/10 v1 samples PASS
    3/3 transcripts PASS
    Both text (.elo) and binary (.eloB) wire formats PASS

This is non-negotiable. Any future change that breaks round-trip
on the existing test set must include a versioned reader/writer
to preserve the v0.3.0 contract.
```

---

## Deferred to System 2

```
EPA projection           (seed-word embedding projection)
Process stage labels     (Surov 2022 stage classification)
FAISS index              (vector similarity search)
Sentence-transformers    (embedding model)
Filler weight deltas     (probability modifiers on adjacent tokens)
NUM: prefix optimisation (number bypass)
Per-document context windows
Adaptive dictionary supplementation
```

---

## C/C++ Migration Rules (enforced)

```
1. FORMAT_VERSION embedded in every .elo file header.
2. struct.pack('<I', n) for all stored integers (no pickle).
3. Codec functions stateless across module boundaries.
4. caps_codec is pure base64 arithmetic; no Python idioms.
5. tokenizer is pure character-class predicates; direct C translation.
6. Module structure mirrors planned C API surface.
```

See `../CLAUDE.md` "C/C++ Migration Readiness" for details.

---

## Reference

```
Repository:     github.com/4waymedia/semantic-compression
.elo format:    github.com/4waymedia/elo-format
EloAI:          https://eloai.dev
Architecture:   ../CLAUDE.md

Tagged releases:
  v0.2          Predictive Binary Token Stream
  v0.3.0        Phrase Dictionary and LLM Vocabulary Contract

Active docs:
  docs/compression/spec-v0.3.md           current spec
  docs/compression/benchmark-v0.3.md      v0.3 measurements
  docs/compression/v0.3-analysis.md       theory vs practice retro
  docs/v1/profiles.md                     LLM vocab contract
  elo_file_format/ELO_FILE_FORMAT.md      file format spec (older draft)
```
