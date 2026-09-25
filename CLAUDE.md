# semantic_compression — Dictionary & Codec Memory (read first)

> The ELO dictionary + compression substrate (System-1 Perception). This file is the
> quick orientation so we don't re-derive it. **Read these docs before building a
> dictionary:** `docs/compression/HANDBOOK.md` (plain-language), `GUIDE-create-dictionary.md`
> (how-to), `spec-v0.4.md` (LLM contract), `spec-vocab-strategy.md` (selection),
> `spec-dict-testgroups.md` (pick the strongest build), `perf-log.md` (codec perf).
> Cross-project consolidation: `../ELO-Browser/docs/DICTIONARY-REFERENCE.md`.

---

## How a dictionary is built (don't reinvent this)

- **One YAML spec fully defines a build**, and **one command builds it end to end**:
  `python -m semantic_compression.build_dictionary semantic_compression/builds/<name>.yaml --device cuda`.
  That chains the CORE build (`build_from_spec`: dictionary + LLM profile cuts + the
  suite's facets/meta) and the ASSET CASCADE (`build_assets`: epa → meta_layer2 →
  vectors → browser export → registry → stamp → verify) for whatever `build.suite`
  declares. Both underlying stages stay independently runnable; `build_dictionary`
  just runs A then B. **A build is not "done" until its `assets_pipeline.json` ledger
  is green.** (`build_from_spec` alone is CORE-ONLY — it leaves epa/vectors/browser
  unbuilt; don't judge a package by a core-only manifest.)
- The resolver hashes every corpus source into a **corpus fingerprint**, builds the
  package, derives facets (if `with_facets`), runs `meta_builder` → `meta.db`, and
  writes a self-contained package under `db/builds/<name>/`:
  `dictionary.lmdb` (forward/reverse/facets[/epa]) · `meta.db` · `token-ids.csv.gz` ·
  `special-tokens.json` · `profile-cuts.json` · `byte-fallback.csv` · `manifest.json`.
- **Engine = `dictionary_builder_v03.py`** (current, despite the name). Facets =
  `facet_builder.py`/`facets.py`; per-surface semantics = `meta_builder.py` (keyed by
  **surface**; IDs are provisional). **EPA is filled by the cascade** — `epa_match.py`
  (stage 3) joins `Memory/data/epa_substrate.lmdb` into the `epa` sub-DB; a core-only
  build has no epa. The registry (stage 9) is **re-derived after the cascade**, so
  `artifacts.epa.present` reflects the finished package, not a pre-epa snapshot.
- **meta.db carries a `complement` column** (verb subcategorization; canonical table
  `verb_complements.py`) — the POS-resolution instruction set the extraction pipeline
  reads to disambiguate "to X" homographs (`seem`→to_infinitive vs `map`→to_noun).
  Deterministic, re-derived per build, in `meta_fingerprint`. Spec: `spec-meta-db.md`.
- **Building ≠ publishing.** `publish_dictionary.py` (stage 12, a SEPARATE verb)
  assembles ONLY the shippable bundle (`<build>.browser.json` + facets/epa/neighbours
  `.bin` + `facets.names.json` + `BUNDLE.json`), runs gates G1–G8, and refuses on any
  failure. Specs: `docs/compression/spec-publish-dictionary.md`, `DICTIONARY-BUILD-RUNBOOK.md`.
- **Per-build knobs the v01a→v01c builds added:** `expand_tiers: true` widens the id
  leading-char alphabet 20→63 (+3.15× tier capacity); browser assets land in a
  **per-build** subfolder `dictionary/<build>/`, never loose in the root; a rebuild is
  a **new build name** (or `--force`/overwrite the same one). Directions table:
  `DICTIONARY-BUILD-RUNBOOK.md`.

## Sizes, tiers, profiles (commonly confused)

- **Size = tier depth** of the built artifact: char-2 (~1,333) · char-3 (~83,253) ·
  char-4 (up to ~5.25M). First ID char encodes the tier. **char-3 caps ~83k** — a
  262k vocab is a **char-4 build**.
- **Profile = a frequency-rank cut** of one dictionary for an LLM embedding budget
  (`tiny/compact/standard/full/reference`; `full` = 262,144). Size ≠ profile.
- **There is no universal "full dictionary"** — each build is its own dictionary with
  its own fingerprint. Decode an `.elo` with the SAME dictionary it was encoded under.

## The codec (`compressor.py`, `.elo`/`.eloB`, ELO_BIN_VERSION 2)

- Tier-tagged variable-length ID stream + **implicit-whitespace** (drop the single
  space between two WORD tokens, reinsert at decode) + **cap-prefix** (`caps_codec`:
  store lowercase, marker restores case) + **byte-fallback** (OOV bytes → reserved
  ids; lossless at every size). Decode ~10 MB/s cached (`perf-log.md`).
- **Density is codec-driven.** Don't bake case/space variants into the dictionary.

## Versions & lifecycle

- **The standard is whatever `dist/dictionary/STANDARD.json` says.** As of 2026-09-25:
  `elo-v5`, `bundle_id: elo-v5r4`. Builds are named `elo-vN`, forward only (ruling
  2026-09-24). `elo-browser-v01`…`v04` and `general_v0.4_char4` are historical.
- **Lock is per (dictionary, model) pair** (`stamp_meta.py`: staged|frozen|locked) — and
  **no model is deployed, so no lock is in force.** Facets/meta are **additive** — re-derive
  freely; they never alter forward/reverse.

## LLM track — RETIRED (Paul, 2026-09-25)

> **There is no LLM contract.** "The LLM training is on hold. We are not using v1 anymore.
> That was months ago, and 9 dictionary builds in the past."

The v0.3 / `docs/v1/` vocabulary contract (`token-ids-v1.csv.gz` and the three `-v1`
siblings, frozen 2026-06-11) is **dead**. Nothing current binds to it. Consequences, stated
so nobody rebuilds against a corpse:

- `../llm-training/` (`elo_tokenizer.py`, `build_tokenizer.py`, the training scripts) is
  frozen with it. Do not run it, do not cite it as a requirement, do not "regenerate" from it.
- `ids.json` / the browser's `conformance_ids` tested that tokenizer. **Retire the test**; do
  not park or regenerate it. `gen_ids_oracle.py` refuses to run for this reason.
- The "LLM tokenizer MUST match `tokenizer.py`" rule has no subject until a tokenizer exists
  again. `tokenizer.py` remains the codec's tokenizer and is governed by the codec contract.
- **Profile cuts (`tiny`…`reference`) remain a live per-build concept** — `<build>.browser.json`
  is projected from them — but they are no longer "for an LLM embedding budget". They are
  frequency-rank cuts of a build, and nothing else.

When the LLM track resumes it starts from a **current** build, with a new contract, a new
tokenizer, and a new oracle. None of the v1 artifacts carry forward.

## HARD RULE — a size limit never drops words (2026-08-10)

When capacity binds, the overflow is taken from **non-lexical surfaces first** — tokens
present ONLY in non-lexical corpus sources (CSS names, JS classes, web-structure).
Provenance: `build_from_spec.resolve_corpus` tags each source (`transcripts`/`books` =
lexical; `web_structure` = not; per-source `lexical:` overrides) and emits
`data/nonlexical_terms_<name>.txt`; the builder evicts from that set, lowest-scored
first, ON TOP of whatever `select_strategy` ordered. **If the lexical candidates alone
exceed capacity, the build FAILS LOUDLY** — raise `size:` or set `expand_tiers: true`;
never ship a dictionary that silently dropped a word. Accounting: `evicted_nonlexical`
in dict_stats; `corpus_lexical_tokens` / `corpus_nonlexical_only_tokens` in the manifest.

## Gotchas

- **Overwrite builds fail if the LMDB can't be unlinked** (and refused if `locked`).
  From a restricted/sandboxed FS, build to a **fresh `<name>`** instead of overwriting.
- Validate every build: `verify_lossless.py` (byte-exact), `verify_facets.py`,
  `bench_dict_efficiency.py` (OOV + ratio on held-out). Guardrails: G1 round-trip
  byte-exact · G2 tier capacity · G3 deterministic fingerprint.
