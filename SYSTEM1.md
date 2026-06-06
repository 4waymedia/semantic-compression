# EloAI — System 1: Base64 Canonical Library
### Status Document
> Reflects actual code at github.com/4waymedia/semantic-compression
> Last verified: 2026-06-06 -- byte-exact round-trip proven on 10/10 v1 formats

---

## Status: LOSSLESS FOUNDATION COMPLETE

All v1 components are built, verified, and proven byte-exact lossless across
the full v1 format scope (.txt .md .json .csv .xml .html .yaml .log .srt .vtt).
`verify_lossless.py` is the canonical proof.

Step 9 (compressor.py) is the only remaining v1 work — it packages the
already-proven encode/decode into a stable public API. No design changes needed.

---

## Core Design Decisions — Locked

```
1. NO LEMMATIZATION
   Surface form = dictionary key. Direct lookup. No NLP processing.
   "running", "ran", "run" each get their own ID.

2. UNIFIED FREQUENCY MODEL
   All tokens compete in one frequency ranking — words, contractions,
   punctuation, whitespace runs, numbers. Most frequent gets shortest ID.
   No separate word_library / phrase_library at the dictionary layer.

3. UNIVERSAL CHARACTER-CLASS TOKENIZER
   tokenizer.tokenize(text) -> list[str]
   Invariant: ''.join(tokenize(s)) == s for any UTF-8 string.
   Rules: alnum runs + interior joiners (' . _) form words.
          Whitespace runs are their own tokens.
          Everything else is single-char punct.
   No language model. Direct C translation.

4. WHITESPACE IS PRESERVED, NOT COLLAPSED
   Every space, tab, newline, indentation pattern is a token.
   Required for byte-exact round-trip on .json .yaml .html .xml.

5. CASE PRESERVED VIA caps_codec
   Dictionary stores lowercase canonical forms.
   In-vocab cased tokens emit '<cap>:<ID>' in the stream.
   OOV tokens emit 'OOV:<cap>:<lower>' (caps_codec.encode_oov).
   Fast path: all-lowercase tokens emit just '<ID>' (no overhead).

6. TIER 0 = 58 LOSSLESS POINTERS
   See Tier 0 Layout section. 26 words + 27 structural + 5 system.
   Tier 0 covers ~67% of YouTube corpus tokens, dominated by single-space.

7. 4-CHAR (TIER 3) IS THE PRODUCTION BOUNDARY
   Tier 1 (2-char): 1,280 IDs    -- top words
   Tier 2 (3-char): 81,920 IDs   -- mid-frequency
   Tier 3 (4-char): 5.2M IDs     -- long tail
   All corpus tokens fit within Tier 3.

8. LMDB IS PRODUCTION STORAGE
   ~100ns lookup. Memory-mapped. C-readable directly.
   Two named DBs in one env: forward (text->id) and reverse (id->text).
   All keys/values are UTF-8 bytes; no pickle.

9. 100% LOSSLESS -- PROVEN
   decode(encode(file_bytes)) == file_bytes for every v1 format.
   Round-trip is the only acceptance criterion.
   verify_lossless.py runs the proof end-to-end.

10. C/C++ PORTABILITY ENFORCED
    FORMAT_VERSION + STREAM_ENCODING locked in config.py.
    struct.pack('<I', n) for integer values (no pickle).
    Stateless codec functions. Pure character-class tokenizer.
```

---

## v1 Format Coverage -- PROVEN

```
Format   Round-trip   Notes
-------- ----------   ------------------------------------------------
.txt        PASS      natural language baseline
.md         PASS      markdown with code blocks + emphasis
.json       PASS      structured with key repetition
.csv        PASS      tabular with quoted fields
.xml        PASS      RSS feed sample
.html       PASS      full document with tags + entities
.yaml       PASS      indentation preserved exactly
.log        PASS      timestamps + levels
.srt        PASS      subtitles with timestamps + quotes
.vtt        PASS      WEBVTT with cue blocks
```

All 10 verified by `python -m semantic_compression.verify_lossless`.

```
NOT v1 (deferred):
.py .js .ts .sql   -- code (would benefit from per-format tokenizer rules)
.pdf .docx .xlsx   -- binary containers (need format-specific decoders)
```

---

## Tier 0 Layout (64 slots)

```
SLOT  TOKEN         ROLE
----  -----------   -----------------------------------------
-- SYSTEM (5) --
0     STREAM_START
1     STREAM_END
2     CHUNK_BOUNDARY
-     ATTR_DELIMITER         (reserved for FLEX mode)
_     CONTINUATION           (reserved)

-- ESSENTIAL 26 WORDS (26) --
A 'a'    B 'be'   C 'we'   D 'do'   E 'he'   F 'of'   G 'to'
H 'have' I 'in'   J 'on'   K 'for'  L 'they' M 'i'    N 'and'
O 'or'   P 'not'  Q 'all'  R 'she'  S 'this' T 'the'  U 'it'
V 'with' W 'will' X 'but'  Y 'you'  Z 'that'

-- STRUCTURAL: WHITESPACE + PUNCTUATION + SYMBOLS (27) --
g <space>  h <newline>  i <tab>
j '.'      k ','        l ':'      m ';'      n '!'      o '?'
p "'"      q '"'        r '('      s ')'      t '['      u ']'
v '/'      w '\\'       x '-'      y '—'      z '&'
3 '%'      4 '$'        5 '#'      6 '@'      7 '*'      8 '+'      9 '='

-- RESERVED FOR SYSTEM 2 PROCESS STAGES (6) --
a PERCEPTION   b NOVELTY   c GOAL_PLAN
d ACTION       e PROGRESS  f RESULT
```

Tier detection is by LENGTH:
  length 1 = Tier 0 (single-char ID or system marker)
  length 2 = Tier 1 (dictionary lookup)
  length 3 = Tier 2
  length 4 = Tier 3

g-z work as both Tier 0 single-char IDs AND Tier 1/2/3 first chars
without ambiguity because length resolves the disambiguation.

---

## Stream Format

```
lowercase in-vocab:   <ID>                  e.g. "T"           -> "the"
cased in-vocab:       <cap>:<ID>            e.g. "g:T"         -> "The"
lowercase OOV:        OOV:A:<word>          e.g. "OOV:A:foo"   -> "foo"
cased OOV:            OOV:<cap>:<word>      e.g. "OOV:g:foo"   -> "Foo"

tokens separated by   '|'                   (PIPE_BYTE = 0x7C)
internal field sep    ':'                   (OOV_SEP_BYTE = 0x3A)

Cap chars: ceil(len(word)/6) Base64 chars encoding a 6-bit-per-char
           bitmask, MSB-first, of which positions are uppercase.
           See caps_codec.py for spec + reversibility proof.

Forced dictionary seeds:
   '|'  -> 'gA'   (Tier 1 -- must be in dictionary so it cannot appear
                   as OOV body content, which would break stream parsing)
```

FORMAT_VERSION = 1 will be embedded in every .elo file header by Step 9.

---

## Components

```
File                          Status      Verification
----                          ------      ---------------------------
config.py                     COMPLETE    verify_config.py     -- 13/13 PASS
corpus_scanner.py             COMPLETE    -- YouTube-specific input adapter
tokenizer.py                  COMPLETE    verify_tokenizer.py  -- 50 cases PASS
format_adapters.py            COMPLETE    verify_adapters.py   -- 10/10 PASS
caps_codec.py                 COMPLETE    verify_caps.py       -- 22 cases PASS
word_frequency_counter.py     COMPLETE    -- 186M tokens, 206k forms
dictionary_builder.py         COMPLETE    -- 100% corpus coverage
samples/ (10 files)           COMPLETE    -- one per v1 format
verify_lossless.py            COMPLETE    -- 10/10 BYTE-EXACT PASS
                                         (the canonical proof)

compressor.py                 PENDING     Step 9 -- API packaging only
benchmarks.py                 PENDING     Step 10 -- ratio/speed report
```

---

## Build + Verify Order

```
config.py
  --> verify_config.py

corpus_scanner.py (YouTube-specific input)
  --> verify_scanner.py

tokenizer.py (universal char-class tokenizer)
  --> verify_tokenizer.py

format_adapters.py (per-format byte<->text)
  --> verify_adapters.py

caps_codec.py (lossless case encoding)
  --> verify_caps.py

word_frequency_counter.py (counts via tokenize)
  --> outputs data/word_frequencies.txt

dictionary_builder.py (LMDB build)
  --> outputs db/dictionary.lmdb + db/dict_stats.json
  --> 53 Tier 0  +  1,280 Tier 1  +  81,920 Tier 2  +  123,390 Tier 3
  --> 100% corpus token coverage, 0% OOV

verify_lossless.py (END-TO-END BYTE-EXACT PROOF)
  --> 10/10 v1 sample formats round-trip PASS
```

---

## Dictionary Build Stats (current)

```
Corpus:                  YouTube transcripts, 14,806 files
Total tokens counted:    186,403,791
Unique surface forms:    206,639

Tier coverage:
  Tier 0 (1-char):           53 IDs      67.2% token coverage
  Tier 1 (2-char):        1,280 IDs      25.6% token coverage
  Tier 2 (3-char):       81,920 IDs       7.1% token coverage
  Tier 3 (4-char):      123,390 IDs       0.1% token coverage
  ------                ----------       ------
  Total assigned:       206,643 IDs     100.0% corpus coverage

Single space alone consumes ~47% of all corpus tokens with a 1-char ID.
That is the largest compression contribution available in the dictionary.

Forced seeds:
  '|'  -> 'gA'  (first Tier 1 slot; guarantees safe stream parsing)
```

---

## Round-trip Test Results (verify_lossless.py)

```
FILE              BYTES   STREAM   RATIO   TIER0/1/2/3        CAP   OOV   STATUS
plain.txt           274      327   0.84x   75/16/14/2           9     4   PASS
readme.md           451      656   0.69x   92/11/29/4          10    30   PASS
config.json         410      683   0.60x   118/9/23/2           5    29   PASS
contacts.csv        298      432   0.69x   55/16/26/0          20     7   PASS
feed.xml            798     1182   0.68x   99/86/58/5          16    32   PASS
page.html           659     1136   0.58x   104/107/45/3         8    33   PASS
service.yaml        537      816   0.66x   81/15/29/1           3    40   PASS
server.log          637      901   0.71x   127/31/75/3         18    17   PASS
episode.srt         401      556   0.72x   113/41/35/1          6     5   PASS
episode.vtt         298      488   0.61x   74/17/24/1           3    15   PASS
                  -----    -----   -----                                  ----
TOTAL             4,763    7,177   0.66x                                  10/10
```

Stream sizes exceed source on these tiny samples because:
  - Pipe delimiter (1 byte) costs amortise badly under ~1KB
  - JSON keys / HTML tags / YAML keywords are absent from the YouTube
    corpus and route through OOV (cost: 6 + len(body) bytes)
  - Dictionary trained on transcripts will always show OOV bloat on
    code-adjacent formats

This is acceptable for v1. Step 10 binary stream format will recover
~25% by encoding base64 IDs as actual 6-bit binary, and per-format or
broader-corpus dictionaries will close the OOV gap on structured data.

Correctness > ratio. Correctness is proven.

---

## Critical Requirement: 100% Lossless

```
For each v1 format:
    decode(encode(file_bytes)) == file_bytes   # byte-exact

VERIFIED:  verify_lossless.py reports 10/10 PASS.
This is the only acceptance criterion for System 1 v1.
```

---

## What Remains

```
Step 9   compressor.py
         Public API surface:
            class Compressor:
                def encode_file(path)  -> bytes
                def decode_bytes(b)    -> bytes
                def encode_text(s)     -> str
                def decode_text(s)     -> str
         File header:  FORMAT_VERSION + source format extension
         Binary I/O for byte-exact preservation across writes.
         CLI entry point.

         No design changes vs. verify_lossless.py's inline logic.
         This is packaging.

Step 10  benchmarks.py
         Ratio + speed per format, on production-size inputs.
         OOV diagnostics + dictionary coverage report.
         Binary stream format experiment (~25% size reduction).
```

---

## Deferred to System 2

```
EPA projection           (seed-word embedding projection)
Process stage labels     (Surov 2022 stage classification)
FAISS index              (vector similarity search)
Sentence-transformers    (embedding model)
Filler weight deltas     (probability modifiers on adjacent tokens)
n-gram phrase mining     (advanced phrase capture beyond unified frequencies)
NUM: prefix optimisation (number bypass to reduce Tier 3 bloat -- nice-to-have)
```

---

## C/C++ Migration Rules (already enforced)

```
1. FORMAT_VERSION embedded in every .elo file header.
2. struct.pack('<I', n) for all stored integers (no pickle).
3. Codec functions stateless across module boundaries.
4. caps_codec is pure base64 arithmetic; no Python idioms in the math.
5. tokenizer is pure character-class predicates; direct C translation.
6. Module structure mirrors planned C API surface.
```

See CLAUDE.md "C/C++ Migration Readiness" for details.

---

## Reference

```
Repository:     github.com/4waymedia/semantic-compression
.elo format:    github.com/4waymedia/elo-format
EloAI:          https://eloai.dev
Architecture:   ../CLAUDE.md
```
