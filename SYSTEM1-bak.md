# EloAI — System 1: Base64 Canonical Library
### Status Document + Remaining Build Spec
> Reflects actual code at github.com/4waymedia/semantic-compression
> Last revised: June 2026 — universal-format scope expansion

---

## V1 Scope — Universal Format Coverage

System 1 must compress and decompress **10 file formats** losslessly:

```
.txt    primary — natural language, maximum dictionary coverage
.md     markdown — natural language + simple punctuation patterns
.json   structured — key repetition compresses extremely well
.csv    tabular — column headers repeat every row
.xml    structured — tag vocabulary is small and very repetitive
.html   structured — known fixed tag set, natural language body
.yaml   structured — config files, very repetitive keys
.log    operational — timestamp + status patterns compress hard
.srt    subtitle — timestamp format + natural language
.vtt    subtitle — same as .srt, already in the YouTube pipeline
```

**Acceptance criterion (per format):**
```
decode(encode(file_bytes)) == file_bytes      # byte-exact round trip
```

This drives several architectural decisions that supersede earlier choices:
case preservation for ALL tokens, whitespace preservation, universal
tokenizer, expanded Tier 0 for punctuation and whitespace.

---

## Core Design Decisions — Locked

```
1. NO LEMMATIZATION
   Surface form = ID. Direct lookup. No NLP processing.
   "running", "ran", "run" each get their own ID.

2. UNIFIED FREQUENCY MODEL
   Words, phrases, sentences, punctuation, whitespace runs all compete
   in one frequency ranking. Most frequent unit gets the shortest ID.

3. ESSENTIAL 26 WORDS → 1-CHAR IDs (A-Z)
   Hardcoded from English universal frequency data.
   Cover ~31% of corpus tokens immediately.

4. TIER 0 ALSO HOLDS PUNCTUATION + WHITESPACE
   Common punctuation (. , ? ! " ( )) and whitespace (space, \n, \t)
   get 1-char IDs. Required to keep .json / .xml / .yaml lossless.

5. 3-CHAR IS THE PRODUCTION BOUNDARY
   ~80,000 IDs in 3-char space = entire dictionary in RAM via LMDB.

6. LMDB IS PRODUCTION STORAGE
   Memory-mapped. ~100ns lookup. C-readable directly.
   SQLite used only for inspection and development tooling.

7. 100% LOSSLESS — NON-NEGOTIABLE, ALL FORMATS, ALL BYTES
   encode(file_bytes) → stream
   decode(stream) == file_bytes
   Round-trip test is the only acceptance criterion.

8. UNIVERSAL TOKENIZER
   Same tokenization rule for English text, code, JSON, markdown, YAML.
   Apostrophe rule handles English contractions AND code string delimiters.
   Whitespace runs are tokens, not separators.

9. CASE PRESERVATION FOR EVERY TOKEN
   caps_codec applies to in-vocab tokens as well as OOV.
   Fast path: all-lowercase tokens emit no case prefix (no overhead).
   Stream cost for typical English: ~0 bytes added.
```

---

## Stream Token Format (locked for v1)

```
lowercase in-vocab:    {ID}                  e.g. "T"           → "the"
capitalized in-vocab:  {capchars}:{ID}       e.g. "g:T"         → "The"
lowercase OOV:         OOV:A:{word}          e.g. OOV:A:foo     → "foo"
capitalized OOV:       OOV:{capchars}:{word} e.g. OOV:g:foo     → "Foo"
```

- Stream tokens are separated by `|` (PIPE_BYTE = 0x7C).
- The `:` inside an in-vocab token is unambiguous: word IDs never contain `:`.
- Fast path: lowercase token is one ID, no prefix, no overhead.
- All-lowercase OOV: capchars = "A" (000000), decoder fast-skips.
- FORMAT_VERSION = 1 is written into every .elo file header.

---

## Tier 0 — Final Layout (64 slots)

```
A-Z   (26)  Essential 26 word IDs               LOCKED
0-2    (3)  STREAM_START, STREAM_END, CHUNK_BOUNDARY  LOCKED
- _    (2)  ATTR_DELIMITER, CONTINUATION        LOCKED
3-9    (7)  Punctuation:  . , ? ! " ( )         NEW
g h    (2)  Code structure:  { }                NEW
i j k  (3)  Whitespace: " " "\n" "\t"           NEW
a-f    (6)  RESERVED for System 2 stages        UNCHANGED
l-z   (15)  TIER 1/2 word ID prefixes           NEW (was g-z, 20 chars)
```

**Tier 1/2 capacity revised:**
- Tier 1: 15 × 64       = 960    IDs   (was 1,280)
- Tier 2: 15 × 64²      = 61,440 IDs   (was 81,920)
- Tier 3: 15 × 64³      = 3.9M   IDs

Still abundant for the corpus (~83k unique forms today).

---

## Universal Tokenization Rules

```
Apostrophe (')   Keep interior:  "don't", "it's", "world's" → one token
                 Split boundary: "'hello'" → "'" + "hello" + "'"
                 Rule: letter-on-both-sides → part of word
                       otherwise → standalone token

Double quote (") Always split — never interior to a word in any language

Other punctuation  Strip leading/trailing and emit as standalone token
( . , ? ! ; : ( ) { } [ ] < > = + * / \ @ # $ % & ^ ~ )
                 "rabbits."  → "rabbits" + "."
                 "(hello,"   → "(" + "hello" + ","

Whitespace       Each contiguous whitespace run = one token
                 " " "\n" "\t" "\n\n" "    " etc.
                 Single space, single \n, single \t are Tier 0
                 Other runs flow into Tier 1 by frequency

Numbers          Stay together as one token: "2024", "3.14", "0xFF"
                 Mixed alphanumeric stays together: "v1.2.3"

Unicode          UTF-8 throughout. Multi-byte chars are valid token bytes.
```

These rules are pure character-class logic — no language model needed,
trivially translatable to C.

---

## What Is Already Built ✓

### caps_codec.py — COMPLETE, but scope expands
- Currently used for OOV only
- v1 will use it on in-vocab tokens too (via `{cap}:{ID}` form)
- No code changes required — encode_caps / decode_caps already pure functions

### config.py — COMPLETE, needs Tier 0 expansion
- 26 word IDs locked, system markers locked
- **Pending:** add 12 new Tier 0 entries (7 punctuation + 2 code + 3 whitespace)
- **Pending:** shrink TIER_WORD_FIRST_CHARS from `g-z` to `l-z`

### corpus_scanner.py — PARTIALLY DEFERRED
- ASR dedup logic stays — useful for .vtt and any timestamped transcript
- `clean_text()` lowercase + whitespace collapse is **wrong for v1**
- Becomes a JSON-format input adapter only
- Must NOT be used as the universal tokenizer

### word_frequency_counter.py — COMPLETE for current corpus
- Will be re-run after tokenizer is in place
- Per-format counters added as adapters become available

### dictionary_builder.py — COMPLETE
- Format-agnostic, no changes
- Will be re-run after vocabulary shifts (punctuation tokenized separately)

### library_builder.py — SUPERSEDED
- Original phases 4-8 (EPA, FAISS, stages) remain System 2 territory
- Will be deleted or marked deprecated once dictionary_builder is canonical

---

## What Remains To Build

### Step 6 — tokenizer.py (NEW — was not in original spec)

Pure character-class tokenizer. No language model. Stateless. Direct C-portable.

```python
def tokenize(text: str) -> list[str]:
    """
    Universal token producer.
    Whitespace runs are preserved as their own tokens.
    Apostrophe stays interior between letters, splits at boundaries.
    Other punctuation always splits.
    decode == "".join(tokenize(text))   — invariant
    """
```

Verify: `"".join(tokenize(s)) == s` for any UTF-8 string.

---

### Step 7 — format_adapters.py

Per-format input/output normalization. Each adapter is a small function pair:

```python
def to_text(path: Path) -> str:      # read file, return text suitable for tokenizer
def from_text(text: str, path: Path): # write text back, format-specific
```

| Format | Adapter complexity |
|---|---|
| .txt  | identity — read bytes, decode UTF-8 |
| .md   | identity |
| .vtt  | identity (whitespace preservation is enough) |
| .srt  | identity |
| .log  | identity |
| .csv  | identity (quoting/escapes preserved as raw bytes) |
| .json | identity (whitespace in formatting matters — preserve all) |
| .xml  | identity |
| .html | identity |
| .yaml | identity (indentation meaningful — preserve all) |

Most are identity functions because the universal tokenizer + whitespace
preservation already handles them. Per-format adapters exist for future
extension (CDATA-aware splitting, code-block awareness in .md, etc.).

---

### Step 8 — dictionary_builder.py (re-run after tokenizer ready)

No code changes — just re-execute with the new universal tokenization
so punctuation and whitespace get proper IDs by frequency.

---

### Step 9 — compressor.py

```python
class Compressor:
    def encode_file(self, path: Path) -> bytes:
        """Read file, tokenize, encode, return .elo bytes."""
    def decode_file(self, elo_bytes: bytes, out_path: Path) -> None:
        """Decode .elo back to original file."""
    def encode_text(self, text: str) -> str:    # in-memory variant
    def decode_text(self, stream: str) -> str:
```

Pipeline:
```
file_bytes
  → format_adapter.to_text()              (read + decode UTF-8)
  → tokenize()                            (universal tokenizer)
  → for each token:
       lookup in LMDB              hit → emit ID (with cap prefix if needed)
                                   miss → emit OOV:cap:lower
  → join with "|"
  → prepend FORMAT_VERSION header + STREAM_START
  → append STREAM_END
  → return bytes
```

Decode is the inverse, byte-exact.

---

### Step 10 — round_trip_test_suite.py

Per-format acceptance. Each format gets at least one sample file with
the requirement `decode(encode(x)) == x` enforced byte-exact.

```python
SAMPLES = {
    '.txt':  ['samples/plain.txt', 'samples/lipsum.txt'],
    '.md':   ['samples/readme.md', 'samples/article.md'],
    '.json': ['samples/config.json', 'samples/big_array.json'],
    '.csv':  ['samples/contacts.csv', 'samples/large.csv'],
    '.xml':  ['samples/svg_icon.xml', 'samples/feed.xml'],
    '.html': ['samples/page.html'],
    '.yaml': ['samples/k8s_pod.yaml'],
    '.log':  ['samples/nginx.log'],
    '.srt':  ['samples/episode.srt'],
    '.vtt':  ['samples/youtube.vtt'],
}

for fmt, paths in SAMPLES.items():
    for p in paths:
        original = Path(p).read_bytes()
        compressed = Compressor().encode_file(p)
        recovered  = Compressor().decode_bytes(compressed)
        assert recovered == original, f'{fmt} failed: {p}'
```

This is the ONLY acceptance criterion for System 1 completion.

---

## REBUILD CHECKLIST (after Steps 6 + 7 land)

```
[ ] delete  semantic_compression/db/dictionary.lmdb
[ ] delete  semantic_compression/data/word_frequencies.txt
[ ] rerun   python -m semantic_compression.word_frequency_counter
[ ] rerun   python -m semantic_compression.dictionary_builder
```

The current LMDB and frequency file were produced before the universal
tokenizer existed and contain punctuation-attached entries
("rabbits.", "it's,", '"chris,'). They must be regenerated against the
new tokenizer output before Step 9 (compressor).

---

## Build Order — Revised

```
Step  4  word_frequency_counter.py    ✓ DONE  (will re-run after Step 6)
Step  5  dictionary_builder.py        ✓ DONE  (will re-run after Step 6)

Step  6  tokenizer.py                  ← NEXT
         Universal tokenization, format-agnostic
         Verify: join(tokenize(x)) == x  for diverse strings

Step  7  format_adapters.py
         One read/write pair per format
         Most are identity functions

Step  8  Tier 0 expansion + dictionary rebuild
         Update config.py with new Tier 0 entries
         Re-run word_frequency_counter through the universal tokenizer
         Re-run dictionary_builder for the updated vocabulary

Step  9  compressor.py
         encode_file / decode_file / encode_text / decode_text
         Pipe stream format with case preservation everywhere

Step 10  round_trip_test_suite.py
         10 formats × multiple samples
         Byte-exact decode(encode(x)) == x
```

---

## Per-Format Compression Expectations

```
.txt    natural language    expect ~4-6× compression
.md     mostly text         ~4-6×
.json   key repetition      ~6-10× (heavy structural compression)
.csv    column headers      ~5-8×
.xml    tag vocabulary      ~6-10×
.html   tag + text mix      ~5-8×
.yaml   key repetition      ~6-10×
.log    timestamp patterns  ~5-8× (timestamps + statuses very repetitive)
.srt    timestamp + text    ~5-7×
.vtt    timestamp + text    ~5-7×
```

These are targets, not gates. Round-trip correctness is the gate.

---

## Critical Requirement: 100% Lossless, All Formats

```
For each of the 10 formats listed above:
    decode(encode(file_bytes)) == file_bytes      # byte-exact

This is the ONLY acceptance criterion for System 1.
Compression ratio, speed, coverage are reported but do not block.
Only round-trip failure blocks completion.
```

---

## System 1 Contract (Delivery to System 2)

```
dictionary.lmdb              LMDB — forward + reverse, ~few MB
compressor.py                encode_file + decode_file (10 formats)
tokenizer.py                 universal tokenizer (stateless, C-portable)
format_adapters.py           per-format adapters
caps_codec.py                case preservation (all tokens)
benchmarks/                  accuracy proof + ratio stats per format
word_frequencies.txt         corpus frequency data
samples/                     test files, one per format minimum
```

System 2 receives these and adds semantic intelligence on top of the
compressed streams.

---

## Deferred To System 2

```
EPA projection           (seed-word embedding projection)
Process stage labels     (Surov 2022 stage classification)
FAISS index              (vector similarity search)
Sentence-transformers    (embedding model)
Filler weight deltas     (probability modifiers on adjacent tokens)
n-gram phrase mining     (advanced phrase capture beyond unified frequencies)
```

System 1 compresses byte-exact, nothing more.

---

## C/C++ Migration Rules (enforced now)

```
1. FORMAT_VERSION embedded in every .elo file header
2. struct.pack('<I', n) for all stored integers — no pickle
3. Codec functions stateless across module boundaries
4. caps_codec is pure base64 arithmetic — no Python idioms in the math
5. Public API returns (value, error_code) — no exceptions across boundary
6. Module structure mirrors planned C API surface
```

See CLAUDE.md "C/C++ Migration Readiness" section for details.

---

## Reference

```
Repository:     github.com/4waymedia/semantic-compression
.elo format:    github.com/4waymedia/elo-format
EloAI:          https://eloai.dev
Architecture:   ../CLAUDE.md
```
