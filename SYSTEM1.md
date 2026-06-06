# EloAI — System 1: Base64 Canonical Library
### Status Document + Remaining Build Spec
> Reflects actual code at github.com/4waymedia/semantic-compression
> Last reviewed: June 2026 — updated after full design session

---

## Core Design Decisions — Locked

```
1. NO LEMMATIZATION
   Surface form = ID. Direct lookup. No NLP processing.
   "running", "ran", "run" each get their own ID.
   Simple, fast, mechanical. Optimize later if needed.

2. UNIFIED FREQUENCY MODEL
   Words, phrases, and sentences all compete in one frequency ranking.
   Most frequent unit → shortest ID. Regardless of unit type or length.
   "aaa" can map to "the cat climbed the tree" if it appears enough.
   No separate word_library vs phrase_library concept at the miner level.

3. TOP 50 UNIVERSAL WORDS → 1-CHAR IDs
   Based on global English frequency data (not corpus-specific).
   These 50 words cover ~50% of all tokens in any English text.
   Remaining single-char slots: 3 system markers + reserved.

4. 3-CHAR IS THE PRODUCTION BOUNDARY
   262,144 IDs in 3-char space = ~14MB LMDB = fits entirely in RAM.
   Covers all target LLM vocabularies + 100,000+ phrases + common sentences.
   4-char reserved for future expansion only — not built in System 1.

5. LMDB IS PRODUCTION STORAGE
   Memory-mapped. ~100ns lookup. Entire dictionary in RAM.
   SQLite used only for inspection and development tooling.

6. 100% LOSSLESS — NON-NEGOTIABLE
   encode(text) → stream
   decode(stream) == text   # exactly, always
   OOV marker preserves any word not yet in dictionary.
   The compressor is a dictionary lookup, not a transformation.
```

---

## What Is Already Built ✓

### config.py — COMPLETE (needs TOP 50 update)
- URL-safe Base64 charset (A-Za-z0-9-_)
- 26 single-char word IDs (A-Z) — **needs expanding to top 50**
- 3 system stream markers (0=STREAM_START, 1=STREAM_END, 2=CHUNK_BOUNDARY)
- 2 stream format separators (- and _)
- 6 reserved slots (a-f) held for System 2 process stages
- Filler classification map (COGNITIVE/DISCOURSE/VALIDATION/HEDGE/EMPHASIS/EMOTIONAL)
- STABLE and FLEX compression mode configs

**One pending update:**
Expand WORD_IDS from 26 (A-Z) to top 50 universal English words.
Use remaining available single-char slots from g-z and 3-9.
Source: global English frequency corpus (Oxford/Google Ngram data).

---

### corpus_scanner.py — COMPLETE
- JSON transcript ingestion with recursive directory scan
- Two-pass ASR deduplication:
  - Pass 1: within-chunk rolling-window phrase repetition removal
  - Pass 2: cross-chunk boundary overlap removal
- Text cleaning: lowercase + whitespace normalization
- Yields (video_id, chunk_id, clean_text, metadata) tuples
- Resumable: corpus_stats table tracks processed files
- scan_single() for unit test spot-checks

---

### library_builder.py — PARTIALLY SUPERSEDED
Built but contains lemmatization + EPA + FAISS phases that are
deferred to System 2. For System 1, the relevant phases are:

```
Phase 1: count_frequencies  ← KEEP  (surface form counting)
Phase 2: lemmatize_vocab    ← SKIP  (no lemmatization in System 1)
Phase 3: assign_ids         ← KEEP  (frequency rank → ID)
Phase 4: embed_words        ← DEFER (System 2)
Phase 5: project_epa        ← DEFER (System 2)
Phase 6: classify_stages    ← DEFER (System 2)
Phase 7: write_db           ← REPLACE with LMDB writer
Phase 8: build_faiss        ← DEFER (System 2)
```

System 1 needs a simplified `dictionary_builder.py` that runs
Phase 1 + Phase 3 only, then writes to LMDB.

---

### verify_config.py — EXISTS
### verify_scanner.py — EXISTS
### verify_library.py — EXISTS (tests old library_builder — needs updating)

---

## What Remains To Build

### Step 4 — word_frequency_counter.py  ← NEXT

Simple script. Scans all transcripts. Counts every surface form.
Outputs sorted frequency list. No NLP. Just counting.

```python
"""
word_frequency_counter.py — Step 4 of System 1

Scans all transcript JSON files.
Counts every surface form word frequency.
Outputs word_frequencies.txt sorted by frequency descending.

Run this first to see:
  - Total unique surface forms in corpus
  - Actual top words (validate against hardcoded TOP 50)
  - Natural tier boundary points

Usage:
    python word_frequency_counter.py
    → outputs word_frequencies.txt
    → prints summary stats
"""

from collections import Counter
from pathlib import Path
import json
from tqdm import tqdm

def count_words(transcript_dir: str, output_path: str = "word_frequencies.txt"):
    counts = Counter()

    files = list(Path(transcript_dir).rglob("*.json"))
    for path in tqdm(files, desc="Counting"):
        try:
            data = json.load(open(path))
            for chunk in data.get("chunks", []):
                words = chunk.get("text", "").lower().split()
                counts.update(words)
        except Exception:
            continue

    with open(output_path, "w") as f:
        for word, count in counts.most_common():
            f.write(f"{count}\t{word}\n")

    print(f"Total unique surface forms: {len(counts):,}")
    print(f"Total tokens:               {sum(counts.values()):,}")
    print(f"Top 10:")
    for word, count in counts.most_common(10):
        print(f"  {count:>10,}  {word}")

    return counts

if __name__ == "__main__":
    count_words("Resources/transcripts")
```

---

### Step 5 — dictionary_builder.py  ← CORE BUILD

Replaces the relevant parts of library_builder.py.
Builds the unified frequency dictionary in LMDB.
No lemmatization. No embeddings. Pure frequency → ID assignment.

```python
"""
dictionary_builder.py — Step 5 of System 1

Builds the canonical Base64 dictionary from corpus frequency data.
Writes to LMDB for production use.

UNIFIED MODEL:
  All text units (words, phrases, sentences) compete in one
  frequency ranking. Most frequent → shortest ID.
  No distinction between "word" and "phrase" at this layer.

ID ASSIGNMENT:
  Rank 1-50:        top 50 hardcoded universal words → 1-char IDs
                    (from config.WORD_IDS — global English frequency)
  Rank 51-4,096:    next most frequent → 2-char IDs (g-z first char)
  Rank 4,097-266,303: next most frequent → 3-char IDs (g-z first char)
  Rank 266,304+:    excluded from System 1 dictionary (OOV at runtime)

STORAGE:
  Primary:   LMDB — two databases
             forward: unit_text → id_bytes  (encode)
             reverse: id_bytes  → unit_text (decode)
  Secondary: word_frequencies.txt (human inspection)
             dictionary_stats.json (build metadata)

OUTPUT:
  dictionary.lmdb   ~14MB   production encode/decode store
  dict_stats.json           build metadata + coverage stats
"""

import lmdb
import struct
from pathlib import Path
from collections import Counter
from tqdm import tqdm
from config import BASE64_CHARS, WORD_IDS, TIER_WORD_FIRST_CHARS


# ID space boundaries
TIER0_MAX = 50            # hardcoded TOP 50 universal words
TIER1_MAX = 4_096         # 2-char IDs: rank 51-4,096
TIER2_MAX = 266_304       # 3-char IDs: rank 4,097-266,303
# Ranks above TIER2_MAX → OOV at encode time


def build_dictionary(
    frequency_file: str = "word_frequencies.txt",
    ngram_file: str = "ngram_frequencies.txt",
    lmdb_path: str = "dictionary.lmdb",
    map_size_gb: int = 1,
) -> dict:
    """
    Build LMDB dictionary from frequency files.

    Args:
        frequency_file:  word_frequencies.txt from word_frequency_counter.py
        ngram_file:      ngram_frequencies.txt from ngram_counter.py
        lmdb_path:       output LMDB directory
        map_size_gb:     LMDB map size in GB (1GB >> actual 14MB usage)

    Returns:
        stats dict with coverage information
    """
    # 1. Load all frequency counts into unified ranking
    all_units = Counter()

    # Load word frequencies
    with open(frequency_file) as f:
        for line in f:
            count, word = line.strip().split('\t', 1)
            all_units[word] = int(count)

    # Load n-gram frequencies (if available)
    if Path(ngram_file).exists():
        with open(ngram_file) as f:
            for line in f:
                count, ngram = line.strip().split('\t', 1)
                all_units[ngram] = int(count)

    # 2. Assign IDs in frequency order
    Path(lmdb_path).mkdir(parents=True, exist_ok=True)
    env = lmdb.open(
        lmdb_path,
        map_size=map_size_gb * 1024**3,
        max_dbs=2,
    )

    forward_db = env.open_db(b'forward')  # text → id
    reverse_db = env.open_db(b'reverse')  # id → text

    # Pre-seed Tier 0: hardcoded TOP 50 universal words
    tier0_assignments = {v: k for k, v in WORD_IDS.items()}  # word → char

    assigned = 0
    tier_counts = {0: 0, 1: 0, 2: 0}

    with env.begin(write=True) as txn:
        # Tier 0 — hardcoded TOP 50
        for word, char_id in tier0_assignments.items():
            key = word.encode('utf-8')
            val = char_id.encode('utf-8')
            txn.put(key, val, db=forward_db)
            txn.put(val, key, db=reverse_db)
            tier_counts[0] += 1

        # Tier 1 + 2 — corpus frequency ranked
        tier1_counter = 0
        tier2_counter = 0

        for rank, (unit, count) in enumerate(
            tqdm(all_units.most_common(), desc="Assigning IDs"), start=1
        ):
            # Skip if already in Tier 0
            if unit in tier0_assignments:
                continue

            # Assign tier by rank
            if rank <= TIER1_MAX:
                token_id = _encode_id(1, tier1_counter)
                tier1_counter += 1
                tier_counts[1] += 1
            elif rank <= TIER2_MAX:
                token_id = _encode_id(2, tier2_counter)
                tier2_counter += 1
                tier_counts[2] += 1
            else:
                break  # beyond 3-char space — OOV at runtime

            key = unit.encode('utf-8')
            val = token_id.encode('utf-8')
            txn.put(key, val, db=forward_db)
            txn.put(val, key, db=reverse_db)
            assigned += 1

    env.close()

    stats = {
        'total_units_in_corpus': len(all_units),
        'total_assigned':        assigned + tier_counts[0],
        'tier0_count':           tier_counts[0],
        'tier1_count':           tier_counts[1],
        'tier2_count':           tier_counts[2],
        'lmdb_path':             lmdb_path,
    }
    return stats


def _encode_id(tier: int, counter: int) -> str:
    """
    Encode sequential counter to Base64 ID for given tier.
    Tier 1 → 2-char, Tier 2 → 3-char.
    First char always from TIER_WORD_FIRST_CHARS (g-z).
    """
    length = tier + 1
    chars = []
    remaining = counter
    for _ in range(length - 1):
        chars.append(BASE64_CHARS[remaining % 64])
        remaining //= 64
    if remaining >= len(TIER_WORD_FIRST_CHARS):
        raise OverflowError(f"Tier {tier} ID space exhausted")
    chars.append(TIER_WORD_FIRST_CHARS[remaining])
    return ''.join(reversed(chars))
```

---

### Step 6 — ngram_counter.py  ← PHRASE MINING

Counts all 2-9 word n-grams across the corpus.
Same pattern as word_frequency_counter.py but for multi-word units.
Output feeds directly into dictionary_builder.py alongside word frequencies.

```python
"""
ngram_counter.py — Step 6 of System 1

Counts all 2-9 word n-grams across transcript corpus.
Output: ngram_frequencies.txt sorted by frequency descending.

These n-grams compete with single words in the unified frequency
ranking. High-frequency phrases earn shorter IDs than rare words.

Examples of what earns a short ID:
  "you know"                 → very high frequency → 2-char ID
  "at the end of the day"    → high frequency → 2 or 3-char ID
  "what i want to say"       → moderate frequency → 3-char ID
  "i already told you that"  → moderate frequency → 3-char ID

Usage:
    python ngram_counter.py
    → outputs ngram_frequencies.txt
    → prints top 20 most common phrases
"""

from collections import Counter
from pathlib import Path
import json
from tqdm import tqdm

NGRAM_MIN = 2
NGRAM_MAX = 9
MIN_FREQUENCY = 10   # discard n-grams below this threshold


def count_ngrams(
    transcript_dir: str,
    output_path: str = "ngram_frequencies.txt",
):
    counts = Counter()

    files = list(Path(transcript_dir).rglob("*.json"))
    for path in tqdm(files, desc="Counting n-grams"):
        try:
            data = json.load(open(path))
            for chunk in data.get("chunks", []):
                words = chunk.get("text", "").lower().split()
                n = len(words)
                for size in range(NGRAM_MIN, min(NGRAM_MAX + 1, n + 1)):
                    for i in range(n - size + 1):
                        ngram = ' '.join(words[i:i + size])
                        counts[ngram] += 1
        except Exception:
            continue

    # Filter below minimum frequency
    counts = Counter({k: v for k, v in counts.items() if v >= MIN_FREQUENCY})

    with open(output_path, "w") as f:
        for ngram, count in counts.most_common():
            f.write(f"{count}\t{ngram}\n")

    print(f"Total unique n-grams (freq≥{MIN_FREQUENCY}): {len(counts):,}")
    print(f"Top 20 phrases:")
    for ngram, count in counts.most_common(20):
        print(f"  {count:>10,}  {ngram!r}")

    return counts
```

---

### Step 7 — compressor.py  ← CORE DELIVERABLE

Lossless encode/decode using the LMDB dictionary.
No lemmatization. Surface form lookup only.
Longest-match-first for phrase detection.

```python
"""
compressor.py — Step 7 of System 1

encode(text) → Base64 stream    (lossless)
decode(stream) → original text  (perfect reconstruction)

ENCODE ALGORITHM:
  Pass 1: longest-match scan
          at each position, try 9-word phrase first, down to 1 word
          first match → emit ID → advance position
          no match possible → emit OOV marker + raw word

  Pass 2: stream assembly
          join IDs with | delimiter
          prepend STREAM_START (0), append STREAM_END (1)

DECODE ALGORITHM:
  split on |
  for each token:
    single char         → config.PRIMITIVES reverse lookup
    OOV:word            → strip marker, return raw word
    anything else       → LMDB reverse lookup → original unit
  rejoin tokens with space

OOV FORMAT:
  "OOV:surface_form"
  Guarantees 100% lossless for any word not in dictionary.
  OOV tokens are candidates for next dictionary build cycle.

ACCEPTANCE TEST:
  decode(encode(text)) == text   # must be true for ALL inputs
"""

import lmdb
from config import WORD_IDS, DB_PATH

STREAM_START    = '0'
STREAM_END      = '1'
CHUNK_BOUNDARY  = '2'
DELIMITER       = '|'
OOV_PREFIX      = 'OOV:'
LMDB_PATH       = 'dictionary.lmdb'


class Compressor:

    def __init__(self, lmdb_path: str = LMDB_PATH):
        self.lmdb_path = lmdb_path
        self._env = None
        self._forward = None
        self._reverse = None

    def _open(self):
        if self._env is None:
            self._env = lmdb.open(self.lmdb_path, readonly=True, max_dbs=2)
            self._fwd_db = self._env.open_db(b'forward')
            self._rev_db = self._env.open_db(b'reverse')
            self._txn = self._env.begin()

    def _lookup_forward(self, unit: str) -> str | None:
        """text → ID. Returns None if OOV."""
        self._open()
        val = self._txn.get(unit.encode('utf-8'), db=self._fwd_db)
        return val.decode('utf-8') if val else None

    def _lookup_reverse(self, token_id: str) -> str | None:
        """ID → text. Returns None if unknown."""
        self._open()
        val = self._txn.get(token_id.encode('utf-8'), db=self._rev_db)
        return val.decode('utf-8') if val else None

    def encode(self, text: str) -> str:
        """
        Encode English text to Base64 ID stream.
        100% lossless — OOV words preserved with OOV: prefix.
        """
        words = text.lower().split()
        tokens = []
        i = 0

        while i < len(words):
            matched = False
            # Try longest phrase first (9 words down to 1)
            for length in range(min(9, len(words) - i), 0, -1):
                unit = ' '.join(words[i:i + length])
                token_id = self._lookup_forward(unit)
                if token_id is not None:
                    tokens.append(token_id)
                    i += length
                    matched = True
                    break
            if not matched:
                # OOV — preserve raw surface form
                tokens.append(f"{OOV_PREFIX}{words[i]}")
                i += 1

        stream = DELIMITER.join(tokens)
        return f"{STREAM_START}{DELIMITER}{stream}{DELIMITER}{STREAM_END}"

    def decode(self, stream: str) -> str:
        """
        Decode Base64 stream to original text.
        Perfect reconstruction guaranteed.
        """
        tokens = stream.split(DELIMITER)
        units = []

        for token in tokens:
            # Skip stream markers
            if token in (STREAM_START, STREAM_END, CHUNK_BOUNDARY):
                continue
            # OOV marker
            if token.startswith(OOV_PREFIX):
                units.append(token[len(OOV_PREFIX):])
                continue
            # Dictionary reverse lookup
            original = self._lookup_reverse(token)
            if original is None:
                raise ValueError(
                    f"Unknown token: {token!r} — "
                    f"dictionary mismatch or corrupt stream"
                )
            units.append(original)

        return ' '.join(units)


# Module-level convenience functions
_default = Compressor()

def encode(text: str) -> str:
    return _default.encode(text)

def decode(stream: str) -> str:
    return _default.decode(stream)

def round_trip_test(text: str) -> bool:
    """Encode then decode. Must equal original. The acceptance test."""
    return decode(encode(text)) == text
```

---

### Step 8 — benchmarks.py  ← FINAL VALIDATION

```python
"""
benchmarks.py — Step 8 of System 1

Runs against sample transcripts and reports:

1. Round-trip accuracy    — MUST be 100% on all transcripts
2. Compression ratio      — chars before / chars after
3. Token reduction        — tokens before / tokens after
4. Dictionary coverage    — % of tokens found in library vs OOV
5. Tier distribution      — % of tokens at each tier (0/1/2/OOV)
6. Processing speed       — tokens/second
7. OOV word list          — candidates for next dictionary build cycle

Outputs: benchmarks/report.json
"""
```

---

## Revised File Structure

```
semantic_compression/
  ├── config.py                  ✓ DONE (update TOP 50 → 1-char IDs)
  ├── corpus_scanner.py          ✓ DONE
  ├── library_builder.py         ✓ EXISTS (phases 1+3 reused, rest deferred)
  ├── verify_config.py           ✓ DONE
  ├── verify_scanner.py          ✓ DONE
  ├── verify_library.py          ✓ EXISTS (update for new model)
  │
  ├── word_frequency_counter.py  ← BUILD NEXT (Step 4)
  ├── dictionary_builder.py      ← Step 5 (LMDB, unified model)
  ├── ngram_counter.py           ← Step 6 (phrase mining 2-9 words)
  ├── compressor.py              ← Step 7 (encode/decode — CORE)
  ├── benchmarks.py              ← Step 8 (final proof)
  │
  ├── db/
  │   ├── dictionary.lmdb        ← PRIMARY: built by dictionary_builder.py
  │   ├── canonical.db           ← SECONDARY: SQLite for inspection only
  │   └── faiss.index            ← DEFERRED: System 2
  ├── data/
  │   ├── transcripts/           ← input JSON
  │   ├── word_frequencies.txt   ← output of word_frequency_counter.py
  │   ├── ngram_frequencies.txt  ← output of ngram_counter.py
  │   └── compressed/            ← output of compressor.py
  └── tests/
      ├── test_compressor.py     ← round_trip_test is THE critical test
      ├── test_dictionary.py
      └── sample_transcript.json
```

---

## Build Order — Revised

```
Step 4  word_frequency_counter.py
        Scan corpus → count all surface form frequencies
        Output: word_frequencies.txt
        Verify: top 10 words match expected universal English words

Step 5  dictionary_builder.py
        Read word_frequencies.txt
        Assign IDs: TOP 50 → 1-char, next → 2-char, next → 3-char
        Write to dictionary.lmdb (forward + reverse)
        Verify: spot-check 20 words decode correctly

Step 6  ngram_counter.py
        Scan corpus → count all 2-9 word n-grams
        Output: ngram_frequencies.txt
        Re-run dictionary_builder.py with both frequency files
        Verify: common phrases decode correctly

Step 7  compressor.py
        Build encode() + decode() using LMDB
        Longest-match-first phrase detection
        OOV handling for unknown words
        Verify: round_trip_test() passes on 100 sample transcripts

Step 8  benchmarks.py
        Run against full sample set
        Report: accuracy, ratio, coverage, speed, OOV list
        PASS criteria: 100% round-trip accuracy on all inputs
```

---

## Critical Requirement: 100% Lossless

```
encode(text) → stream
decode(stream) == text      # EXACTLY. Every time.

This is the only acceptance criterion for System 1.
Everything else (compression ratio, speed, coverage)
is reported but does not block completion.

Only round_trip_test() failing blocks completion.
```

---

## System 1 Contract (Delivery to System 2)

```
dictionary.lmdb     LMDB — forward + reverse lookup, ~14MB
compressor.py       encode(text) + decode(stream) — validated lossless
benchmarks/         accuracy proof, compression stats, OOV word list
word_frequencies.txt + ngram_frequencies.txt — corpus frequency data
```

System 2 (semantic formula + matrix models) receives these.
It does not need to know how compression works internally.

---

## Deferred To System 2

These exist in library_builder.py but are NOT part of System 1:

```
EPA projection        (seed-word embedding projection)
Process stage labels  (Surov 2022 stage classification)
FAISS index           (vector similarity search)
Sentence-transformers (embedding model)
Filler weight deltas  (probability modifiers on adjacent tokens)
```

System 2 reads the compressed streams and adds semantic intelligence.
System 1 just compresses. Nothing more.

---

## Reference

```
Repository:     github.com/4waymedia/semantic-compression
.elo format:    github.com/4waymedia/elo-format (create next)
EloAI:          https://eloai.dev
Design session: claude.ai — search "semantic compression base64 eloai"
CLAUDE.md:      full architectural north star document
```
