# Spec — Dictionary Asset Pipeline

> **Every dictionary build has a fixed set of derived assets. They must be
> re-derived, in dependency order, every time the dictionary is (re)built.** This
> spec is the contract; `build_assets.py` is the executor. Driver: `python
> build_assets.py db/builds/<name>` (from `semantic_compression/`).
>
> Written after the 2026-07 asset work, which found the ad-hoc process caused two
> silent failures: a non-atomic write **corrupted `meta.db`** while reporting
> success, and a cwd-relative path made `meta_fingerprint` **irreproducible**. The
> rules in §4 exist to make those impossible.

---

## 1. The principle

A dictionary is one artifact (`dictionary.lmdb`: forward/reverse). Everything else
is a **derived asset** — a deterministic (or reproducible) function of the
dictionary plus fixed external inputs (EPA substrate, embedding model). Derived
assets are **re-derived per build, never hand-edited, never persisted across
builds.** Raw IDs are provisional until a build is `frozen`; downstream binds to
surfaces, or re-derives.

Three of the assets form the browser's runtime substrate, and share one key:

> **One vocabulary, one `n`.** `epa.bin`, `facets.bin`, and `neighbours.bin` are
> parallel arrays over the SAME vocab index `n` (from `<build>.browser.json`).
> `facets` says what you may do with a word, `epa` how it feels, `neighbours` what
> it means. They must be built from the same build, or `channel[n]` disagrees.

> **Status:** `epa.bin` and `facets.bin` **ship today**. `neighbours.bin`'s emitter
> (`export_neighbours.py`, stage 8) is now **written and self-tested** (CSR packing +
> all filters), but the channel is **not yet live**: it needs the 768-d denotative
> index (`vectors`, stage 5) built on the browser build's own vocab, which is the
> remaining blocker. Stage 8 gates on `vectors`; enable `vectors` in the suite and run
> the index, then `export_neighbours` produces `neighbours.bin`.

> **Not in this DAG: the EPA affect index** (`db/dictionary.faiss.index`). It is a
> 3-d nearest-neighbour index derived from `epa_substrate.lmdb`, **not** from the
> dictionary — it rebuilds when the *substrate* changes, on the substrate's
> fingerprint, and is consumed server-side by the verbalizer, not by the browser.
> This DAG covers only **dictionary-derived** assets. The substrate→index binding
> has its own gate, `verify_faiss.py` (§7).

---

## 2. The asset DAG (dependency order)

```
(0) dictionary.lmdb  forward/reverse            build_package / build_from_spec   ROOT
     │
     ├─(1) facets sub-DB            facet_builder.py --db …            S1 deterministic
     ├─(2) meta.db  (layer 1)       meta_builder.py <pkg>              S1 deterministic  (+complement col)
     │        │
     │        └─(3) epa sub-DB      epa_match.py <pkg>                 S2  needs EPA substrate
     │                 │
     │                 └─(4) meta.db (layer 2)  meta_layer2.py <pkg>   S2  fills epa/polarity/concreteness
     │
     ├─(5) denotative index         denotative_index.py embed+finalize  needs embed model (mpnet)
     │        vecs.f32 + .index + surfaces
     │
     ├─(6) browser vocab            export_browser_vocab.py  <build>.browser.json (projects token-ids) → surface->n
     │
     ├─(7) browser epa.bin+facets.bin   export_browser_assets.py       needs (6),(3),(1)
     ├─(8) browser neighbours.bin       export_neighbours.py           needs (6),(5)   ← the ID-keyed map
     │
     ├─(9) registry / manifest      artifact_identity.write_registry   fingerprint binding
     ├─(10) stamp                    stamp_meta.py --release --status   lifecycle
     └─(11) verify                   verify_lossless / verify_facets / bench_dict_efficiency
```

Determinism classes: **S1** = pure deterministic (facets, meta L1, complement) —
byte-identical across runs, part of `meta_fingerprint`. **S2** = reproducible given
a versioned external substrate (epa, concreteness). **Model** = reproducible given a
pinned embedding model (denotative, neighbours).

---

## 3. Per-asset contract

| # | asset | script (cwd `semantic_compression/`) | inputs | outputs |
|---|---|---|---|---|
| 1 | facets | `facet_builder.py --db <pkg>/dictionary.lmdb --overrides data/facet_overrides.tsv` | lmdb, overrides | `facets` sub-DB, `facets_stats.json` |
| 2 | meta L1 | `meta_builder.py <pkg>` | lmdb, overrides, `verb_complements.py` | `meta.db` (layer 1) |
| 3 | epa | `epa_match.py <pkg>` | lmdb, `Memory/data/epa_substrate.lmdb` | `epa` sub-DB |
| 4 | meta L2 | `meta_layer2.py <pkg>` | meta.db, epa sub-DB, concreteness norms | `meta.db` (layer 2) |
| 5 | denotative | `denotative_index.py embed … && finalize …` | meta.db, mpnet | `dictionary.denotative.{vecs.f32,index,surfaces.json,json}` |
| 6 | browser vocab | `export_browser_vocab.py --build <pkg> --cut full` | token-ids.csv.gz, profile-cuts.json | `<build>.browser.json` (`surface,id,n`) |
| 7 | browser epa/facets | `../ELO-Browser/tools/export_browser_assets.py --build <pkg>` | vocab, epa, facets | `epa.bin`, `facets.bin`, `assets.meta.json` |
| 8 | browser neighbours | `../ELO-Browser/tools/export_neighbours.py --build <pkg>` | vocab, denotative index | `neighbours.bin` (+ `assets.meta` update) |
| 9 | registry | `artifact_identity.write_registry(<pkg>)` | all above | `manifest.json` artifacts registry |
| 10 | stamp | `stamp_meta.py --db <pkg>/dictionary.lmdb --release <rel> --status staged` | lmdb | meta stamp |
| 11 | verify | `verify_lossless.py`, `verify_facets.py`, `bench_dict_efficiency.py` | package | pass/fail gates |

`epa.bin` / `facets.bin` wire format (`export_browser_assets.py` 92-96, indexed by
dense `n`): shared **80-byte header** — `<8sII>` = magic (8B) + `count` u32 + `fp_len`
u32 (==64), then the 64-byte dictionary fingerprint — followed by `count` fixed-width
records. Magic is `ELOEPA\x01\x00` / `ELOFCT\x01\x00`. `epa.bin` record = `EPA_REC`
`<fff>` (12 B: E, P, A float32 LE); `facets.bin` record = `FCT_REC` `<BHB>` (4 B:
bucket u8, cue_mask u16, flags u8). Both are `include_bytes!`-mapped by the Rust
side (`epa.rs`, `facets.rs`) — zero parse.

`neighbours.bin` wire format (same 80-byte header as above; matches
`export_browser_assets` exactly): 80-byte
header (`<8sII>` magic/count/fp_len + 64-byte dictionary fingerprint), then a
variable-k list per `n` via an offset table, records `u32 neighbour_n + u8 sim`
(sim = round(cosine*255)). Emit only CONTENT surfaces; absent `n` → empty list.
Filters: `sim < 0.35` pruned; neighbours de-duped by lowercase; a neighbour whose
lowercase equals the query is dropped. k ≤ 16.

---

## 4. Rules (enforced by the driver; violations were live bugs)

1. **No observer ever sees a partial or corrupt asset.** This is the *property*;
   there are two conformant *mechanisms*, and which one applies depends on the
   store — do NOT audit this rule by grepping every writer for `os.replace` (that
   flags LMDB writers as false positives; it happened in the 2026-07 review):
   - **SQLite files** (`meta.db`, stages 2 & 4): build in a scratch dir → `fsync`
     → `PRAGMA integrity_check` → atomic `os.replace` over the target. **No
     overwrite-copy fallback** — `shutil.copyfile` onto a live SQLite file is not
     atomic and a partial write leaves a malformed image. If `os.replace` is
     refused, FAIL loudly leaving a valid staged `.tmp`. (`meta_builder.py`
     120-131, `meta_layer2.py` 229-235.)
   - **LMDB sub-DBs** (`facets`/`epa`, stages 1 & 3): write inside a single
     `env.begin(write=True)` transaction. LMDB is MVCC/ACID — the commit is atomic
     by construction and there is no live-file partial-overwrite class, so these
     writers neither need nor use `os.replace`. (`facet_builder.py` 119/160,
     `epa_match.py` 127.)
   - **`.bin` files** (stages 7 & 8): assemble the full byte image in memory
     (header + all records), then write once; verify `size == 80 + count·record`.
   Regenerable stats sidecars (`*_stats.json`) are exempt — they carry no state and
   are rewritten every run.
2. **Reproducible + cwd-independent.** Inputs resolve module-relative, never by a
   bare relative path in a `try/except`. A missing required input RAISES. Every
   fingerprint's inputs are recorded (`overrides_sha`, `epa_fingerprint`, model
   name+revision).
3. **Re-derive per build; bind to surfaces.** Assets are regenerated every build.
   Until the package is `frozen`, no consumer persists a raw `n`/ID as a
   cross-build reference.
4. **One vocabulary, one `n`** (§1). The denotative index feeding `neighbours.bin`
   MUST be built on the **same** build whose `<build>.browser.json` defines `n`.
5. **Fingerprint chaining = staleness detection.** Each asset records the
   `dictionary_fingerprint` it descends from (`artifact_identity`). If the
   dictionary fingerprint changes, ALL downstream assets are stale. The driver
   treats a changed dictionary fingerprint, a newer stage script, or a missing
   output as "stale → rebuild".
6. **EPA is affect; neighbours is denotation.** Never conflate. `epa.bin` is the
   3-d affect channel (gate in the dense core — see `epa_validity.py`);
   `neighbours.bin` is the 768-d denotation channel. Distinct files, distinct use.

---

## 5. The driver — `build_assets.py`

```
python build_assets.py <pkg>              # rebuild only stale assets (fingerprint-guarded)
python build_assets.py <pkg> --dry-run    # print the plan: per-stage stale/skip, run nothing
python build_assets.py <pkg> --force      # rebuild everything
python build_assets.py <pkg> --only 5,8   # run just these stages (+their outputs)
python build_assets.py <pkg> --from 3     # run stage 3 onward
python build_assets.py <pkg> --device cuda # passed to the embed stage
```

- **Staleness** (per stage): rebuild iff `--force`, OR the dictionary fingerprint
  changed since the recorded run, OR the stage's script mtime is newer, OR any
  declared output is missing. Otherwise SKIP.
- **Ledger:** `assets_pipeline.json` in the package (distinct from the registry's
  `manifest.json`) records, per stage, `{name, dict_sig, ran_at, script_mtime,
  outputs}`. Written atomically (`os.replace`).
- **Heavy-dep stages** (epa: substrate; denotative/neighbours: mpnet+faiss;
  browser: those) are marked; on a machine lacking them the stage reports
  `SKIPPED (missing dep: …)` rather than failing the run.
- **Missing script** (e.g. `export_neighbours.py` before it's written) →
  `PENDING`, not `FAIL`. The driver reports the gap and continues.
- The driver **orchestrates**; each script owns its logic and its own atomic write.
  The driver never writes an asset itself.

---

## 6. Integration

- Browser assets land in `ELO-Browser/elo-browser/src-tauri/dictionary/` and are
  keyed by the build's `n`; re-export whenever the dictionary is rebuilt.

### Open items (tracked, not parenthetical)

- **O1 — auto-wire the driver.** `build_from_spec.py` builds the dictionary +
  facets + meta L1 + registry, but does **not** yet call `build_assets.py` to finish
  the cascade (epa → meta L2 → denotative → browser → stamp → verify). Until this is
  wired, `build_assets.py <out_dir>` must be run manually after every build. This
  gap is the difference between a spec and a pipeline.
- **O2 — build the denotative index on the browser vocab.** `export_neighbours.py`
  (stage 8) is now written + self-tested; what remains is running `vectors` (stage 5,
  the 768-d index) on the browser build's own `meta.db` so `neighbours.bin` aligns
  `n`-for-`n` with `epa.bin`/`facets.bin` (mpnet + faiss + GPU). Enable `vectors` in
  the suite, build the index, then stage 8 emits the channel.
- **O3 — per-dictionary browser output.** The driver writes stage 7/8 assets to a
  single fixed dir (`BROWSER_DICT`), so two published dictionaries collide. Emit to a
  per-dictionary path (`…/dictionary/<dict-id>/`). `export_browser_assets.py` already
  takes `--out`; only the driver constant is single-tenant. The `.bin` headers
  already stamp the 64-byte fingerprint, so the files are self-identifying once the
  layout is per-dictionary.
- **O4 — parameterize the stamp.** Stage 10 hardcodes `--release v0.4.0 --status
  staged`. Publishing arbitrary dictionaries requires release/status/owner as driver
  arguments, not constants.

---

## 7. Pointers

- **North-star target this cascade evolves toward:** [`spec-build-family.md`](spec-build-family.md) (build_id coupling, the full asset family, the 12-step compiler).
- Determinism / fingerprints: [`VERSIONS.md`](../../../VERSIONS.md) §4.
- Meta columns incl. `complement`: [`spec-meta-db.md`](spec-meta-db.md).
- EPA is affect not semantics: [`probe-epa-embedding-results.md`](probe-epa-embedding-results.md).
- Build a dictionary: [`GUIDE-create-dictionary.md`](GUIDE-create-dictionary.md).
- **EPA affect index (out of this DAG) + its read-side gate:** `faiss_builder.py`
  (substrate-derived index) and `verify_faiss.py` (checks the index is still bound to
  the live `epa_substrate.lmdb` fingerprint — the two-artifact ID-alignment hazard).

---

## 8. Multi-dictionary / publication model

The dictionary is a **build-as-you-like product**: any user, team, or org can build a
custom dictionary sized from a handful of IDs up to the tier ceiling, and many
dictionaries coexist and publish independently. This spec supports that at the build
layer today; the driver has two single-tenant assumptions to close (O3, O4).

- **A build IS a dictionary.** Every `db/builds/<name>/` is a complete, independent
  package (`dictionary.lmdb` + all derived assets) with its own content fingerprint.
  There is **no universal "full dictionary"** — six independent builds already
  coexist. `artifact_identity.py` gives each build + asset a
  `{version, fingerprint, status∈staged|frozen|locked, bound_refs}` record, so
  publication state is per-dictionary.
- **Consumer binding invariant.** An `.elo`/`.eloB` stream decodes **only** under the
  exact dictionary it was encoded with. Every browser `.bin` header carries the
  64-byte dictionary fingerprint; every stream binds to that fingerprint. A consumer
  MUST verify the fingerprint matches before decoding — mixing dictionaries silently
  yields wrong surfaces (the two-artifact hazard). Bind to surfaces / fingerprints,
  never to raw `n`/ID across dictionaries (Rule 3).
- **Size range (capacity is tier-char bounded).** The first ID char encodes the tier
  and word tiers draw their first char from `TIER_WORD_FIRST_CHARS = g–z` (20 slots,
  `config.py`). So usable **word** capacity per dictionary is **tier-1 1,280 + tier-2
  81,920 + tier-3 5,242,880 ≈ 5.33M** (`TIER_CAPACITY`), selectable by `size:`
  (`char-2`/`char-3`/`char-4` → `max_tier` 1/2/3), plus the separate
  phrase/collocation/pragmatic partitions on the `-` prefix (tier 4) and codec
  byte-fallback for OOV. Guardrail **G2** enforces the per-tier bound at build time.
  **Going beyond ~5.33M words (a `char-5`/tier-4 *word* depth) is not wired** — tier 4
  is currently the phrase partition, so a deeper word tier needs a first-char-scheme
  decision in `config.py` + a `dictionary_builder_v03` branch (tracked as a capacity
  extension, not a config toggle). Profiles (`tiny…reference`) are frequency-rank cuts
  *within* one dictionary for an LLM budget — orthogonal to how many dictionaries exist.
- **Publication layout (target).** Each published dictionary owns an addressable
  asset dir keyed by its id/fingerprint; a catalog lists `{id, fingerprint, tier
  size, profiles, status, owner}`. O3 makes the browser export per-dictionary; a
  catalog/registry file is the natural next artifact once O3/O4 land.

---

## 9. Validation ledger

Per the project's external-review rule, review claims are labelled against the code
before entering this contract. The 2026-07 review of this spec:

| Claim | Status | Evidence |
|---|---|---|
| Rule 1 (atomic write) violated by 3 of 4 DB writers (`os.replace=0`) | **REFUTED** | Stale/grep-only. `meta_builder.py` 120-131 and `meta_layer2.py` 229-235 implement the full SQLite pattern; `facet_builder.py` 119/160 and `epa_match.py` 127 write via LMDB `env.begin(write=True)` — atomic by MVCC, no `os.replace` needed. Rule 1 rewritten (§4) to state the property + both mechanisms so the grep no longer misfires. |
| `epa.bin`/`facets.bin` wire format missing from the spec | **VALIDATED** | Added inline to §3 from `export_browser_assets.py` 92-96. |
| `neighbours.bin` presented present-tense but unbuilt | **VALIDATED** | Marked PLANNED in §1; `export_neighbours.py` absent → driver stage 8 `PENDING`. |
| `db/…faiss.index` "belongs as a stage between epa and denotative" | **REFUTED** | `faiss_builder.py` 8: index is derived from `epa_substrate.lmdb`, on the *substrate* fingerprint, not the dictionary. It is out of this dictionary-keyed DAG by design; scoped-out note added to §1, gate cross-referenced in §7. |
| Cross-reference `verify_substrate_chain.py` | **UNVERIFIED** | No such file in the repo. The read-side gate that exists is `verify_faiss.py` (now cited §7). |
| `build_from_spec` "should call … until wired" is the real gap | **VALIDATED** | Promoted to tracked open item O1 (§6). |
| Intro should carry `VALIDATED` incident evidence | **VALIDATED** | This ledger; corruption incident cited at `meta_builder.py` 114 (`elo-browser-v01/meta.db`, 2026-07-08). |
| Spec supports publishing many custom dictionaries (1–16M IDs) | **VALIDATED w/ correction** | Per-build packages already coexist (`db/builds/` × 6) with per-build fingerprints + `artifact_identity` status; spec is per-`<pkg>`. Corrections: real word capacity is ~5.33M (`TIER_CAPACITY`, `g–z` 20-slot first char), not 16.7M; `char-5`/tier-4 word depth is unwired; and the driver has two single-tenant assumptions (fixed browser out dir, hardcoded stamp) → O3, O4. Model made explicit in §8. |
