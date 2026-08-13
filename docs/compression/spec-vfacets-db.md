# Vfacets Database + Bundle Channel — Specification (v1)

### A static reasoning-facet layer over the frozen dictionary: five dimensions, two bytes per entry, one wire, two homes.

> **Status: NORMATIVE.** This is the single source for the vfacets record, its
> enumerations, and its two on-disk forms. Any doc, reader, or exporter that
> restates the layout and disagrees with this file is stale — report it.
>
> | | |
> |---|---|
> | Spec version | **1** (2026-08-13) |
> | Record | 2 bytes, `<BB>`, id-keyed (sub-DB) and `n`-indexed (bundle) — identical packing |
> | Verified against | `vfacet_builder.py` (writer), `export_browser_assets.py` (bundle exporter), the shipped `elo-browser-v01c` bundle |
> | Producers | `vfacet_builder.py` (polarity/temporal/domain, deterministic) → `vfacet_llm.py` (agency/direction, 2-pass) |
> | Consumers | server: verbalizer `recall_seeds` (vfacet-aligned ranking); browser: `vfacets.bin` role/polarity per `n` |
> | Related | assignment HOW → [`GUIDE-vfacet-llm.md`](GUIDE-vfacet-llm.md); sibling facet layer → [`spec-facets-db.md`](spec-facets-db.md); EPA → [`HANDBOOK-facets-epa.md`](HANDBOOK-facets-epa.md); bundle → [`spec-publish-dictionary.md`](spec-publish-dictionary.md) |

---

## 1. What this is

`vfacets` is a **reasoning-facet annotation layer** over the frozen dictionary —
parallel to `facets` (lexical affordance) and `epa` (affect), and keyed the same
way. Where `facets` says *what kind of token this is* and `epa` says *how it
feels*, `vfacets` says *how it behaves in a reasoning chain*: who acts, which way
the movement points, when in a process it sits, what domain it belongs to, and
whether its charge is positive or negative.

Like `facets`/`epa`, it is computed **once, at build time**, as a fixed few bytes
per entry. A consumer reads a tag by index — `txn.get(id_bytes, db=vfacets)` on
the server, or `vfacets[n]` in the browser — with no model inference at read
time. It does **not** enter the `.elo` token stream; it is an annotation beside
the vocabulary, not part of the wire.

---

## 2. The record — 2 bytes, five dimensions

One record is **2 bytes**, packed little-endian (`struct '<BB'`), identical in
both homes. Byte 0 carries the three reasoning axes; byte 1 carries domain and
polarity. Two low bits of byte 1 are reserved (must be 0).

```
byte 0:  AAA... no ->  A A | D D D | T T T
           bit:        7 6   5 4 3   2 1 0
           A = agency (2b)   D = direction (3b)   T = temporal (3b)

byte 1:              M M M M | P P | . .
           bit:      7 6 5 4   3 2   1 0
           M = domain (4b)   P = polarity (2b)   . = reserved (0)
```

Shifts/masks (the one implementation, `vfacet_builder.py`):

```
AGENCY_SHIFT=6  MASK=0b11000000     DOMAIN_SHIFT=4   MASK=0xF0
DIRECTION_SHIFT=3 MASK=0b00111000   POLARITY_SHIFT=2 MASK=0b00001100
TEMPORAL_SHIFT=0  MASK=0b00000111
```

### The five enumerations (normative)

| dimension | bits | values (code → name) |
|---|:--:|---|
| **agency** | 2 | 0 UNKNOWN · 1 SELF · 2 OTHER · 3 SYSTEM |
| **direction** | 3 | 0 UNKNOWN · 1 TOWARD · 2 AWAY · 3 STABLE · 4 REVERSAL · 5 NEUTRAL |
| **temporal** | 3 | 0 UNKNOWN · 1 STATE · 2 PROCESS · 3 EVENT · 4 OUTCOME · 5 CONDITION |
| **domain** | 4 | 0 GENERAL · 1 CODING · 2 MEDICAL · 3 LEGAL · 4 FINANCE · 5 SCIENCE · 6 PSYCHOLOGY · 7 MILITARY · 8 CULINARY · 9 EDUCATION |
| **polarity** | 2 | 0 NEUTRAL · 1 POSITIVE · 2 NEGATIVE · 3 BIPOLAR |

`0` is `UNKNOWN`/`GENERAL`/`NEUTRAL` in every dimension — an all-zero record is
the well-defined "nothing asserted" value, and a missing entry is read as that.
Direction and temporal reserve codes 6–7; domain reserves A–F. New values append
into the reserved codes and are a spec-version bump here — never a silent
addition.

---

## 3. Home A — the `b'vfacets'` sub-DB (server)

Lives inside `dictionary.lmdb` as the named sub-DB `b'vfacets'`, keyed by
`id_bytes` (the same key as `forward`/`facets`), one 2-byte record per surface.
It is added to an existing dictionary by `vfacet_builder.py`; it is **not** part
of the base `forward`/`reverse` build.

Consumer: the verbalizer's `recall_seeds` opens `b'vfacets'` (via
`VerbalizerSubstrate`) and ranks candidate seeds by **vfacet alignment** to the
query intent. If the sub-DB is absent, `recall_seeds` cannot open it and the
verbalizer-backed recall path is unavailable — so a dictionary that ships without
`vfacets` silently loses lexical/facet recall (assembly still works). **A
complete dictionary carries `vfacets`.** (This is the failure this session hit:
a rebuild without the stage → `mdb_dbi_open: vfacets` → `verbalizer: 0`.)

---

## 4. Home B — the `vfacets.bin` bundle channel (browser)

A shippable binary channel in the dictionary bundle, **indexed by the vocab index
`n`** — parallel to `epa.bin` and `facets.bin`, three arrays over one vocabulary.

```
MAGIC   = b"ELOVFT\x01\x00"                    # 8 bytes
HEADER  = struct '<8sII'  (magic, count, fp_len)  = 16 bytes,  + 64-byte fingerprint  = 80-byte header
RECORD  = struct '<BB'    (2 bytes)  ×  count      # same b0/b1 packing as §2
```

The 80-byte header and the trailing fingerprint match `epa.bin`/`facets.bin`
exactly, so the family-binding check that cross-verifies the `.bin` channels
covers `vfacets.bin` the same way. A browser reader is a mirror of `facets.rs`
(`include_bytes!`, zero parse, `vfacets[n]`); build it when a client consumer
wants role/polarity per `n`.

**`vfacets.bin` is an OPTIONAL channel.** The exporter emits it only when the
source dictionary carries the `b'vfacets'` sub-DB (`export_browser_assets.py`
guards on its presence). Every consumer and every completeness check must treat
its absence as valid — an older or partial build simply has no `vfacets.bin`.

---

## 5. Identity — by reference, never restated

The bundle authors a per-file pin, **`assets.meta.json:vfacets_bin_sha256_16`**,
alongside `epa_bin_sha256_16` / `facets_bin_sha256_16`. That authored SHA is the
identity of the shipped `vfacets.bin`; a binding (`[bindings.vfacets]`) pins to
it, and `verify_substrate_chain.py` checks `sha256_16(vfacets.bin) == the
authored pin`.

Do **not** restate a vfacets fingerprint as a literal anywhere. The dictionary
fingerprint the vfacets records were built against is read from the build's
`assets.meta.json` / `facets_stats.json`, never hand-copied — the same rule the
tier and facets specs are (being) held to, for the same reason: a restated
identity drifts, and the drift is silent.

---

## 6. Coverage is provisional; the format is not

The **format** in §2 is normative and stable. The **coverage** of each dimension
is a function of the assignment pipeline and is still improving — do not read a
`0` as an assertion of `UNKNOWN`, only as "not yet assigned":

- **polarity / temporal / domain** — filled deterministically by
  `vfacet_builder.py` from EPA + heuristics. Polarity is EPA-gated: on
  `elo-browser-v01c` only the EPA-hit surfaces (~50% of vocab, bounded by the
  `epa_substrate.lmdb` the builder reads) got a non-`NEUTRAL` polarity; the rest
  default to `NEUTRAL`. Widening this is an open item — see §7.
- **agency / direction** — default `UNKNOWN`, then filled by `vfacet_llm.py` in
  two passes (Pass 1 deterministic from EPA A/E axes + curated lists; Pass 2
  LLM over the remaining UNKNOWNs). See `GUIDE-vfacet-llm.md`.

A consumer that needs high-confidence facets should treat non-zero as signal and
zero as absent, not as a negative assertion.

---

## 7. Build + open items

**Build order.** `vfacets` is a stage over an already-built dictionary:
`vfacet_builder.py --db <dictionary.lmdb> --epa-db ../Memory/data/epa_substrate.lmdb`,
optionally followed by `vfacet_llm.py` for agency/direction. The bundle exporter
then emits `vfacets.bin` + the authored pin.

**Open items (declared, not yet closed):**

1. **Fold `vfacet_builder` into the build/publish pipeline** so every build ships
   `vfacets` (sub-DB + `vfacets.bin`) by construction. Today it is a manual
   post-step; a rebuild that skips it produces a dictionary that silently loses
   verbalizer recall (§3). This is the durable fix and belongs in
   `spec-publish-dictionary.md` / `build_assets.py`.
2. **Higher polarity coverage** — build vfacets against the fuller EPA source
   (the dictionary's own `epa` sub-DB, ~236k entries) rather than the ~68k
   `epa_substrate.lmdb`, if it can be adapted to the `b'en|surface'` key the
   builder expects. Would lift polarity well above the current ~50%.
3. **Immutable build identity** — vfacets inherits the dictionary's build
   fingerprint, so a build re-exported *in place* under the same name carries a
   different `vfacets` than a doc pinning that name expects. The naming policy in
   `spec-tier-system.md` / `spec-build-family.md` governs this; vfacets defers to
   it.

---

## 8. Verifying

Record round-trip (the packing is its own inverse):

```python
from semantic_compression.vfacet_builder import pack_vfacet, unpack_vfacet
for a in range(4):
  for d in range(6):
    for t in range(6):
      for m in range(10):
        for p in range(4):
          assert unpack_vfacet(pack_vfacet(a,d,t,m,p)) == \
                 {'agency':a,'direction':d,'temporal':t,'domain':m,'polarity':p}
```

Sub-DB presence + coverage on a built dictionary:

```python
import lmdb
e = lmdb.open('<dictionary.lmdb>', readonly=True, subdir=True, lock=False, max_dbs=20)
vf = e.open_db(b'vfacets', create=False)          # raises if the build lacks the stage
with e.begin() as t: print('vfacets entries:', t.stat(vf)['entries'])
```

Bundle channel: `sha256_16(vfacets.bin)` must equal
`assets.meta.json:vfacets_bin_sha256_16`, and the 80-byte header's trailing
fingerprint must match the other `.bin` channels (family binding).

---

## 9. Change log

| spec | date | change |
|---|---|---|
| 1 | 2026-08-13 | First normative declaration. Record (2 bytes, 5 dimensions), sub-DB (`b'vfacets'`) and bundle (`vfacets.bin`, `ELOVFT`) homes, optional-channel contract, authored pin (`vfacets_bin_sha256_16`), identity-by-reference. Assignment pipeline in `GUIDE-vfacet-llm.md`. |
