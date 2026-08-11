# semantic_compression — Process (prototype → export)

> The per-concept runbook for the System 1 dictionary / compression / facets
> concept: how to **prototype, build, deploy, test, validate, and export-for-share**.
> Catalog entry: [`../MASTER_CATALOG.md`](../MASTER_CATALOG.md). Status doc:
> [`SYSTEM1.md`](SYSTEM1.md). Last updated: 2026-06-19.
> **New here? Start with the step-by-step [Dictionary Create Guide](docs/compression/GUIDE-create-dictionary.md).**

All commands run from the `semantic_compression/` directory unless noted. The
export steps run from the workspace root (`R-D-concepts/`).

---

## 0. Current state (read first)

- Release **v0.4.0 — Semantic Facets Layer, `dictionary_status = staged`**.
- The dictionary IDs are **provisional**: being re-selected across 5–10 test
  groups (char-2/char-3); the char-4 build is deferred to stabilization. **Facets
  are re-derived on every dictionary build.** Nothing downstream should persist
  raw IDs until status flips to `frozen` (see §7).
- LMDB `db/dictionary.lmdb` has 4 sub-DBs: `forward`, `reverse`, `facets`, `meta`.

---

## 1. Prototype process — the build cascade

Each step gates the next. A change near the top invalidates everything below it,
so re-run downstream after any dictionary rebuild.

```
(1)  INGEST (MULTI-SOURCE)  source adapters -> SourceRecords (transcripts, wikipedia, …)
(2)  COUNT (POOLED)         per-source counts + weighted merge + dispersion
(3)  MINE PHRASES           PMI / maximal-phrase candidates
(3b) WORD BASE (authority)  topic-labeled inclusion list (coverage; not frequency)
(4)  SELECT VOCABULARY      select_strategy + max_tier (size = char-2/3/4); test groups
(5)  BUILD DICTIONARY       frequencies + phrases -> tiered Base64 IDs -> LMDB
(6)  EMBED / VECTORS        embeddings -> FAISS + EPA (vectors)
(7)  DERIVE FACETS          deterministic facet record per entry (re-run every build)
(8)  LLM PROFILES           vocab profiles + tokenizer artifacts
(9)  RETRAIN LLM            llm-training/ (separate track)
```

Owning scripts (v0.4 corpus front-end + builder hooks):

| Step | Script(s) | Output |
|---|---|---|
| 1 | `source_adapters.py` (`transcripts`), `wikipedia_adapter.py`; contract = `SourceAdapter` | `SourceRecord` streams (per-source, deduped) |
| 2 | `pool_counter.py` (multi-source, weighted) | `data/word_frequencies.txt` + `data/word_frequencies_sources.tsv` (per-source + dispersion) |
| 3 | `phrase_miner.py`, `ngram_counter.py` | `data/phrase_candidates.txt` |
| 3b | `wiktionary_categories.py` (authority/coverage) | `data/wordbase_*.tsv` (term · topics · source) |
| 4 | builder `select_strategy` (default `score_by_frequency`) + `--max-tier`; harness `docs/compression/spec-dict-testgroups.md` | chosen config |
| 5 | `dictionary_builder_v03.py` (`--max-tier`, `--with-facets`) | `db/dictionary.lmdb` (forward/reverse), `db/dict_stats_v03.json` |
| 6 | `library_builder.py` (embeddings/EPA/FAISS) | `db/faiss.index`, `db/canonical.db` |
| 7 | `vfacet_builder.py` — **now cascade stage 13** (`build_assets.py`), no longer hand-run (2026-08-10) | `b'vfacets'` sub-DB (deterministic half: polarity, temporal, domain) + `vfacets_stats.json` |
| 7b | `vfacet_llm.py` — enrichment, deliberately NOT in the cascade (needs an LLM; a build stage must reproduce offline) | agency + direction fill; flips `llm_enriched=true` in `vfacets_stats.json` → see [`docs/compression/GUIDE-vfacet-llm.md`](docs/compression/GUIDE-vfacet-llm.md) |
| 8 | `generate_essentials.py` + builder artifacts | `data/*-v1.csv(.gz)/.json` |
| 9 | `llm-training/` (`build_tokenizer.py` → `train.py`) | tokenizer + checkpoints |

> Sources are **pooled, not concatenated**: each source has a weight; per-source
> counts give **dispersion** (robust vs one-source spike). Authority sources
> (Wiktionary) feed an **inclusion list** (coverage), not the frequency pool.
> Design: `docs/compression/spec-corpus-sourcing.md`. Dictionary **size = build
> depth** (`max_tier`: char-2/char-3/char-4); selection is pluggable
> (`select_strategy`). Design: `docs/compression/spec-dict-testgroups.md`.

---

## 2. Build

```bash
# (1-2) pooled multi-source counts (weighted); --canonical writes the builder input
python -m semantic_compression.pool_counter --source transcripts:1.0 --source wikipedia:0.5 --canonical
# (3b) authority word base from Wiktionary categories (offline sample; --live needs network)
python -m semantic_compression.wiktionary_categories --root en:Sciences

# (5) dictionary — words + phrases; optionally facet in the same run
python dictionary_builder_v03.py --min 1 --tier1-reserve 1024 --with-facets         # char-4 (full)
# sized builds (size = tier depth):
python dictionary_builder_v03.py --max-tier 1 --with-facets                          # char-2 (app/edge)
python dictionary_builder_v03.py --max-tier 2 --with-facets                          # char-3 (medium)

# (7) facets only (re-run after any dictionary rebuild; idempotent, ~2s)
python facet_builder.py --db db/dictionary.lmdb --overrides data/facet_overrides.tsv

# stamp / re-stamp the release label + lifecycle status (no re-facet; fingerprint
# unchanged). Use 'frozen' only at stabilization.
python stamp_meta.py --release v0.4.0 --status staged --version 4

# (8) LLM-facing frozen artifacts
python generate_essentials.py
```

The dictionary build keeps `forward`/`reverse` authoritative; `facet_builder`
reads them and writes only `facets`+`meta` (never mutates forward/reverse).

---

## 3. Test

Per-module harnesses (run directly) + unit tests:

```bash
python verify_config.py        # charset/primitives/tier invariants
python verify_tokenizer.py     # ''.join(tokenize(s)) == s
python verify_caps.py          # cap/OOV codec round-trip
python verify_library.py       # tiers, IDs, EPA range, FAISS self-NN
python verify_compressor.py    # encode/decode shape
python verify_lossless.py      # full byte-exact round-trip (13/13 samples)
python verify_facets.py          # facets gate suite (T1/T3/T4/T5/T6/T9/T10/T12)
python test_facets.py            # facets unit tests (temp LMDB, corruption, overrides)
python test_decoder_cache.py   # cached vs uncached decode parity
# v0.4 corpus front-end + builder (run from repo root, `python -m semantic_compression.<t>`):
python -m semantic_compression.test_source_adapters     # C1 adapter contract (transcripts reproduces scan_single)
python -m semantic_compression.test_pool_counter        # C2 pooled counts reproduce; weighted merge + dispersion
python -m semantic_compression.test_wikipedia_adapter   # C3 wiki cleaner + 2-source dispersion
python -m semantic_compression.test_wiktionary_categories  # authority word-base harvest
python -m semantic_compression.test_builder_strategy    # select_strategy + max_tier (size) gating
```

`verify_facets.py --db <path>` runs against any faceted dictionary; `--pure-only`
skips the DB layer.

---

## 4. Validate (gates before calling a build good)

```
[ ] Round-trip byte-exact: verify_lossless.py passes (decode(encode(b)) == b).
[ ] Facets coverage: facets_total == forward count; all values in-enum (verify_facets T4/T6).
[ ] Isolation: tagging left forward/reverse byte-exact (content hash unchanged).
[ ] Reproducible: two builds from identical inputs -> identical fingerprint (T10).
[ ] Identity: meta has versions + fingerprint; verify_fingerprint() == stored (T5).
[ ] Consumer safety: not/if/unless/because/but keep a logic cue (T12).
```

For high-stakes/release builds, run the calibrated suite from the R-D test
platform (PipelineLab) in addition to the local harnesses.

---

## 5. Deploy (local artifacts)

The concept "deploys" by producing artifacts other layers read:

```
db/dictionary.lmdb        LMDB: forward/reverse/facets/meta (runtime dictionary)
db/faiss.index            vector index (vectors layer)
db/canonical.db           legacy SQLite (deprecated, kept for ref)
data/token-ids-v1.csv.gz  frozen vocab contract  ┐
data/special-tokens-v1.json                        │ the data/ contract boundary —
data/byte-fallback-v1.csv                          │ versioned, never edited in place
data/profile-cuts-v1.json                          ┘
data/facet_overrides.tsv    human-editable facet overrides (input)
```

These are read by `llm-training/`, `PipelineLab/`, and the runtime. Never modify a
published artifact in place — bump the version.

---

## 6. Export for share (graduate to a package)

`semantic_compression` graduates into two uv-workspace packages (see
`elo-dev/ELO_PACKAGING_STANDARD.md`):

```
compression-dictionary       block  (shareable)  config + caps_codec + tokenizer
eloai-semantic-compres
---

## v0.4 additions (2026-06-22)

- **Spec-driven build:** `build_from_spec.py <spec.yaml>` resolves corpus (+ fingerprint),
  builds the sized package, derives facets, and stamps the `artifacts` registry.
- **EPA match (new cascade step):** after facets, `epa_match.py` joins the global EPA
  substrate (`Memory/mneme/substrate/epa_substrate.py`) to dictionary IDs → id-keyed
  `b'epa'` + coverage; phrase EPA via composition. Substrate is versioned/fingerprinted.
- **Meta:** `meta_fields.py` (System-1 deterministic) → `meta.db` (spec-meta-db.md, pending wire-in).
  Includes the **`complement`** column (verb subcategorization from `verb_complements.py`):
  the POS-resolution instruction set the extraction pipeline reads to disambiguate a
  "to X" homograph (`seem`→TO_INFINITIVE vs `map`→TO_NOUN). Deterministic, re-derived per
  build, part of `meta_fingerprint`.
- **Artifact identity:** `artifact_identity.py` writes `manifest.artifacts` (dictionary/
  facets/epa/meta/templates) with fingerprint+version+status+bound_refs.
- **Lock:** `stamp_meta.py --lock-for-model` → `locked` status; overwrite refused.
- Guide: `docs/compression/GUIDE-create-dictionary.md`.
