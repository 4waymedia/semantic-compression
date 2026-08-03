# Tier System — Spec

> **The single normative source for tiers, ID assignment, and tier capacity.**
> Every other document defers to this one. If a doc restates a tier table and
> disagrees with this file, this file is right and that doc is stale — report it.
>
> | | |
> |---|---|
> | **Spec version** | **2** (spec-1 was the first-character scheme; see §7) |
> | Status | **normative** |
> | Written | 2026-08-02 |
> | Verified against | `config.py`, `dictionary_builder.py`, `dictionary_builder_v03.py`, `compressor.py`, `elo.rs`, `verify_config.py`, and all 437,995 ids of `elo-browser-v01c` |
> | Related | [`DICTIONARY_SLOTS_SPEC.md`](DICTIONARY_SLOTS_SPEC.md) (which surface lands where), `ELO_CODE_BAND_SPEC.md` (reserved bands), [`../format/ELO_FILE_FORMAT.md`](../format/ELO_FILE_FORMAT.md) (the wire) |

---

## 1. The rule

**Tier is determined by ID LENGTH. The first character carries no tier information.**

```
len 1  ->  Tier 0        len 3  ->  Tier 2
len 2  ->  Tier 1        len 4  ->  Tier 3
leading '-'  ->  Tier 4  (phrase namespace — reserved, see §5)
```

That is the whole rule. It is implemented three times, independently, and all
three agree:

| runtime | implementation | file |
|---|---|---|
| Python | `detect_tier()` — `len()`, plus the `'-'` test | `config.py` |
| Rust | `tier_of()` — `id.chars().count()` | `elo.rs` |
| `.eloB` wire | tier in the top 2 bits of the tag byte; first char as a **6-bit index over all 64 charset bytes** (`tag & 0x3F`) | `compressor.py` |

The wire format has *always* carried the leading character at full 6-bit width.
No tier change requires a `FORMAT_VERSION` bump.

---

## 2. Tier 0 — the 64 single-character slots

Tier 0 is **fully allocated. There are no free slots.** The 64 characters
partition exactly three ways, and `verify_config.py` asserts the total:

| class | count | characters | in the dictionary? |
|---|---:|---|---|
| `WORD_IDS` | 26 | `A`–`Z` | yes — the 26 highest-frequency words (`T` = `the`) |
| `STRUCTURAL_IDS` | 27 | `3`–`9`, `g`–`z` | yes — space, newline, tab, punctuation |
| `SYSTEM_IDS` | 5 | `-` `0` `1` `2` `_` | **no** — stream control, never a surface |
| `RESERVED_IDS` | 6 | `a`–`f` | **no** — held for the six process stages |
| | **64** | | **53 dictionary entries** |

Two counts circulate and both are correct; they measure different things:

- **`len(PRIMITIVES) == 58`** — every character with a defined meaning
  (26 word + 27 structural + 5 system).
- **53 Tier-0 rows** in a built dictionary — the 58 minus the 5 system markers,
  which are format control and never surfaces.

The six reserved are **named holds**, not spare capacity:
`RESERVED_STAGE_{PERCEPTION, NOVELTY, GOAL_PLAN, ACTION, PROGRESS, RESULT}`.

> **Consequence for any reservation proposal:** Tier 0 cannot host one. A
> reserved band — for a code ontology or anything else — must live in Tier 1.
> See `ELO_CODE_BAND_SPEC.md`.

---

## 3. Tiers 1–3 — capacity is a function of the build's alphabet

A Tier *n* id is **n+1 characters**: the leading one drawn from the build's
leading-character alphabet, the remaining *n* from all 64.

```
capacity(n) = len(first_chars) x 64^n          # NOT 64^(n-1)
```

`config.tier_capacity_for(first_chars)` is the one implementation. Two alphabets
are defined:

| alphabet | constant | size | T1 | T2 | T3 |
|---|---|---:|---:|---:|---:|
| legacy | `TIER_WORD_FIRST_CHARS` = `g`–`z` | 20 | 1,280 | 81,920 | 5,242,880 |
| expanded | `TIER_FIRST_CHARS_EXPANDED` = all but `-` | 63 | **4,032** | **258,048** | **16,515,072** |

A build opts in with `expand_tiers: true` in its spec. The legacy alphabet
remains the default so v0.2 (`dictionary_builder.py`) and builds v01a/v01b stay
byte-reproducible.

**4,032 is 98.4% of the theoretical maximum.** The absolute ceiling is
64 × 64 = 4,096; the missing 64 are the `-` prefix held for Tier 4. **There is
no further expansion available in this scheme** — the trailing alphabet is
already all 64. Future gains come from *allocation* (which surfaces get short
ids), not capacity.

---

## 4. Measured effect of the expansion

`elo-browser-v01c` is `v01b` with `expand_tiers` and **nothing else** changed —
same corpus, same ranking, same phrase inventory, same 437,995 entries.

| tier | id len | v01b used | v01c used |
|---|:--:|---:|---:|
| 0 | 1 | 53 | 53 |
| 1 | 2 | 1,280 | **4,032** |
| 2 | 3 | 81,920 | **258,048** |
| 3 | 4 | 354,742 | **175,862** |

- **178,880 entries promoted out of Tier 3.**
- Share reachable in ≤3 characters: **19.0% → 59.8%**.
- Both builds fill Tiers 1 and 2 *completely* — the expansion lengthened the
  shelf, it did not create slack.

**`.eloB` stream size — measured 2026-08-02** by encoding the same bytes against
each build's own LMDB (`Compressor.encode_bytes_binary`), the 11 `samples/`
files plus one book, 219,579 raw bytes:

| | v01b | v01c |
|---|---:|---:|
| encoded bytes | 130,551 | **124,773** |
| ratio | 1.682x | **1.760x** |

**−4.43% overall**, and smaller on **all 12 files** (range −1.64% `episode.vtt`
to −7.92% `server.log`; `thesis.txt` −5.51%, the book −4.25%). Reproduce with
§8's snippet.

> Two other figures are in circulation and should not be quoted as this one.
> The v01c build spec predicted **2.7–4.0%** — that was a *cost model* over id
> lengths on captured pages (cnn/tomshardware/google-search), not encoder
> output. A **3.8%** figure also circulated with no committed artifact behind
> it; it is `UNVERIFIED` and superseded by the table above. The captured-page
> measurement has not been re-run since the pages are not committed.

- **Browser page capture: no effect.** `Dict::encode` on the `browse_url` path
  writes `varint(index+1)`, not the Base64 id, so that path is tier-blind by
  construction. Do not expect a page-compression gain from a tier change; this
  is why elo-v2 and elo-v3 read near-identical in the browser comparison.

Fingerprints: v01a `b1790799882521e5`, v01b `495cd5355ec374ce`,
v01c `bdec07bf404aa5d7`.

---

## 5. Tier 4 — the phrase namespace

`detect_tier` returns 4 for any id starting with `-`. **No build emits a
`-`-prefixed id.** The namespace is reserved and currently unused — phrases are
minted into Tiers 1–3 alongside words, from the same ranking.

`-` is excluded from `TIER_FIRST_CHARS_EXPANDED` for exactly this reason, which
is why the expanded alphabet is 63 and not 64. Reclaiming it would buy 1.6%
capacity and cost the phrase namespace. Not recommended.

---

## 6. IDs are not stable across builds

**Only Tier 0 is positionally assigned** — `tier0_records` iterates a
hand-authored map, so `the` → `T` in every build. Tiers 1–3 are counter outputs
over the ranked list, so **an id is a function of both rank and alphabet**.
Changing either re-mints everything:

| surface | v01b | v01c |
|---|---|---|
| `\|` | `gA` | `AA` |
| `_` | `iV` | `CV` |
| `car` | `ghj` (T2) | `1j` (T1) |

> **Downstream rule:** treat S1 ids as **provisional** until the char-4
> stabilization build. Bind to surfaces and re-map per build; never persist a
> raw S1 id as a long-lived reference. Any scheme needing a stable id must be
> carved *before* the counter starts — see `ELO_CODE_BAND_SPEC.md`.

---

## 7. What spec-1 said, and why it is gone

**spec-1: the first character encoded the tier.** Each tier owned a disjoint
range (`0`–`9` → Tier 1, `A`–`Z` → Tier 2, `a`–`z` → Tier 3, `-` → Tier 4), so
tier could be read without a length check or a DB lookup.

That design was replaced by length. `TIER_WORD_FIRST_CHARS = 'g'–'z'` is its
**residue** — it is exactly `[a-z]` minus the six stage-reserved Tier-0 ids
`a`–`f`, which is why it is 20 characters and not a round number. It was never
a chosen alphabet.

The restriction outlived its reason by an unknown number of builds and cost
**69% of every tier's capacity**. Two things hid it:

1. **`detect_tier` still tested the first character** before using length, so
   the constant looked load-bearing. It was called from nowhere but
   `verify_config.py`.
2. **`verify_config.py` asserted the restriction's *output*, not its
   necessity.** Every multi-character sample in its tier-detection set began
   with `g` or `-`, so the suite could — and did — pass while `detect_tier`
   raised `ValueError` on every id in the shipped v01c dictionary. Section 11
   pinned `TIER_CAPACITY[1] == 1280`, making a capacity change look like a
   config violation.

Both were corrected 2026-08-02. The suite now probes all 63 leading characters
and asserts capacity against **both** the relationship and known-good literals —
the relationship alone is a tautology, and on the day of the fix that tautology
passed a `tier_capacity_for()` written with the wrong exponent.

> **Doctrine.** An assertion over a value the code itself constrains is a
> change-detector, not a correctness proof. Test the property you need, with at
> least one case the current implementation would have to be wrong to pass.

---

## 8. Verifying

```powershell
cd F:\Script-Projects\elo-dev\elo_dev\R-D-concepts
$env:PYTHONPATH="."
python semantic_compression\verify_config.py
```

Expected:

```
[OK] Tier detection: length decides; all 63 non-"-" leading chars accepted
[OK] Tier capacity legacy  (20 chars): T1=1,280  T2=81,920  T3=5,242,880
[OK] Tier capacity expanded(63 chars): T1=4,032  T2=258,048  T3=16,515,072
```

To check a built dictionary end-to-end, every id must round-trip its recorded
tier — this is the check that would have caught the drift:

```python
from semantic_compression.config import detect_tier
# for each row of db/builds/<build>/token-ids.csv.gz:
assert detect_tier(row['base64_id']) == int(row['tier'])
```

Run on v01c 2026-08-02: **437,995 ids checked, 0 mismatches.** Before the fix,
every one of them raised `ValueError`.

To reproduce §4's stream measurement:

```python
from semantic_compression.compressor import Compressor
c = {b: Compressor(lmdb_path=f'semantic_compression/db/builds/{b}/dictionary.lmdb')
     for b in ('elo-browser-v01b', 'elo-browser-v01c')}
raw = open('semantic_compression/samples/page.html','rb').read()
{b: len(v.encode_bytes_binary(raw, 'html')) for b, v in c.items()}
```

---

## 9. Change log

| spec | date | change |
|---|---|---|
| 1 | — | First character encodes tier; disjoint range per tier. **Superseded.** |
| 2 | 2026-08-02 | Length decides. Capacity derived from the build's alphabet; `expand_tiers` opt-in (63 chars). `detect_tier` first-char test removed. Tier-0 partition documented as fully allocated. |
