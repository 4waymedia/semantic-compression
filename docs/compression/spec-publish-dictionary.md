# Spec — Publishing a Dictionary (stage 12) — spec 1, **2026-08-07**

> **Status: DESIGN.** Decided with Paul 2026-08-07: build and publish are **separate verbs**,
> and publish runs its own verification. This spec defines the publishable object, its identity,
> its gates, and where it lands. Nothing here is implemented yet.
>
> Companion: [`DICTIONARY-BUILD-RUNBOOK.md`](../../DICTIONARY-BUILD-RUNBOOK.md) (stages 1–11),
> [`spec-asset-pipeline.md`](spec-asset-pipeline.md) (one vocabulary, one `n`),
> [`../../../ELO_PACKAGING_STANDARD.md`](../../../ELO_PACKAGING_STANDARD.md) §2 (`incubate |
> develop | published`).

---

## 1. Why — measured on `elo-browser-v01c`, 2026-08-07

### 1.1 Five fingerprints identify one build

| source | fingerprint |
|---|---|
| `manifest.json` `artifacts.dictionary` | `b0164e50816af845` |
| `manifest.json` `artifacts.facets` | `f4c8879e5311f892` |
| `assets_pipeline.json` `dict_sig` | `3b2a3a8e093cffda` |
| `dictionary.denotative.json` `surfaces_fingerprint` | `9780202f7c745ea9` |
| `<build>.browser.json` · `assets.meta.json` · `neighbours.meta.json` · `ACTIVE_BUILD` | **`bdec07bf404aa5d7`** |

The **browser bundle is internally consistent** — four files, one hash. The **build directory is
not**: the manifest, the pipeline ledger and the denotative sidecar each carry a different
identifier. "Are these files from the same build?" has no single answer.

`spec-asset-pipeline.md` §1 already states the invariant — *`epa.bin`, `facets.bin` and
`neighbours.bin` are parallel arrays over the SAME vocab index `n`… they must be built from the
same build, or `channel[n]` disagrees.* It is enforced inside the bundle and nowhere else.

### 1.2 The manifest denies a channel that ships

```
manifest.artifacts.epa.present = false
  notes: "EPA match step not yet run (see EPA.md)"

epa.bin   3,142,544 bytes · 208,556 entries · present in the v01c bundle
```

Stage 7 exported it; stage 9 never learned. This is precisely the failure `ACTIVE_BUILD`
documents — *an identity retyped in a consumer is an identity that can disagree with the
artifact*.

### 1.3 `deliverables` describes the wrong object

`manifest.json.deliverables` lists `dictionary.denotative.index` and `.vecs.f32` — **1.5 GB of
build-time input** whose only job is to produce `neighbours.bin`. It does **not** list
`epa.bin`, `facets.bin` or `neighbours.bin`, because stages 7–8 write those into
`ELO-Browser/`, outside the manifest's view.

> **The 11 MB that ships has no manifest. The 1.5 GB that must not ship is what the manifest
> calls the deliverable.**

### 1.4 Nothing has ever been published

Every entry in `manifest.artifacts` is `status: "staged"`. There is no `published` transition,
because there is no publish step.

---

## 2. The publishable object — the **bundle**

A published dictionary is exactly this, and nothing else:

```
elo-dictionary/<build>/
  <build>.browser.json     vocab: {surface, id, n}      ~12.7 MB
  facets.bin               role      — parallel over n   ~1.0 MB
  epa.bin                  affect    — parallel over n   ~3.1 MB
  vfacets.bin              reasoning — parallel over n   ~0.5 MB  (optional)
  neighbours.bin           meaning   — CSR over n        ~7.3 MB
  facets.names.json        code -> name legend             <1 KB
  BUNDLE.json              the manifest of record          <4 KB
                                                       ~24 MB total
```

**Explicitly NOT in the bundle** — build-time inputs, retained in the build dir, never shipped:
`dictionary.denotative.{index,vecs.f32,surfaces.json}` (1.5 GB), `meta.db`, `dictionary.lmdb`,
`token-ids.csv.gz`, every `*_stats.json`.

> The denotative index is the **machine that makes** `neighbours.bin`. Consumers need the
> neighbours, not the machine. This is the 138× reduction already achieved by stage 8 — the
> publish step's job is to make it official rather than incidental.

### 2.1 `BUNDLE.json`

One file, one identity, and the **only** thing a consumer must read to know what it has.

```json
{
  "schema": "elo-dictionary-bundle/1",
  "build": "elo-browser-v01c",
  "display": "elo-v3",
  "status": "published",
  "published_utc": "…",
  "bundle_fingerprint": "…",          // §3 — over the bundle, not the build
  "source_build_fingerprint": "bdec07bf404aa5d7…",
  "vocab": { "entries": 261872, "cut": "full", "version": "elo-browser-v01c" },
  "channels": {
    "facets":     { "file": "facets.bin",     "sha256": "…", "entries": 261872, "coverage": 1.000 },
    "epa":        { "file": "epa.bin",        "sha256": "…", "entries": 208556, "coverage": 0.796 },
    "vfacets":    { "file": "vfacets.bin",    "sha256": "…", "entries": 261872, "coverage": 1.000, "optional": true },
    "neighbours": { "file": "neighbours.bin", "sha256": "…", "entries":  84854, "coverage": 0.324,
                    "format": "CSR: 80B header + (count+1) u32 offsets + records(u32 n,u8 sim)",
                    "k": 16, "min_sim": 0.35 }
  },
  "gates": { … },                     // §4 — results, not claims
  "provenance": { "corpus_fingerprint": "…", "bound_model": "all-mpnet-base-v2",
                  "built_utc": "…", "assets_pipeline_ledger": "…" }
}
```

**`coverage` is mandatory per channel and must be recorded, not assumed.** `neighbours` covers
**32.4%** of vocab entries (84,854 / 261,872 at `min_sim ≥ 0.35`). A consumer that treats
denotative lookup as universally available is wrong two times in three by entry count. Publish it
as a number so nobody has to discover it.

**`vfacets` is an OPTIONAL channel.** It is present only when the source dictionary carries the `b'vfacets'` sub-DB; a consumer must treat its absence as valid. Its record, both homes, and the `vfacets_bin_sha256_16` pin are defined in [`spec-vfacets-db.md`](spec-vfacets-db.md).

---

## 3. One fingerprint — how it is computed

```
bundle_fingerprint = sha256 over the sorted list of
                     "<filename>\t<sha256-of-file>\n"
                     for every file in the bundle EXCEPT BUNDLE.json
```

Properties that matter:

- **Deterministic and order-free** (sorted), so two machines agree.
- **Self-excluding**, so `BUNDLE.json` can carry it without circularity.
- **Complete** — every shipped byte is covered. The v01c situation, where `neighbours.bin`'s
  hash lives only in a separate `neighbours.meta.json` and is absent from `assets.meta.json`,
  becomes impossible.
- Reuses the existing convention in `artifact_identity.py` (sorted, tab-joined, sha256) so it is
  not a fifth hashing scheme. **It replaces the five in §1.1 for consumers**; the build-side
  hashes remain as internal provenance under `provenance`.

`ACTIVE_BUILD` continues to select a bundle by *name* and to read identity from the bundle. After
this spec, the file it reads is `BUNDLE.json`, not `assets.meta.json`.

---

## 4. Stage 12 — `publish_dictionary.py`

Runs **after** stage 11. Assembles, verifies, then stamps. **Refuses on any failure — never
publishes a partial bundle.**

```powershell
# [venv: yes — root]   from semantic_compression/
python publish_dictionary.py db/builds/elo-browser-v01c --dry-run   # plan + gates, write nothing
python publish_dictionary.py db/builds/elo-browser-v01c             # assemble, verify, stamp
python publish_dictionary.py --verify elo-dictionary/elo-browser-v01c   # re-check a published bundle
```

### 4.1 Gates — all must pass, results recorded in `BUNDLE.json.gates`

| # | gate | how | why |
|---|---|---|---|
| G1 | **one vocabulary, one `n`** | `len(facets.bin/rec) == len(epa.bin/rec) == vocab_entries`; neighbours CSR offset count `== vocab_entries + 1` | the `spec-asset-pipeline` §1 invariant, finally executable |
| G2 | **channel fingerprints agree** | every channel's source build fingerprint == `source_build_fingerprint` | catches an `epa.bin` from a different build (the §1.1 defect) |
| G3 | **manifest agrees with reality** | for each channel, `present == file exists`; entry counts match the files | catches §1.2 — a manifest that denies a shipped channel |
| G4 | **no build-time artifact in the bundle** | assert the §2 deny-list absent | catches 1.5 GB shipping by accident |
| G5 | **round-trip** | `verify_lossless.py` against the bundle's vocab | the dictionary still decodes what it encodes |
| G6 | **facets** | `verify_facets.py` | facet channel is sane |
| G7 | **coverage recorded** | each channel's `coverage` computed from the file, not copied from a stats json | §2.1 — measured, never inherited |
| G8 | **spot read** | resolve a known surface through all three channels and assert non-empty facets, an EPA triple, and `k<=16` neighbours | end-to-end proof the arrays align at a real `n` |

> **G8 is the one that would have caught the most.** G1–G4 compare metadata; G8 reads an actual
> word through the actual bundle. A build can satisfy every count and still have `channel[n]`
> misaligned by one.

### 4.2 Where it lands

Per `ELO_PACKAGING_STANDARD.md` §2 (`publish → frozen, pinned, versioned copy`):

```
dist/dictionary/<build>/     # published bundles, one dir per build, never edited
```

- **Not under `packages/`.** The uv workspace globs `packages/*` as Python members; a data dir
  there is misread as a package. Bundles are a distribution artifact → `dist/dictionary/`.
- **Never overwrite an existing published bundle.** A rebuild is a new `<build>`; every dictionary
  build is a version/tag.
- The browser consumes a copy under `ELO-Browser/.../dictionary/<build>/`, selected by
  `ACTIVE_BUILD`. That copy is a *deployment* of a published bundle, not a second source.

> **Naming note — the collision is now REAL (updated 2026-08-07).** `compression-dictionary` has
> been renamed to the Python package **`packages/elo-dictionary/`** (R-A landed). So `elo-dictionary`
> is now unambiguously the *package* (codec/tokenizer/config); the published **data** bundle must
> NOT reuse that path. It lands at `dist/dictionary/<build>/` instead. Same idea, two kinds of thing
> — the package **reads** bundles; it does not contain one.

---

## 5. What changes in the existing pipeline

| stage | change |
|---|---|
| 7 `export_browser_assets.py` | also record `neighbours` in `assets.meta.json`, or stop recording per-file hashes there at all and leave it to `BUNDLE.json` — **one place, not two** |
| 9 `artifact_identity.write_registry` | fix `artifacts.epa.present` (§1.2). Mark `deliverables` entries as `build` vs `bundle` so §1.3 is visible in the build dir too |
| 10 `stamp_meta.py` | unchanged — `staged` remains the post-build state; `published` is stage 12's to set |
| 11 verify | unchanged — build-level gates stay where they are; stage 12 re-runs G5/G6 **against the bundle**, which is a different object |

---

## 6. Settled — decided by Paul, 2026-08-07

### 6.1 ✅ **Raw at rest, `.tar.zst` for transport**

**The published bundle is raw files.** The three `.bin` channels stay mmap-able and
`neighbours.bin` is readable by CSR offset with no parse and no load step — which is what the
browser's interactive reply path needs.

**Distribution is a separate concern.** Ship/fetch `<build>.tar.zst`; **expansion is an install
step, not a load step.** The published directory is always the raw form; the archive is a
transport encoding of it.

```
elo-dev/packages/elo-dictionary/<build>/          <- raw. canonical. mmap-able.
elo-dev/packages/elo-dictionary/<build>.tar.zst   <- transport only
```

Rejected: compressed-at-rest. `neighbours.bin` would have to be fully decompressed into memory
before the first offset read, which trades away the one property that makes precomputed
neighbours worth having.

> **The `bundle_fingerprint` (§3) is computed over the RAW files.** The archive is a container,
> not a member — it is never in the hash, and a bundle that round-trips through `.tar.zst` must
> reproduce the same fingerprint. Make that a gate on the expand path.

### 6.2 ✅ **One bundle at full `n`; cuts are consumer views**

A published bundle carries the **full** vocabulary and one channel set. The five profiles in
`profile-cuts.json` (Tiny / Compact / Standard / Full / Reference) are applied **by the
consumer**, not baked into the artifact.

**Why.** The three channels are parallel arrays over the full vocab index `n`. Emitting per-cut
bundles means re-indexing every channel per cut — five different `n` values, five fingerprints,
and *one vocabulary, one `n`* becomes five invariants to hold instead of one. That invariant is
the thing this spec exists to make enforceable; multiplying it by five to save disk is a bad
trade.

A cut is a **view over `n`**, so a consumer applying one keeps using the same `channel[n]`
lookups. Nothing about a cut changes what facets, epa or neighbours say about a word.

> If a real consumer ever needs a reduced bundle — an on-device Tiny profile that cannot carry
> 24 MB — add `--cut` to `publish_dictionary.py` **then**, as a derived artifact that records
> which full bundle it came from. Do not build it speculatively.

---

## 7. Still open

1. **Retention.** How many published bundles stay resident in the browser tree? v01a/b/c are all
   present today (~72 MB). A GC policy, or keep forever? Note §6.1 makes this cheaper to answer:
   old bundles can be archived to `.tar.zst` in place and expanded only if a rollback needs them.
2. **Submodule coupling assertion.** `packages/elo-compression/export-package.py --check` is green
   only against a specific `semantic_compression` commit (the `facet_builder` path fix, landed
   2026-08-07). If the parent is ever committed without the submodule pointer, `--check` goes red
   and reads as new drift rather than a missing pointer. A one-line assertion of the expected
   submodule commit would turn that into a clear message. Not blocking stage 12.
