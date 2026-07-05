# Reading Compressed Output — Handbook

> The companion to the annotation handbook. That one explains the *meaning* layer
> (facets/EPA); this one explains the *compression* layer: what you're looking at
> when you see an `.elo`/`.eloB` stream or a dictionary entry, and how to read the
> stats. Plain-language, no deep code. Specs: `spec-facets-db.md`, `SYSTEM1.md`;
> code: `config.py`, `caps_codec.py`, `compressor.py`.

---

## 1. The idea in one line

Every word and phrase maps to a short **Base64 ID**. The stream of IDs *is* the
compressed text — and because each ID is also annotated (facets/EPA), you can
search and reason in ID space without decompressing. **It is byte-exact:** decoding
reproduces the original input exactly, character for character.

---

## 2. The ID alphabet & tiers (the most important thing to know)

IDs are written in a 64-symbol URL-safe alphabet: `A–Z a–z 0–9 - _`.

**An ID's length tells you its tier** — and the tier tells you how common the word
is. This is the quick read:

| Length | Tier | What lives here | Capacity |
|---|---|---|---|
| **1 char** | Tier 0 | system/structural **primitives** (sentence boundary, etc.) | ~58 |
| **2 chars** | Tier 1 | the most common words/phrases | ~1,280 |
| **3 chars** | Tier 2 | mid-frequency words/phrases | ~81,920 |
| **4 chars** | Tier 3 | the long tail (rare words, low-freq phrases) | up to ~5.2M |

Word/phrase IDs (tiers 1–3) **start with a letter `g`–`z`**; the single-character
primitives use the rest of the alphabet. So `T` is a 1-char primitive, `gB` is a
Tier-1 entry, `hxQ` is Tier-2, `mPq4` is Tier-3.

**Why tiers:** common things get the shortest IDs (2 bytes), rare things get longer
ones (4 bytes). That length-by-frequency trade *is* the compression. A dictionary's
**size** (`char-2` / `char-3` / `char-4`) is simply how deep into these tiers it was
built.

---

## 3. The stream formats

- **`.elo` (text — readable, use this to inspect):**
  ```
  ELO|1|.txt|<token>|<token>|<token>...
  ```
  `ELO` magic · format version · the source file's extension · then the tokens,
  each separated by a `|` (pipe).
- **`.eloB` (binary — compact, for storage/wire):** same information packed into
  bytes (`ELO` magic + a version byte + extension + a binary token stream). Not
  human-readable — decode it or look at the `.elo` text form to read it.

---

## 4. The tokens you'll see (how to decode by eye)

A token in the stream is one of:

- **A plain ID** — e.g. `T`, `gB`, `hxQ`. A word/phrase found in the dictionary.
  Read its length for the tier (§2).
- **A capitalized in-vocab word** — `g:Xy`. The part before the `:` is a
  **capitalization mask** (6 bits per character, Base64-encoded); the part after is
  the normal ID. Lowercase words have no prefix. (`A` as a cap char = all-lowercase /
  no change.)
- **An OOV (out-of-vocabulary) word** — `OOV:g:hello`. The word was **not in this
  dictionary**, so it's stored as `OOV` : caps-mask : the lowercased letters. This is
  the **byte-fallback** path — the word is preserved exactly, just not compressed.
  *Seeing lots of `OOV:` means the dictionary doesn't cover this text well.*
- **A primitive** — a single char standing for a structural/system atom (e.g. a
  sentence boundary).
- **Whitespace and punctuation** are tokens too, preserved exactly — that's how the
  round-trip stays byte-exact (a space, a newline, a comma each have their slot).

Worked mini-example: `"Hello world"` where `world` is in-vocab but `Hello` isn't →
roughly `OOV:g:hello | <space-token> | <id-for-world>`.

---

## 5. Phrases — one ID for several words

Common multi-word units (`at the end of the day`, `you know`) get a **single ID**.
The encoder scans **longest-match-first**, so it grabs the biggest known phrase
before falling back to individual words. So one token in the stream can stand for a
whole phrase — which is why phrase coverage matters so much for the ratio.

---

## 6. Reading the stats

When you see a build's `dict_stats` / efficiency / `epa_stats` numbers:

- **`ratio = raw_bytes / encoded_bytes`.** `>1` = it compressed; `=1` = no gain;
  **`<1` = it EXPANDED.** Expansion is real and happens at **high OOV** — each
  fallback token costs more than the original bytes, so once ~25%+ of tokens are
  OOV (e.g. literary text against a transcript dictionary) the "compressed" file is
  *bigger*. The fix is coverage, not the codec.
- **`OOV%`** — the fraction of tokens with no dictionary ID. This is the leading
  indicator: high OOV → poor coverage → worse ratio.
- **tier occupancy** — how many entries sit at each tier (Tier-1 ≤ 1,280, etc.).
- **round-trip byte-exact** — `decode(encode(x)) == x`. This must always pass.

---

## 7. "Byte-exact" vs "lossy" (a common confusion)

- The **codec is always lossless** — byte-exact at every size. OOV bytes are
  reconstructed exactly via fallback, so nothing is ever lost in the file.
- **"Lossy" only ever refers to the LLM layer** — a model that has no embedding row
  for a rare ID can't *interpret* it. That's a model-vocabulary limitation, **never**
  the file format.
- An `.elo` must be decoded with the **same dictionary it was encoded under**,
  identified by fingerprint (the dictionary↔stream pairing rule).

---

## 8. It's not just smaller — it's operable

Because every ID also carries facets and (where rated) EPA, a compressed token is
machine-readable *as-is*: you can filter fillers, find causal statements, or read a
token's affect **without decompressing**. See the annotation handbook
(`HANDBOOK-facets-epa.md`) for that layer.

---

## 9. Cheat sheet

```
ID LENGTH -> TIER   1=primitive  2=Tier1(common)  3=Tier2  4=Tier3(rare)
WORD IDS start g..z ;  primitives are single chars
TOKENS    plain ID = in-dict word | cap:ID = capitalized | OOV:cap:lower = not in dict
          single char = primitive | spaces/punct = their own tokens (byte-exact)
PHRASES   one ID can = several words (longest-match scan)
FORMATS   .elo  = ELO|ver|ext|tokens(separated by |)      .eloB = binary
STATS     ratio>1 good, =1 none, <1 EXPANDED (high OOV) ; OOV% = coverage gap
EXACT     codec is always byte-exact ; "lossy" = LLM embedding only, never the file
PAIRING   decode with the SAME dictionary (by fingerprint) it was encoded under
```

---

## 10. Pointers
```
Charset / tiers   config.py (BASE64_CHARS, TIER_WORD_FIRST_CHARS, WORD_IDS, STRUCTURAL_IDS)
Case / OOV codec  caps_codec.py (encode_caps / encode_oov)
Encode / decode   compressor.py ; round-trip gate: verify_lossless.py
Sizes & building  GUIDE-create-dictionary.md ; SYSTEM1.md
Meaning layer     HANDBOOK-facets-epa.md
```
