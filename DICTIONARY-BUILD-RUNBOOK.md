# Dictionary Build Runbook — build any dictionary from its spec, with a matched, publishable asset set

> **Updated 2026-08-07.** This was written for `elo-browser-v01`; the three builds after it
> (`v01a`/`v01b`/`v01c`) added the build directions now folded in below — one-command build,
> per-build asset subfolders, oracle-naming-after-build, `expand_tiers`, and a separate publish
> verb. Section 1 keeps the original v01 walk-through as the worked example; §4–§6 are current.
>
> **The one command (build core + full asset cascade):**
> ```powershell
> python -m semantic_compression.build_dictionary semantic_compression/builds/<name>.yaml --device cuda
> ```
> Then publish (§ Stage 12). `build_from_spec` alone is CORE-ONLY — see §5.

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
| 9 | registry | `artifact_identity.py <pkg>` (always-run gate) | `manifest.json` artifacts registry — **re-derived AFTER stages 1–8**, so `epa.present` reflects reality (fixes the pre-epa freeze, spec-publish-dictionary §1.2) and `deliverables_by_kind` tags each file build-vs-bundle (§1.3) |
| 10 | stamp | `stamp_meta.py --status staged` | meta stamp in lmdb |
| 11 | verify | `verify_lossless.py`, `verify_facets.py`, `bench_dict_efficiency.py` | pass/fail gates |
| 13 | vfacets | `vfacet_builder.py` (deterministic half) | `b'vfacets'` sub-DB + `vfacets_stats.json` (`llm_enriched=false`; `vfacet_llm.py` enrichment stays OUT of the cascade) |

> **The manifest updates itself — don't run builders by hand.** Stage 9 (registry) is
> exempt from `--only`/`--from` filtering: every `build_assets` invocation re-derives
> `manifest.json`'s `artifacts` from the artifact, so the config can't go stale behind a
> partial run. If you DO hand-run an asset script, `python artifact_identity.py db/builds/<name>`
> afterward is mandatory. Consumers should not read the manifest at runtime anyway — use
> `compression_dictionary.dictionary_info(lmdb_path)` (one call: identity + available assets
> + sidecar bindings, read from the artifact itself).
>
> **Stage numbers are append-only ids, not positions.** Execution follows list order — stage 13
> (vfacets) actually runs after meta-L2, before denotative. Numbered 13 so `--only`/`--from` and
> every existing `assets_pipeline.json` ledger (keyed `"1"…"12"`) keep their meaning. vfacets is a
> **declared suite asset** (`assets: vfacets: true`; in `preset: full`; requires `epa`).
> Note its keying: `b'vfacets'` is **id-keyed**, unlike `epa.bin`/`facets.bin`/`neighbours.bin`
> which are parallel arrays over the vocab index `n` — the names invite the assumption; don't.

### Run it

```powershell
# [venv: yes — root]   from semantic_compression/
python build_assets.py db/builds/elo-browser-v01              # rebuild stale only
python build_assets.py db/builds/elo-browser-v01 --dry-run    # plan; run nothing
python build_assets.py db/builds/elo-browser-v01 --force      # rebuild everything
python build_assets.py db/builds/elo-browser-v01 --from 5 --device cuda   # embed onward, GPU
```

### Stage 12 — publish (a SEPARATE verb)

Building derives the channels; **publishing** assembles only the shippable files into one
verified bundle. It is deliberately not part of `build_assets.py` — spec-publish-dictionary.md
sec 4. Run it after stage 11 passes:

```powershell
# [venv: yes — root]   from semantic_compression/
python publish_dictionary.py db/builds/<build> --dry-run   # assemble + run gates G1–G8, write nothing
python publish_dictionary.py db/builds/<build>             # publish -> dist/dictionary/<build>/BUNDLE.json
python publish_dictionary.py --verify dist/dictionary/<build>   # re-check a published bundle
```

Gates (all must pass, results recorded in `BUNDLE.json.gates`): **G1** one vocabulary/one `n`,
**G2** channel *and* build-LMDB fingerprints agree (catches a bundle exported from a since-replaced
build), **G3** manifest present-flags match shipped channels, **G4** no build-time artifact in the
bundle, **G5** round-trip, **G6** facets, **G7** coverage measured from the files, **G8** end-to-end
spot read of one surface through all three channels. It **refuses on any failure** — a partial or
inconsistent bundle is never published.

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

## 4. Build directions added by v01a → v01c (what changed after the original v01)

The original v01 walk-through (§1) is still accurate for the CORE build, but four
directions were added by the builds after it. **These are now the defaults; use them.**

| build | direction it introduced | why |
|---|---|---|
| **v01a → v01b** | **Per-build asset subfolder.** Browser assets land in `dictionary/<build>/`, never loose in the `dictionary/` root; the exporter derives the path from `--build`, so no run can drop one build's `.bin` beside another's. | v01a assets were loaded while `vocab_version` reported v01 — a consumer restating an identity that disagreed with the artifact. |
| **v01b** | **Oracles named after their build.** `poc/conformance/<build>/{epa,facets}.json` — one file per build, so exporting a new build can't overwrite an older build's test evidence. | The oracle used to be one flat file every export overwrote; it always described the newest build and could never fail against an older one. |
| **v01c** | **`expand_tiers: true`** — widen the id leading-character alphabet 20→63 (all non-`-` Base64). +3.15× per-tier capacity (T1 1,280→4,032, T2 81,920→258,048). Opt-in per build so earlier builds stay byte-reproducible. | 69% of every tier's capacity was stranded by a restriction left over after tier detection moved to id LENGTH. |
| **2026-08-07** | **One command + separate publish.** `build_dictionary.py` chains core+cascade (§5); `publish_dictionary.py` (stage 12) is the separate publish verb. Stage 9 registry is re-derived **after** the cascade so `artifacts.epa.present` is truthful. | `build_from_spec` printed `downstream(build_assets)` but didn't run it; the manifest froze a pre-epa view; build and publish were entangled. |

**Rebuild discipline.** A rebuild is a **new build name** — or `--force`/overwrite the same
one. `build_from_spec` overwrites `db/builds/<name>/` by default; if the LMDB can't be
unlinked (a process holds it, or a sandboxed FS), build to a fresh `<name>` instead. Every
build is a version/tag; a *published* bundle is immutable (spec-publish-dictionary §4.2).

---

## 5. `build_from_spec` is CORE-ONLY — `build_dictionary` is the whole thing

`build_from_spec` builds the core (dictionary + LLM profile cuts + the suite's facets/meta)
and **prints** `downstream(build_assets)=…` but does **not** run the cascade. Running only it
leaves epa/meta_layer2/vectors/browser unbuilt — the exact trap that made an early manifest
report `epa.present=false` while epa.bin shipped.

`build_dictionary.py` is the single entrypoint that runs **A then B** from the spec's
`suite:` — core, then `build_assets` (epa → meta_layer2 → vectors → browser export →
registry → stamp → verify). The two stay independently runnable; this just chains them.
**A dictionary is not "done" until its `assets_pipeline.json` ledger is green.**

Still-true contract points (unchanged): `stamp_meta --status staged` keeps ids **provisional**
until a model is trained (`frozen`/`locked`); downstream binds to **surfaces**, not raw ids.
`domain`/`register` in meta L2 remain reserved (empty) — topic semantics would be a new tagger
stage, not a wiring fix.

---

## 6. Fresh-machine reproduce (start to a published bundle)

```powershell
# [venv: yes — root]   from R-D-concepts/
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.prefix)"          # must end \R-D-concepts\.venv (uv venv)

# Build core + full asset cascade in ONE command (embed stage wants the GPU)
python -m semantic_compression.build_dictionary semantic_compression/builds/<name>.yaml --device cuda

# Confirm the matched set + gates (every stage SKIP == fresh + consistent; ledger green)
cd semantic_compression
python build_assets.py db/builds/<name> --dry-run
type db\builds\<name>\assets_pipeline.json

# Publish: assemble the bundle, run gates G1-G8, write BUNDLE.json (refuses on any failure)
python publish_dictionary.py db/builds/<name> --dry-run     # preview gates, write nothing
python publish_dictionary.py db/builds/<name>               # -> dist/dictionary/<name>/
```

Inputs required (all present today): `data/word_frequencies.txt`,
`data/web_terms_frequencies.txt`, `data/phrase_candidates.txt`, `Resources/books/`,
`Memory/data/epa_substrate.lmdb`, `Memory/data/concreteness_substrate.lmdb`, and the
mpnet model for stage 5 (`--device cuda`).

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
