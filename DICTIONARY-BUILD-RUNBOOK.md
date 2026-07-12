# Dictionary Build Runbook — how `elo-browser-v01` was built, and how to rebuild with matching assets

> For the dictionary cowork. Reconstructed from the build's own `manifest.json`,
> `builds/elo-browser-v01.yaml`, and `docs/compression/spec-asset-pipeline.md`
> (the contract). Every command tagged `[venv: yes — root]` unless noted.
>
> **The headline:** the "matching assets" pipeline you want **already exists** —
> `build_assets.py`, an 11-stage, fingerprint-guarded, ledger-tracked orchestrator
> written after the July 2026 asset work. The refactor is to make it **one contract
> the browser consumes**, not to build it from scratch. Read
> `docs/compression/spec-asset-pipeline.md` first; this runbook is the operator's
> view of it.

---

## 0. The two-stage shape (this is the whole mental model)

A dictionary build is **core** then **derived assets**. They are two commands today.

```
STAGE A — CORE            python -m semantic_compression.build_from_spec builds/<name>.yaml
  dictionary.lmdb (forward/reverse)  + optionally facets, meta L1
  the ONLY hand-authored input is the YAML spec. Everything below is derived.

STAGE B — DERIVED ASSETS  python build_assets.py db/builds/<name>
  11 stages, in dependency order, each a fingerprint-guarded re-derivation.
  Ledger: db/builds/<name>/assets_pipeline.json  (records what ran, when, from what sig)
```

`build_from_spec` prints `downstream(build_assets)=[...]` — the assets the spec
declared but that Stage B must produce. It does **not** run Stage B itself. **That
hand-off is the single most important thing to get right in the refactor** (see §5).

---

## 1. How `elo-browser-v01` was actually built

**Spec:** `builds/elo-browser-v01.yaml` · **Resolver:** `build_from_spec` ·
**Created:** 2026-06-24 · **Status:** `staged` (never `frozen` — no model trained yet).

### Build parameters (`manifest.json`)

```
size                char-4           tiers 0-3; full profile cut = 262,144
select_strategy     frequency        (score_by_frequency)
min_freq            1
tier1_word_reserve  1024
with_facets         true
entries             437,990
tier_counts         [53, 1280, 81920, 354737]     (T0, T1, T2, T3)
corpus_fingerprint  575bfe79…
```

### Corpus (three sources, all present on disk)

| source | file | role |
|---|---|---|
| transcripts | `data/word_frequencies.txt` (206,642 uniq) | v0.4 general base (373 channels) |
| books | `Resources/books/*.txt` (26 files) | literary coverage |
| **web fold-in** | `data/web_terms_frequencies.txt` (14,487 terms) | the HTML/website group the general dict lacks |

The web fold-in is the defining feature: a synthetic frequency band that places
HTML/structure tokens into tier-2/3 so the same artifact compresses captured pages.
**Density is NOT from the dictionary** — it comes from the codec (implicit-whitespace
+ caps), proven separately (3.61 → 5.44 chars/tok on the general char-4 full cut).
Do not bake space-variants into the dictionary to chase density; that was a wrong
detour.

### Reproduce the core

```powershell
# [venv: yes — root]   from R-D-concepts/
python -m semantic_compression.build_from_spec semantic_compression/builds/elo-browser-v01.yaml
```

> **Mount/overwrite gotcha:** LMDB overwrite fails if the dir can't be unlinked
> (the Linux sandbox can't; Windows can). `build_package(overwrite=True)` handles a
> fresh Windows build. If a build ever half-writes, delete the dir on Windows
> (`Remove-Item -Recurse -Force`) and rebuild to the same name.

---

## 2. The 11 derived-asset stages (the contract — `spec-asset-pipeline.md` §, table)

`build_assets.py` runs these in order. Each script owns its own atomic write; the
driver only orchestrates and records the ledger. **Verified present on disk:** all
scripts below exist (`export_browser_assets.py` 283 lines, `export_neighbours.py`
269 lines).

| # | stage | script | emits |
|---|---|---|---|
| 1 | facets | `facet_builder.py` | `facets` sub-DB, `facets_stats.json` |
| 2 | meta L1 | `meta_builder.py` | `meta.db` (layer 1) |
| 3 | epa | `epa_match.py` | `epa` sub-DB (from `Memory/data/epa_substrate.lmdb`) |
| 4 | meta L2 | `meta_layer2.py` | `meta.db` L2 — epa_e/p/a, polarity, abstraction, **frequency** |
| 5 | **denotative** | `denotative_index.py embed && finalize` | `dictionary.denotative.*` — **mpnet 768-d meaning index** |
| 6 | browser vocab | `export_browser_vocab.py --cut full` | `<build>.browser.json` (`{surface,id,n}`) |
| 7 | browser epa/facets | `ELO-Browser/tools/export_browser_assets.py` | `epa.bin`, `facets.bin`, `assets.meta.json` |
| 8 | browser neighbours | `ELO-Browser/tools/export_neighbours.py` | `neighbours.bin` (+ updates `assets.meta`) |
| 9 | registry | `artifact_identity.write_registry` | `manifest.json` artifacts registry |
| 10 | stamp | `stamp_meta.py --status staged` | meta stamp in lmdb |
| 11 | verify | `verify_lossless.py`, `verify_facets.py`, `bench_dict_efficiency.py` | pass/fail gates |

### Run it

```powershell
# [venv: yes — root]   from semantic_compression/
python build_assets.py db/builds/elo-browser-v01              # rebuild stale only
python build_assets.py db/builds/elo-browser-v01 --dry-run    # plan; run nothing
python build_assets.py db/builds/elo-browser-v01 --force      # rebuild everything
python build_assets.py db/builds/elo-browser-v01 --from 5 --device cuda   # embed onward, GPU
```

---

## 3. The asset-matching contract — "one vocabulary, one `n`"

This is the invariant the whole refactor exists to guarantee. From
`spec-asset-pipeline.md` §1, verbatim:

> `epa.bin`, `facets.bin`, and `neighbours.bin` are parallel arrays over the SAME
> vocab index `n` (from `<build>.browser.json`). `facets` says what you may do with a
> word, `epa` how it feels, `neighbours` what it means. **They must be built from the
> same build, or `channel[n]` disagrees.**

So "matching assets" is not a nice-to-have — it is a **structural requirement**:
`channel[n]` in every `.bin` must index the same surface that `n` maps to in
`.browser.json`. Stage 6 fixes the `n`; stages 7–8 must be derived from *that exact*
stage-6 output, never from a different build. `build_assets`'s fingerprint guard is
what enforces it: change the dictionary, and stages 6/7/8 go stale together and
rebuild together. **Never hand-copy one `.bin` from one build and a `.json` from
another** — that is the ID-alignment hazard your memory file already records.

`assets.meta.json` (stage 7) is the manifest that ties them: it carries the build
fingerprints and the vocab `n`-count the three `.bin`s share. It is the file the
browser should read to verify it holds a matched set (this is the browser's
`systems-manifest.json`).

---

## 4. Current state of `elo-browser-v01` (verified 2026-07-08)

| asset | state |
|---|---|
| `dictionary.lmdb` forward/reverse | ✅ built |
| facets sub-DB, meta.db | ✅ L1 built; **L2 present** (epa_e, polarity, abstraction, frequency 98.3%) |
| epa sub-DB | ✅ present |
| `epa.bin`, `facets.bin`, `assets.meta.json` in browser dir | ✅ present (stage 7 output landed) |
| `neighbours.bin` (stage 8, denotative) | ⚠️ **UNVERIFIED** — confirm on Windows |
| `assets_pipeline.json` ledger | ❌ **absent** → `build_assets.py` was never run end-to-end as the orchestrator; stages were run piecemeal |

**Read this correctly:** the assets mostly exist, but they were produced by running
individual stage scripts by hand, not by `build_assets`. So there is **no ledger
proving they are all from the same dictionary signature.** The first refactor action
is to run `build_assets db/builds/elo-browser-v01 --force` once, so the ledger exists
and the matched-set guarantee is real rather than assumed.

---

## 5. The refactor — what "matching assets" should mean operationally

Everything below is a wiring/contract change. The machinery exists.

1. **One command, or an enforced hand-off.** `build_from_spec` prints
   `downstream(build_assets)` but doesn't run it. Either have it invoke
   `build_assets` at the end, or add a `build_dictionary.py <spec>` wrapper that runs
   A then B. A dictionary is not "done" until its ledger is green — encode that.

2. **The ledger is the source of truth for freshness.** After any core rebuild,
   `build_assets` must be re-run; the fingerprint guard already rebuilds only what
   went stale. Make a green ledger a release gate (stage 11 verify must pass).

3. **The browser consumes the matched set, never a snapshot.** Ship stages 6–8
   output (`<build>.browser.json`, `epa.bin`, `facets.bin`, `neighbours.bin`,
   `assets.meta.json`) into `src-tauri/dictionary/` as ONE atomic drop. The browser
   reads `assets.meta.json` to confirm they match; it must not read a hand-maintained
   `epa.json` (that legacy 13,905-entry file is what caused stale affect — it should
   be retired in favour of `epa.bin`, which covers 236,645 surfaces incl. phrases).

4. **`stamp_meta --status staged` until a model is trained.** IDs are provisional
   while staged; downstream binds to surfaces or re-derives. Only `frozen` (post
   model-train) makes the fingerprint a contract. Don't let the browser treat a
   staged build's ids as permanent.

5. **`domain` / `register` are still empty** in meta L2 (reserved, never populated).
   If the browser needs topic/domain semantics, that is a **new stage** (a tagger),
   not a wiring fix. Scope it separately; don't assume L2 provides it.

---

## 6. Fresh-machine reproduce (start to shippable assets)

```powershell
# [venv: yes — root]   from R-D-concepts/
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.prefix)"          # must end \R-D-concepts\.venv (uv venv)

# Stage A — core
python -m semantic_compression.build_from_spec semantic_compression/builds/elo-browser-v01.yaml

# Stage B — all derived assets, from scratch (embed stage wants a GPU)
cd semantic_compression
python build_assets.py db/builds/elo-browser-v01 --force --device cuda

# Confirm the matched set + gates
python build_assets.py db/builds/elo-browser-v01 --dry-run     # every stage SKIP == fresh + consistent
type db\builds\elo-browser-v01\assets_pipeline.json            # ledger green
```

Inputs required (all present today): `data/word_frequencies.txt`,
`data/web_terms_frequencies.txt`, `data/phrase_candidates.txt`, `Resources/books/`,
`Memory/data/epa_substrate.lmdb`, `Memory/data/concreteness_substrate.lmdb`, and the
mpnet model for stage 5.

---

## 7. Honesty ledger (what I verified vs. did not)

**VERIFIED** (read from disk / manifest / spec this session):
- build params, corpus sources + fingerprints, tier counts.
- the 11-stage table (from `spec-asset-pipeline.md`).
- stage 7/8 exporter scripts exist; `epa.bin`/`facets.bin`/`assets.meta.json` are in the browser dir.
- meta.db is layer 2 (epa/polarity/abstraction/frequency populated; domain/register empty).
- `build_from_spec` does NOT auto-run `build_assets`.

**UNVERIFIED — confirm on Windows before relying on it:**
- whether `neighbours.bin` (stage 8, denotative) was actually produced.
- whether `build_assets` runs clean end-to-end on this machine (no ledger exists yet).
- `assets.meta.json` contents (the sandbox mount returned a truncated copy; read it on Windows).
- whether the browser's Rust actually reads `epa.bin`/`facets.bin` yet, or still the legacy JSON path (that is the consumer-side half, tracked in `ELO-Browser/bindings.toml`).

Treat every UNVERIFIED line as a hypothesis until a command settles it — same rule the
rest of this repo runs on.
