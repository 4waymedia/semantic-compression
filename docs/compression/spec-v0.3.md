# ELO Compressor v0.3 Specification
### Phrase-Atom Vocabulary + LLM Readiness

> Status: design — implementation pending
> Predecessor: v0.2 (commit `7fc9152`, tag `v0.2`)
> Target milestone: "Phrase-Atom Compression + Vocabulary Freeze"

---

## Strategic Intent

v0.3 turns the dictionary from a word-level lookup table into a
**phrase-level semantic atom table**. The hypothesis is the EloAI core
thesis applied at scale:

> Compression and semantics are the same operation. A phrase that
> compresses well to a single ID compresses well *because* it is a
> single semantic unit. Promoting it to a single ID makes that fact
> visible to anything reading the stream.

The downstream consumers are:

1. **Compression tool users** — files get 50% smaller than v0.2 on
   natural-language input.
2. **LLM training pipelines** — vocabulary becomes a frozen, semantic,
   phrase-aware contract that retraining can target. Models see
   atomic semantic units instead of tokenized fragments.

---

## Goals

```
G1   Mine the corpus for phrase candidates (2-9 grams)
G2   Score candidates so true semantic atoms beat coincidental n-grams
G3   Promote high-value phrases to the dictionary, sharing Tier 1/2/3
     ID space with single-word entries on a unified frequency ranking
G4   Update the compressor to use longest-match against phrase + word
     entries, byte-exact lossless, before emitting binary stream
G5   Achieve >= 2.5x average compression on the 3-transcript test set
G6   Freeze the v1 vocabulary as a stable LLM-training contract:
     export integer ID mapping + special-token allocation + byte
     fallback table as a publishable artifact
G7   Maintain backwards compatibility: v0.2 .elo files remain
     decodable; v0.3 files are decoded by v0.3 readers only
```

---

## Non-Goals

```
N1   Building the C/C++ port (post-v0.3 work)
N2   Building the LLM training pipeline itself
N3   Building structured-document container (records/index/string-table) —
     that lives in a separate format if ever needed
N4   Semantic profile / EPA / process stage encoding (System 2 territory)
N5   Per-channel auxiliary dictionaries (deferred to v0.4)
N6   N-gram capture beyond 9-grams (diminishing returns in conversational
     corpora; revisit if domain shifts)
```

---

## Design Decisions

### D1 — Phrase candidate scoring uses PMI, not raw frequency

Raw n-gram frequency promotes phrases like `"the the"` or `"and and"`
which are conversational noise, not semantic atoms.

Use **Pointwise Mutual Information** to favour phrases that occur more
than chance would predict from their parts:

```
PMI(w1 w2 ... wn) = log2( P(w1 w2 ... wn) / (P(w1) * P(w2) * ... * P(wn)) )

score = PMI * log(count) * length_bonus(n)
```

`length_bonus(n)` slightly favours longer phrases (more compression per
hit) but does not dominate. Candidate threshold: top N by score, where
N is chosen to fit comfortably in Tier 2 ID space alongside words.

### D2 — Longest-match scan at encode time

Replace the current per-token lookup with **greedy longest-match** over
the source tokens:

```
def encode(text):
    tokens = tokenize(text)
    i = 0
    while i < len(tokens):
        # try longest first: 9, 8, ..., 2, 1
        for span in range(MAX_PHRASE_LEN, 0, -1):
            phrase = ' '.join(tokens[i:i+span])  # or class-aware join
            if phrase in dictionary:
                emit(dictionary[phrase])
                i += span
                break
        else:
            # single token, even Tier 0 must be a hit since corpus coverage = 100%
            raise ValueError("unreachable")
```

Performance: requires either an in-memory phrase trie or LMDB
prefix-scan. Decision deferred to implementation; LMDB prefix iteration
is likely sufficient at corpus scale, with a small RAM cache for the
hottest entries.

### D3 — Phrases store as space-joined token sequences

The dictionary key for a phrase is the literal joined token sequence
including any interior whitespace tokens. Examples:

```
"you know"           → joined key:  "you know"
"i don't know"       → joined key:  "i don't know"
"at the end of the day"           → joined key:  "at the end of the day"
```

Joining uses single space. Phrases containing other whitespace
(newlines, tabs, multi-space runs) are excluded from mining — they are
structural artifacts, not semantic phrases.

### D4 — Punctuation variants are separate atoms

`"don't"`, `"don't,"`, `"don't."`, `"don't?"` are **different phrase
candidates**. Each gets its own dictionary entry if its frequency
warrants. This prevents the encoder from doing punctuation surgery at
match time and keeps the longest-match logic simple.

The frequency table is split organically: trailing-period variants are
common at sentence ends, trailing-comma variants mid-sentence. Both
are real semantic distinctions worth their own IDs.

### D5 — Case handled identically to v0.2 (via caps_codec)

Dictionary keys are always lowercased. The encoder lowercases the
candidate string before lookup, then emits the cap-prefix marker for
mixed-case input — exact same mechanism as v0.2.

`"You Know"` → lowercase → match `"you know"` → emit `[CAP marker][cap chars][phrase ID]`.

### D6 — Unified frequency competition between words and phrases

Phrases and words compete in **one ranked list** ordered by raw
frequency (not PMI score). High-frequency phrases like `"you know"`
will land in Tier 1 ID space alongside single high-frequency words.
Less-frequent phrase atoms slide to Tier 2 or Tier 3.

This is the existing v0.2 ranking behaviour — phrases just join the
candidate pool. No separate phrase-tier system.

### D7 — Tier 3 (4-char IDs) becomes the working capacity

v0.2 used 123,390 Tier 3 slots out of 5,242,880 available. v0.3 is
expected to fill Tier 3 considerably — phrases multiply the addressable
unit count. Architecture already supports it; only the build pipeline
needs to actually populate it.

Target sizing:
```
Tier 0    53 fixed       (words + structural — unchanged from v0.2)
Tier 1   1,280 active    (top words + top phrases)
Tier 2  81,920 active    (mid-frequency words + phrases)
Tier 3  ~500,000 active  (deep tail + rare phrases + per-channel proper nouns)
```

### D8 — Special tokens get 6 reserved Tier 0 slots

The 6 reserved slots (`a-f`) currently labeled `RESERVED_STAGE_*` for
System 2 are repurposed for LLM special tokens:

```
a → <PAD>     padding for batched tensors
b → <BOS>     beginning of sequence
c → <EOS>     end of sequence
d → <UNK>     reserved; OOV mechanism is the real fallback
e → <SEP>     segment / document separator
f → <MASK>    masked-LM placeholder
```

System 2 stage labels move to a Tier 1 allocation if/when needed.
Special tokens are higher priority for v1 vocabulary freeze.

### D9 — Byte fallback for true OOV at LLM training time

Current v0.2 OOV mechanism stores UTF-8 verbatim with a length prefix.
That works for file compression but not for LLM tensors (variable-length
tokens).

For LLM use, we publish an **auxiliary byte-fallback table**: 256
fixed integer IDs mapping to bytes `0x00`-`0xFF`. Training-time
tokenization that hits a true OOV decomposes the byte sequence into
this fallback alphabet. Compression-time .elo files continue to use
the variable-length OOV format.

```
Vocabulary tiers for LLM use:
    [0, 52]              Tier 0: words + structural
    [53, 58]             Special tokens (PAD, BOS, EOS, UNK, SEP, MASK)
    [59, 314]            Byte fallback (256 IDs for bytes 0x00-0xFF)
    [315, 1594]          Tier 1
    [1595, 83514]        Tier 2
    [83515, ~600000]     Tier 3 (depends on final phrase miner output)
```

### D10 — Vocabulary is frozen and additive-only

Once v1 vocabulary ships, **no ID is ever reassigned**. Future
dictionary versions can only ADD new IDs at the end. Models trained
against v1 vocabulary remain decodable against v1.1, v1.2, etc.

This is the core contract for LLM training.

### D11 — Backwards compatibility with v0.2 files

v0.3 readers can decode v0.2 `.elo` files (magic + flags + version
check). v0.2 readers cannot decode v0.3 files — the dictionary they
hold lacks the phrase IDs.

File-level signal:
```
Header byte 4 (flags) bit 0: USES_PHRASES
    0 = v0.2-compatible stream (no phrase IDs used)
    1 = v0.3 stream (may contain phrase atoms)
```

A v0.3 encoder that doesn't actually use any phrase atoms could emit
USES_PHRASES=0 and remain readable by v0.2 readers. Useful for
gradual deployment.

---

## v0.3 File Format Delta

Changes from v0.2 binary format:

```
Offset  Size  Field           v0.2                v0.3
─────   ───   ──────────      ────────────        ──────────────────────
0       4     magic           ELO\x02              ELO1 (locked v1 magic)
4       1     flags           ext_len             flags byte
5       1     ext_len         (header ends)       ext_len
6       N     ext             ext                 ext
6+N     ...   stream          token stream         token stream

flags bit 0:  USES_PHRASES    (new in v0.3)
flags bit 1:  HAS_CHECKSUM    (reserved, not used in v0.3)
flags bits 2-7: reserved, must be 0
```

The v0.2 magic `ELO\x02` and the v1.0 magic `ELO1` (where '1' is ASCII)
happen to differ at byte 4 (`0x02` vs `0x31`), so a single magic-byte
check distinguishes them. No accidental misreading.

---

## Acceptance Criteria

The v0.3 milestone is locked when ALL of the following hold:

```
A1  10/10 v1 sample files round-trip byte-exact (lossless gate)
A2  3/3 transcript files round-trip byte-exact
A3  Average transcript ratio >= 2.5x  (>33% improvement over v0.2's 1.84x)
A4  Encode throughput >= 1.5 MB/s (no worse than 2x slowdown from v0.2)
A5  Decode throughput >= 2.0 MB/s
A6  Vocabulary frozen: token-ids.csv exported and committed
A7  Special tokens reserved in dictionary (PAD/BOS/EOS/UNK/SEP/MASK)
A8  Byte fallback table defined and committed
A9  v0.2 .elo files still decodable via the v0.3 reader (backwards compat)
A10 ELO_FILE_FORMAT.md updated to v1.0 magic + flags layout
```

Failure of A1/A2 is a blocker. A3-A5 are reportable but not blockers — if
phrase mining adds significant compression but slows encode by more than
2x, we negotiate.

---

## Build Order

```
Step 11  ngram_counter.py
         Tokenize corpus chunks via existing tokenizer.
         Count 2-9 grams of word-class tokens.
         Skip n-grams containing whitespace runs > single space.
         Skip n-grams crossing sentence-boundary markers (j = '.').
         Output: data/ngram_frequencies.txt (escaped, frequency-sorted).
         Verify: top 20 n-grams match expected discourse phrases.

Step 12  phrase_miner.py
         Read ngram_frequencies.txt + word_frequencies.txt.
         Compute PMI for each n-gram candidate.
         Apply length_bonus + frequency floor.
         Output: data/phrase_candidates.txt (sorted by score).
         Verify: known phrases ('i mean', 'you know') are in top 100.

Step 13  dictionary rebuild (v0.3)
         Reset LMDB. Reseed Tier 0 (words + structural + special tokens
         + byte fallback). Merge word and phrase frequency tables.
         Assign IDs in unified frequency order.
         Output: db/dictionary.lmdb (v0.3 ranking).
         Output: db/dict_stats.json (stats with phrase counts).
         Output: data/token-ids.csv (frozen vocabulary export).
         Verify: spot-check 30 known words/phrases route to expected tiers.

Step 14  compressor longest-match
         Implement greedy longest-match scan in encode_text and
         encode_bytes_binary. Decoder unchanged.
         Implicit-whitespace transform still applies after phrase match.
         Output: updated compressor.py.
         Verify: 10/10 v1 sample byte-exact round-trip.
         Verify: 3/3 transcript byte-exact round-trip.

Step 15  v0.3 benchmark
         Run test_binary_stream.py and test_compress_transcripts.py.
         Update docs/compression/benchmark-v0.3.md with measured ratios.
         Compare against v0.2 baseline (1.84x).
         Document throughput regression if any.

Step 16  v0.3 milestone
         Commit ELO_FILE_FORMAT.md v1.0 update.
         Commit token-ids.csv (the frozen vocabulary contract).
         Tag v0.3 release.
         Push branches main + tag.
```

Each step verifiable; each step commits a measurable artifact.

---

## Risks + Mitigations

```
R1   PMI scoring promotes garbage n-grams from low-frequency words
     (e.g. typos appearing twice). 
     Mitigation: frequency floor (>= 100 occurrences in corpus).

R2   Longest-match encoder is slow at corpus scale.
     Mitigation: in-memory phrase trie for top 50k phrases, LMDB
     lookup only for the long tail.

R3   Phrase atoms break tokenizer round-trip invariant
     ''.join(tokenize(s)) == s.
     Mitigation: tokenizer unchanged; phrase logic is in the encoder
     layer above the tokenizer. Invariant is preserved.

R4   Punctuation variants explode dictionary size with low-value entries.
     Mitigation: PMI threshold + frequency floor + tier capacity cap.
     If pathological, fall back to "phrases store base form; encoder
     emits trailing-punctuation token separately" -- design decision
     D4 may be revised.

R5   v0.3 dictionary breaks v0.2 stability — every v0.2 ID could
     shift its position in the LMDB.
     Mitigation: v0.2 IDs stay locked. v0.3 phrases get ONLY new IDs.
     This forces phrases to start at Tier 1 capacity slot 1280+ unless
     they out-rank existing Tier 1 words. To keep v0.2 stable, freeze
     the current Tier 0/1/2 word assignments and assign phrases to
     new slots at the same tier they would naturally rank into. This
     compromises the "unified frequency ranking" but preserves
     backwards compatibility. Locked decision: v0.2 IDs are immutable.

R6   LLM vocabulary size becomes unwieldy (>500k tokens).
     Mitigation: ship two profiles -- compact (Tier 0-2 only, ~83k) and
     full (Tier 0-3, ~600k). Models choose at training time.
```

R5 deserves emphasis: **v0.2 IDs are now part of the public contract**.
The v0.3 dictionary builder must preserve every v0.2 ID assignment and
only grow new IDs at the tail. This means phrases compete only for
unfilled tier slots, not for re-ranking existing words.

---

## Open Questions

```
Q1  Should phrase mining run on the full 14,806-file corpus or on a
    held-out training split, leaving a test split for ratio
    measurement?  Answer needed before Step 11.

Q2  What is the publication strategy for token-ids.csv? Bundled in
    the repo at hundreds of MB, or hosted separately (e.g. HuggingFace
    Datasets) with a checksum in the repo?

Q3  At what point do we register the .elo MIME type with IANA? Could
    be after v0.3 milestone or deferred to v1.1.

Q4  Does the C/C++ port target v0.2 (already proven) or wait for v0.3
    (the production vocabulary)?

Q5  Do special tokens need their own reserved magic-byte range in the
    binary stream, separate from regular Tier 0? Answer probably no
    (special tokens are just Tier 0 IDs in slots a-f), but want to
    confirm the encoder/decoder handles them gracefully.
```

---

## References

```
v0.2 benchmark        docs/compression/benchmark-v0.2.md
v0.2 merge commit     7fc9152
SYSTEM1 design        SYSTEM1.md
File format draft     ../../elo_file_format/ELO_FILE_FORMAT.md
Repository            github.com/4waymedia/semantic-compression
EloAI                 https://eloai.dev
```
