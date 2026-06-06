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
3. **On-device inference** — small profiles (Tiny / Compact) combined
   with int4 quantization put 3B-8B parameter models within reach of
   consumer phones. See `docs/v1/profiles.md` -- "On-device inference"
   for the detailed memory math. The phrase-atom vocabulary also acts
   as a context-window multiplier: same KV-cache budget supports
   2-3x more conversational tokens than BPE-tokenized equivalents.

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

### D8 — Tier 0 reserved slots stay reserved for System 2

The 6 reserved Tier 0 slots (`a-f`) remain reserved for System 2 process
stages (`RESERVED_STAGE_*`) as originally specced. They are NOT consumed
by phrase atoms, LLM special tokens, or any other v0.3 work.

High-frequency phrase atoms enter Tier 1 / 2 / 3 by frequency ranking
under D6 — no special Tier 0 reservation is required.

### D9 — LLM special tokens live in a separate auxiliary namespace

LLM training requires bookkeeping tokens (`<PAD>`, `<BOS>`, `<EOS>`,
`<SEP>`, `<MASK>`, etc.). These are model-side training infrastructure,
not natural-language content, and they have **no presence in the .elo
file dictionary**.

The integer ID layout for LLM use is standardized by the **Dictionary
Profiles** system (see `docs/v1/profiles.md`). Five named profiles
ship with v1: Tiny (32k), Compact (64k), Standard (128k), Full (256k),
and Reference (the entire dictionary). Each profile is a strict
frequency-ranked subset; users can retrain at any profile.

When a model is trained against the v1 vocabulary, the trainer maps:

```
LLM integer ID range          Maps to
─────────────────────         ──────────────────────────────────
[0, V-1]                      Dictionary IDs (Tier 0-3 from .elo dict)
[V, V+255]                    256-byte fallback table
[V+256, V+256+K]              K special tokens (PAD/BOS/EOS/SEP/MASK/...)
```

Where `V` is the total dictionary entry count at v1 freeze.

The byte fallback table is published once as part of the v1 vocabulary
artifact. The special-token list is a separate publishable spec for
LLM consumers; the exact set of special tokens is deferred to the
LLM training stage (post-v0.3) since it depends on the model
architecture chosen.

`.elo` files never contain special tokens. Special tokens never enter
the dictionary. The two namespaces share an integer ID space only at
LLM tensor construction time.

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
A6  Vocabulary frozen: token-ids.csv.gz exported and committed at
    data/token-ids.csv.gz
A7  Byte fallback table defined and committed at
    data/byte-fallback-v1.csv  (256 fixed integer IDs for bytes 0x00-0xFF)
A8  Mining excludes the 3 measurement transcripts; held-out ratio
    measurement is honest, not memorized
A9  v0.2 .elo files still decodable via the v0.3 reader (backwards compat)
A10 ELO_FILE_FORMAT.md updated to v1.0 magic + flags layout
A11 Top 6 phrases promoted naturally into Tier 1 via the unified
    frequency ranking; Tier 0 a-f slots remain reserved for System 2
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

## Resolved Questions

```
Q1  Corpus + held-out split.
    RESOLVED: Mine on the full 14,806-file corpus EXCEPT the 3
    measurement transcripts (KhyQCU6oqE8.json, KOhbGjmidgs.json,
    bRwFb8JmznE.json). Excluding 3 files of 14,806 has no measurable
    effect on the vocabulary but keeps the benchmark numbers honest.

Q2  Publication strategy for the frozen vocabulary.
    RESOLVED: Commit to the repo at data/token-ids.csv.gz (gzipped,
    ~3 MB). Plain CSV would be ~18 MB; gzipped travels better in
    git history and stays directly inspectable via standard tools.

Q3  MIME type registration with IANA.
    DEFERRED: After proof-of-concept, validation, testing. Not in
    v0.3 scope.

Q4  C/C++ port target version.
    DEFERRED: C/C++ optimization happens in a stage after v0.3.
    The port will target the v0.3 frozen vocabulary (the production
    contract), not the v0.2 intermediate.

Q5  Special tokens reserved magic-byte range.
    RESOLVED VIA D9: Special tokens live in the LLM auxiliary
    namespace, not the .elo file dictionary. There is nothing for
    the encoder/decoder to handle — special tokens never appear in
    .elo files. No reserved magic-byte range needed.
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
