# Dictionary & Compression Handbook

> A plain-language guide to how the ELO dictionary turns text into compact,
> semantically-rich IDs — what the IDs mean, **how to read a build's size and
> numbers**, and **how to choose/adjust a build for your use case**. No deep code
> needed. *File paths below are relative to the `semantic_compression/` root.*
> Deeper specs: `docs/compression/spec-v0.4.md`, `spec-vocab-strategy.md`,
> `spec-dict-testgroups.md`, `GUIDE-create-dictionary.md`, `SYSTEM1.md`.

---

## 1. The idea in one picture

```
raw text ──► [dictionary lookup] ──► ID stream ──► .elo / .eloB file
   "because"        every word/phrase            short Base64 IDs +
                    has a canonical ID            byte-fallback for the rest
```

The dictionary is a **canonical map: surface → Base64 ID**. Compression replaces
each word/phrase with its ID; decompression reverses it. The win is twofold: the
stream is **smaller** (v0.3 ≈ 1.99× on transcripts) *and* every ID already carries
meaning (facets/EPA), so the compressed form is machine-operable, not just zipped.

---

## 2. The Base64 ID and the tiers

IDs use a **64-character URL-safe alphabet** (`A–Z a–z 0–9 - _`). An ID's **length
tells you its tier** — no lookup needed:

| Tier | ID width | What lives here | Capacity |
|---|---|---|---|
| **Tier 0** | 1 char | ~64 **primitives** — EPA poles, filler classes, process stages, logic/structural atoms | 64 slots |
| **Tier 1** | 2 char | the most frequent / highest-value words | 1,280 |
| **Tier 2** | 3 char | mid-frequency words | 81,920 |
| **Tier 3** | 4 char | low-frequency + high-meaning words | ~5,242,880 |
| **Phrases** | 4 char (`-` prefix) | multi-word units / collocations / pragmatic formulas | separate partition |

The **first character encodes the tier**, so a reader knows an ID's tier instantly.
Reserved slots (Tier-0 primitives, ~1,024 single-word slots in Tier 1) are honored
at every build — never reassigned.

---

## 3. Sizes = build depth (the scale-by-use-case dial)

A dictionary's **size is how deep into the tiers it is built**. You pick the size
for the target:

| Size | Tiers built | ~Capacity | Use case |
|---|---|---:|---|
| **char-2** | 0 + 1 | ~1,333 | tiny: app / keyboard / edge |
| **char-3** | + 2 | ~83,253 | medium: on-device, mid apps |
| **char-4** | + 3 | up to ~5.25M | large: LLM / max atomicity |

> **There is no universal "full dictionary."** "Full" is whatever a build's
> decisions (size + corpus + vocabulary selection) produced — *each build is its own
> dictionary*. (Don't confuse **sizes** with **LLM profiles**: a profile is a
> frequency-rank cut of one dictionary for a model's embedding budget; a size is the
> physical tier depth of the built artifact. A char-N build can still be
> profile-cut.)

---

## 4. The lossless guarantee (and byte-fallback)

The codec is **byte-exact at every size.** Anything not in the dictionary (an
out-of-vocabulary surface, at *that* build depth) is encoded with **byte-fallback** —
its raw bytes are carried through and reconstructed exactly. So `decode(encode(x)) == x`
holds regardless of how small the dictionary is (`verify_lossless.py`). "Lossy"
only ever refers to the *LLM embedding layer* (a model with no row for an ID) —
**never** the codec.

---

## 5. Identity & pairing (read before you share a file)

Every build is **fingerprinted**, and an `.elo` file **must be decoded with the same
dictionary it was encoded under** — identified by fingerprint. This is the same
pairing rule as the dictionary↔model lock: mix a stream with the wrong dictionary
and IDs resolve to the wrong words. The build records its corpus, facet, meta, and
EPA fingerprints so an artifact always declares exactly what produced it.

---

## 6. Choosing / adjusting a build (cookbook)

**"Which size do I build?"** → match the target: edge/app → char-2; on-device →
char-3; LLM/max detail → char-4. Each size is built and scored independently.

**"How do I pick the *strongest* char-2/char-3?"** → the **test-group harness**
(`spec-dict-testgroups.md`) builds several candidates under different selection
strategies and scores them on a held-out set. Compare **frequency** (S0, the
baseline) vs **bytes-saved** (S1, value-not-frequency) and pick the winner —
adoption is a deliberate step, never automatic.

**"What goes in the scarce short-ID slots?"** → that's the *vocabulary selection*
question (`spec-vocab-strategy.md`): maximize the value packed into Tiers 1–2
(bytes saved) without hurting compression ratio or OOV rate.

**"How do I build one end-to-end?"** → `GUIDE-create-dictionary.md`.

**Guardrails that must always hold:** round-trip byte-exact (G1), tier capacity
respected (G2), deterministic fingerprint (G3).

---

## 7. Cheat sheet

```
ALPHABET   A–Z a–z 0–9 - _   (64, URL-safe)
TIER       by ID length: 1=primitive · 2=Tier1 · 3=Tier2 · 4=Tier3/phrase
SIZE       char-2 (edge) · char-3 (on-device) · char-4 (LLM)   = build depth
LOSSLESS   byte-exact at every size; OOV -> byte-fallback (exact)
IDENTITY   fingerprinted; decode with the SAME dictionary it was encoded under
PICK       size = use case ; strongest build = test-group harness (freq vs bytes-saved)
METRICS    compression ratio · char-2/3 yield · OOV rate   (on a held-out set)
```

---

## 8. Pointers

*Paths relative to `semantic_compression/`.*
```
Overview        SYSTEM1.md ; PROCESS.md (build steps)
Versions        docs/compression/spec-v0.3.md ; spec-v0.4.md
Vocabulary      docs/compression/spec-vocab-strategy.md
Build chooser   docs/compression/spec-dict-testgroups.md (test-group harness)
Create guide    docs/compression/GUIDE-create-dictionary.md
Corpus          docs/compression/spec-corpus-sourcing.md
Identity        docs/compression/spec-artifact-identity.md
Round-trip      verify_lossless.py
Meaning layer   ../handbook/facets-epa/HANDBOOK.md (facets/meta/EPA on each ID)
```
