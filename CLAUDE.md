# semantic_compression — Dictionary & Codec Memory (read first)

> The ELO dictionary + compression substrate (System-1 Perception). This file is the
> quick orientation so we don't re-derive it. **Read these docs before building a
> dictionary:** `docs/compression/HANDBOOK.md` (plain-language), `GUIDE-create-dictionary.md`
> (how-to), `spec-v0.4.md` (LLM contract), `spec-vocab-strategy.md` (selection),
> `spec-dict-testgroups.md` (pick the strongest build), `perf-log.md` (codec perf).
> Cross-project consolidation: `../ELO-Browser/docs/DICTIONARY-REFERENCE.md`.

---

## How a dictionary is built (don't reinvent this)

- **One YAML spec fully defines a build.** Author it in `builds/<name>.yaml`, then:
  `python -m semantic_compression.build_from_spec semantic_compression/builds/<name>.yaml`
- The resolver hashes every corpus source into a **corpus fingerprint**, builds the
  package, derives facets (if `with_facets`), runs `meta_builder` → `meta.db`, and
  writes a self-contained package under `db/builds/<name>/`:
  `dictionary.lmdb` (forward/reverse/facets) · `meta.db` · `token-ids.csv.gz` ·
  `special-tokens.json` · `profile-cuts.json` · `byte-fallback.csv` · `manifest.json`.
- **Engine = `dictionary_builder_v03.py`** (current, despite the name). Facets =
  `facet_builder.py`/`facets.py`; per-surface semantics = `meta_builder.py` (keyed by
  **surface**; IDs are provisional). EPA columns are reserved (filled by the embedding
  layer in `library_builder.py`, which needs spaCy/faiss — `build_from_spec` does NOT
  call it).
- **meta.db carries a `complement` column** (verb subcategorization; canonical table
  `verb_complements.py`) — the POS-resolution instruction set the extraction pipeline
  reads to disambiguate "to X" homographs (`seem`→to_infinitive vs `map`→to_noun).
  Deterministic, re-derived per build, in `meta_fingerprint`. Spec: `spec-meta-db.md`.

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

- v0.3 = first LLM vocab contract, **LOCKED** (a model is trained on it).
- v0.4 = latest general dictionary (`db/builds/general_v0.4_char4`), **staged**.
- `elo-browser-v01` = v0.4 general + website/HTML fold-in (`builds/elo-browser-v01.yaml`).
- **Lock is per (dictionary, model) pair** (`stamp_meta.py`: staged|frozen|locked).
  Facets/meta are **additive** — re-derive freely; they never alter forward/reverse.

## LLM track

- `../llm-training/`: `elo_tokenizer.py` (HF `PreTrainedTokenizer`), `build_tokenizer.py`,
  `prepare_training_data.py`, `init_embeddings.py`, `train.py`. **The LLM tokenizer
  MUST match `tokenizer.py` char-class logic** (spec-v0.4 RC4) — same implicit-ws +
  caps + byte-fallback as the codec, or ids/counts won't match the model.

## Gotchas

- **Overwrite builds fail if the LMDB can't be unlinked** (and refused if `locked`).
  From a restricted/sandboxed FS, build to a **fresh `<name>`** instead of overwriting.
- Validate every build: `verify_lossless.py` (byte-exact), `verify_facets.py`,
  `bench_dict_efficiency.py` (OOV + ratio on held-out). Guardrails: G1 round-trip
  byte-exact · G2 tier capacity · G3 deterministic fingerprint.
