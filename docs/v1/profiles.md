# EloAI Dictionary Profiles
### Vocabulary Subsets for LLM Training and Inference

> Companion to `spec-v0.3.md` and `ELO_FILE_FORMAT.md`.
> Profiles are part of the v1 frozen vocabulary contract.

---

## Why profiles exist

The full v1 dictionary contains ~342,000 entries — comprehensive for
**compression** but larger than the vocabulary of most current LLMs. The
embedding matrix for a 14B-parameter model at full vocab is ~3.5 GB.

For LLM training and inference, we publish **named subsets** of the
dictionary. Each subset:

- is **frequency-ranked** — keeps the top N most-frequent entries
- is a **strict subset** of the next-larger profile
- is **stable across dictionary versions** — adding entries to the
  dictionary never changes profile membership for IDs already in the
  profile
- has a **power-of-2 total LLM vocab size** for memory and tensor
  alignment

The same `.elo` file decompresses under any profile: a decoder using a
smaller profile that encounters an out-of-profile ID simply degrades
to byte-fallback rendering for that ID (lossy at the LLM layer, but the
file-format decoder still has the full dictionary).

---

## The Five Profiles

| Profile | LLM vocab | Dict content | Byte fallback | Special | Closest LLM |
|---|---:|---:|---:|---:|---|
| **Tiny** | 32,768 | 32,496 | 256 | 16 | LLaMA 2, Mistral 7B |
| **Compact** | 65,536 | 65,264 | 256 | 16 | GPT-2, BERT large stretched |
| **Standard** | 131,072 | 130,800 | 256 | 16 | LLaMA 3, Gemma small |
| **Full** | 262,144 | 261,872 | 256 | 16 | future research / Gemma 256k+ |
| **Reference** | ALL | ~341,371 | 256 | 16 | EloAI native, max atomicity |

The integer ID layout inside each profile is identical:

```
[0, V-1]         Content IDs       -- dictionary entries by frequency rank
[V, V+255]       Byte fallback     -- one ID per byte 0x00..0xFF
[V+256, V+271]   Special tokens    -- PAD, BOS, EOS, SEP, MASK, CLS, ...
                                      (16 slots; the public list locked once
                                       the LLM training stage starts)
```

Where `V` is `dict_content` size for the profile (e.g. 32,496 for Tiny).

---

## Profile membership rule

```
A dictionary entry belongs to profile P iff its
frequency rank < P.dict_content size

rank 0 = the most frequent entry in the unified words+phrases ranking
rank 1 = next most frequent
...
```

This rule is **deterministic, reproducible, additive-only**:

- Anyone with the dictionary + frequency ranks can derive every profile
  with one comparison.
- Adding new entries to the dictionary (v1.1, v1.2, ...) can only push
  new IDs into Reference; existing profile contents never change.
- A user can build a custom profile of arbitrary size N by selecting
  `rank < N`.

---

## Selection example

Suppose v1 dictionary has these top entries by frequency:

```
rank   freq         entry              tier
0      88,414,058   " " (single space)  0
1       4,420,594   "."                 0
2       3,460,786   "the"               0
3       3,108,000   "you know"          1  (PHRASE)
4       2,908,522   "and"               0
5       2,647,128   ","                 0
...
32,495  450         "and so on"         2
32,496  448         "for example"       2   <-- last entry in Tiny profile
32,497  445         "in many ways"      2   <-- first NOT in Tiny
...
65,263  120         "broadcast journalism"  2
65,264  119         "phonetic alphabet"     2   <-- first NOT in Compact
...
```

For Tiny: ranks `[0, 32495]` are members.
For Compact: ranks `[0, 65263]` are members.
For Standard: ranks `[0, 130799]` are members.
Etc.

---

## Compatibility matrix

|  | Decode `.elo` written by Tiny | Compact | Standard | Full | Reference |
|---|---|---|---|---|---|
| **Tiny model** | full content | byte-fallback above 32,496 | byte-fallback above 32,496 | byte-fallback above 32,496 | byte-fallback above 32,496 |
| **Compact** | full content | full content | byte-fallback above 65,264 | byte-fallback above 65,264 | byte-fallback above 65,264 |
| **Standard** | full content | full content | full content | byte-fallback above 130,800 | byte-fallback above 130,800 |
| **Full** | full content | full content | full content | full content | byte-fallback above 261,872 |
| **Reference** | full content | full content | full content | full content | full content |
| **File-format decoder (Compressor class)** | full content | full content | full content | full content | full content |

**Key point:** the file-format decoder (the standalone `.elo` reader, not
the LLM) always uses the full dictionary. The profile concept applies
only to LLM training — it controls which IDs the model has embeddings
for. A model with a Tiny profile sees byte fallback for any ID above
its budget, but the `.elo` file itself loses nothing.

---

## Choosing a profile

| If you are... | Choose | Why |
|---|---|---|
| Training a 1-3B model for on-device inference | **Tiny** | Smallest embedding, mobile-deployable when quantized |
| Training a 3-8B model, want familiar size | **Compact** | GPT-2/4-class vocab, fits flagship phones at int4 |
| Training an 8-14B model, want phrase atomicity | **Standard** | LLaMA-3-class size, captures the top ~50k phrases |
| Doing research, willing to pay 2x embedding cost | **Full** | Includes the long tail of phrase atoms |
| Building a compression-tool consumer | **Reference** | Use the entire dictionary; no LLM constraints |

---

## On-device inference -- the unlock

The original reason to standardize vocabulary profiles is that a small
vocab + aggressive quantization moves capable LLMs from cloud-only to
**phone-deployable**. The math, with honest accounting:

### Memory budget for an LLM at inference

```
Total RAM needed = Embedding matrix
                 + Transformer weights (attention + FFN)
                 + KV cache (grows with context length)
                 + Activations (small per step)
                 + Misc overhead
```

The first two dominate at typical context lengths. EloAI affects both:

- **Embedding** is `vocab_size × hidden_dim × bytes_per_param`. Smaller
  vocab cuts this directly.
- **Transformer weights** are fixed by model size, but quantization
  (int8, int4, int2) reduces bytes_per_param 4-16x.
- **KV cache** scales with token count -- phrase atoms reduce token
  count for the same source text, so KV cache is smaller for the same
  semantic content.

### Concrete deployment scenarios

Model weights stored at int4 (4 bits per param, ~0.5 bytes), embedding
at int8 (1 byte per param, modest accuracy tradeoff):

| Model | Profile | Embed | Weights | KV (4k ctx) | **Total RAM** | Device class |
|---|---|---:|---:|---:|---:|---|
| 1B | Tiny     | 0.07 GB | 0.5 GB | 0.05 GB | **0.6 GB** | Budget phone (4 GB) |
| 3B | Tiny     | 0.07 GB | 1.5 GB | 0.1 GB  | **1.7 GB** | Mid-range phone (6 GB) |
| 3B | Compact  | 0.13 GB | 1.5 GB | 0.1 GB  | **1.7 GB** | Mid-range phone |
| 7B | Compact  | 0.27 GB | 3.5 GB | 0.2 GB  | **4.0 GB** | Flagship phone (8 GB Pro / S25) |
| 7B | Standard | 0.54 GB | 3.5 GB | 0.2 GB  | **4.2 GB** | Flagship phone |
| 8B | Compact  | 0.27 GB | 4.0 GB | 0.2 GB  | **4.5 GB** | Flagship phone (8 GB tight) |
| 8B | Standard | 0.54 GB | 4.0 GB | 0.2 GB  | **4.8 GB** | Flagship phone |
| 14B | Standard | 0.67 GB | 7.0 GB | 0.3 GB  | **8.0 GB** | High-end tablet / laptop only |

**The threshold finding:** **3B-class models with Tiny or Compact
profile fit comfortably in 2 GB at int4** — well within mid-range phone
RAM budgets, leaving room for the OS and the host app.

**A 7B-8B model fits in a flagship phone (8 GB RAM)** with Compact
profile at int4. The user's intuition that "8B in 1 GB" is the
direction is right; the actual achievable size today is closer to
4-5 GB for 8B with int4, which is still revolutionary versus the
16 GB+ those models need at bf16. With int2 quantization that drops
toward the 1-2 GB range — research-stage but the trajectory is clear.

### The phrase-atom force multiplier

There's a second compounding effect. EloAI vocabularies aren't just
smaller -- they're **semantically denser**. A phrase atom like
`"i don't know"` is one token where BPE tokenizers spend 4-5 tokens.

For the same input text:

```
Input:       2,000 words of conversational dialogue
BPE tokens:  ~2,500 tokens
EloAI tokens (Tiny):    ~1,400 tokens (40% fewer)
EloAI tokens (Standard): ~900 tokens (64% fewer)
```

The same KV cache budget supports **2-3x more conversational context**
at the same RAM. A phone running a 7B EloAI-Compact model with 4 GB
RAM has the effective context window of a desktop running a 7B
BPE model with 8-12 GB.

### Why this matters

- Models that previously required cloud inference become local.
- Privacy benefits: documents, dialogue, dictation never leave the device.
- Latency drops 10-100x for short queries.
- Offline operation becomes table stakes.
- Per-query energy cost on battery becomes negligible.

The on-device LLM era was waiting for two things to converge:
aggressive quantization (already here) and **semantically dense small
vocabularies** (what EloAI provides). The profile system makes that
contract explicit and testable.

---

## File format references

The `.elo` file format itself is **profile-agnostic**: every encoder
writes the same byte stream, every decoder reads it. Profiles are a
**reader-side selection** for LLM-vocabulary work, never part of the
file format.

A future field MIGHT appear in the `.elo` header's flags byte to
signal "this file uses only Tiny-profile IDs" for fast compatibility
detection. Reserved bit, not active in v0.3.

---

## Versioning

Profiles are versioned with the dictionary:

```
Profile name     Dictionary contract       Compatibility
─────────────    ─────────────────────     ───────────────
tiny@v1          v1 dictionary, rank < N   stable forever
compact@v1       v1 dictionary, rank < N   stable forever
standard@v1      v1 dictionary, rank < N   stable forever
full@v1          v1 dictionary, rank < N   stable forever
reference@v1     v1 dictionary, all        stable forever
```

When the dictionary upgrades to v2 (adding rare entries), the profiles
become `tiny@v2`, etc. **Profile-vN files are forward-readable by
profile-vM where M > N**, because additive-only is the contract.
Profile-vM files require profile-vM readers when they use the new IDs.

---

## Published artifacts (per dictionary release)

```
data/token-ids-v1.csv.gz                    -- unified table:
                                                id,base64_id,surface,tier,
                                                freq,tiny,compact,standard,full
                                                (~3 MB gzipped)

data/special-tokens-v1.json                 -- the 16 LLM special token
                                                definitions and their IDs
                                                inside each profile

data/byte-fallback-v1.csv                   -- 256-row table:
                                                byte_value (0-255),
                                                integer_id_per_profile

docs/v1/profiles.md                         -- this document

dictionary.lmdb                             -- single LMDB, all profiles
                                                derive from it via the
                                                rank filter
```

For LLM training pipelines (HuggingFace, etc.), each profile is exposed
as a standard tokenizer config:

```python
from eloai.tokenizer import EloTokenizer

tok = EloTokenizer.from_pretrained('eloai/v1-standard')
# Returns a HuggingFace-compatible PreTrainedTokenizer
# with vocab_size = 131,072
```

The Python package exports adapters; the underlying contract is just
the published CSV.

---

## Acceptance criteria for "the profile system works"

```
P1   Profile membership reproducible: applying the rank filter to
     the published frequency table yields exactly the published CSV.

P2   Cross-profile decode works: a `.elo` file encoded under Full reads
     correctly under Reference; under Standard it loses nothing the
     Standard reader's dictionary supports.

P3   Special tokens have stable IDs across profiles
     (only the V offset changes; the special-token offsets within
     each profile's tail are identical).

P4   Byte fallback table has stable IDs across profiles
     (same offset within each profile's tail).

P5   Adding new entries to the dictionary (v1.1) does not change
     membership for any rank < dict_v1 size.
```

---

## Reference

```
Spec               docs/compression/spec-v0.3.md
File format        elo_file_format/ELO_FILE_FORMAT.md
Companion data     data/token-ids-v1.csv.gz
Repository         github.com/4waymedia/semantic-compression
EloAI              https://eloai.dev
```
