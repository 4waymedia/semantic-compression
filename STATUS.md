# semantic_compression — Status

| | |
|---|---|
| **Status** | `develop` (dev source; graduates two packages) |
| **Liveness** | `active` — codec + dictionary build exercised 2026-08-29 |
| **Updated** | 2026-08-29 |
| **Owner lane** | semantic_compression / dictionary lane |
| **Spec** | `SYSTEM1.md` · `docs/compression/*` · `docs/format/ELO_FILE_FORMAT.md` + `docs/format/spec-elo-dictionary-binding.md` |
| **Package** | graduates **two**: `packages/elo-dictionary/` 0.3.1 (block, publishable) · `packages/elo-compression/` 0.3.1 (system, private) |

> This is a **git submodule** — changes here need a submodule commit **plus** a parent
> gitlink bump. Import names are frozen (`compression_dictionary`, `eloai_semantic_compression`);
> only the distribution names moved to `elo-*` (2026-08-07 naming change).

## 1. What this is

The ELO dictionary + compression substrate (System-1 Perception): builds a Base64 canonical
dictionary from a corpus, mines phrases, derives per-surface facets/EPA/meta, and encodes/decodes
text to the `.elo`/`.eloB` container losslessly. It is the root object the rest of the stack
is built around. It does **not** do runtime reasoning, memory, or LLM inference — it produces
the ID space and the coupled asset family that downstream systems consume.

## 2. Current build

| component | file | state |
|---|---|---|
| Base64 dictionary + tiers | `dictionary_builder_v03.py`, `library_builder.py` | built |
| Phrase mining + hygiene | `phrase_miner.py`, `ngram_counter.py` | built |
| Facets (bucket · cue · utility) | `facets.py`, `facet_builder.py`, `facet_reader.py` | built |
| EPA join (from mneme substrate) | `epa_match.py` | built — ⚠ `sys.path` reach-in (§8) |
| meta.db (POS / complement / layer-2) | `meta_builder.py`, `meta_layer2.py` | built |
| Codec `.elo`/`.eloB` | `compressor.py` | built — **now dictionary-bound (§ below)** |
| Build family driver + asset cascade | `build_dictionary.py`, `build_from_spec.py`, `build_assets.py`, `build_suite.py` | built |
| Artifact identity / manifest | `artifact_identity.py` | built |
| Publish (stage 12) | `publish_dictionary.py` + `docs/compression/spec-publish-dictionary.md` | built — gates G1–G8, refuses on failure |
| **Standard pointer + index** | `dictionary_standard.py` → `dist/dictionary/{STANDARD,INDEX}.json` | **built 2026-08-29** |
| **Resolver (the one door)** | `resolve.py` → `compression_dictionary.resolve_dictionary()` | **built 2026-08-29** |
| **Resolution gate** | `check_dict_resolution.py` + `tests/test_dict_resolution_gate.py` | **built 2026-08-29 — 0 violations, `--strict` passes** |
| Word class channel | `wordclass_builder.py` (`b'wordclass'`, 3 B) | built — run by hand, **not yet a cascade stage** |
| Coverage census (stage 14) | `coverage_census.py` | built — always-run, fingerprint-bound |

### 2026-08-29 — the dictionary standard, and the drift it exposed

**Promoted standard: `elo-browser-v04`** (`b0164e50816af845`, v4.0.0, staged) via
`dist/dictionary/STANDARD.json`. Consumers resolve through
`compression_dictionary.resolve_dictionary()`; precedence is `ELO_DICT` → the pointer →
a named `DictionaryUnavailable`. **There is no default** — a fallback is how eight lanes
ran on `9a77e623`/v1.2.0, an orphan matching no build package, with identical row counts
(437,995) hiding it.

- Resolution gate: **37 → 0** violations across 8 lanes; `--strict` exits 0.
- Export drift repaired: `elo-dictionary` and `elo-compression` now `CHECK: clean`.
  `resolve.py` + `dictionary_info.py` added to the export include list — they existed in
  **both** trees and **neither** include list, so nothing kept them equal (and
  `dictionary_info.py` had already diverged).
- **The gate had a false clean.** It did not scan the dev source, so it reported 0
  violations while `compressor.py`'s source still hardcoded the orphan path — the repoint
  had landed only in the *exported* copy, and the next `--apply` would have reinstated the
  bug. The dev source is now scanned by default, with V1 scoped to module-level defaults
  so build tooling composing a path from a parameter is not flagged.
- Three further dev-source defaults repointed: `epa_phrase_composer.py`,
  `vfacet_context_classifier.py`, `vfacet_llm.py`.

**Word class corrections (same day):** `dominant` is now argmax over per-class evidence,
not a fixed priority — `dog`/`stone`/`water`/`run`/`house` were byte-identical `54 03 40`
(VERB at CORPUS confidence). Comparatives/superlatives fixed (`morph_class('faster')`
returned NOUN); plurals inherit the singular's resolved class (10,074). Coverage: all-zero
lowercase-alphabetic 127,713 → 121,133; frequent unclassified 481 → 393.

### 2026-08-29 (later) — byte 2 repaired, `wordclass_format_version` → **2**

**BREAKING for readers.** `proper` and `requires_determiner` were single bits and are now
2 bits each, paid for from the two reserved bits. A v1 reader against a v2 record takes
`proper` from the wrong bits and reports a boolean for a tri-state, so **check the
version**. `04-Verbalizer/reader.py` and its package mirror are updated.

```
byte 2  [7:6] countability  [5:4] inherent_number  [3:2] proper  [1:0] requires_determiner
                                                   0 UNKNOWN · 1 NO · 2 YES
```

The bugs, all measured, all mine:

| | was | now |
|---|---|---|
| `requires_determiner` | defined, read, **never assigned** — 437,995 confident falses | YES 5,267 · NO 4,663 · rest UNKNOWN |
| `proper` | capitalisation **shape**: flagged `Abate`/`Able`, missed `israel`/`washington` | YES 9,837 · NO 31,141, from mid-sentence capitals |
| `countability` | 32,781 COUNT, **0 MASS** — `water` read COUNT (*"a water") | COUNT 8,373 · MASS 190 · BOTH 412 |

A single-bit boolean cannot express UNKNOWN, so both fields violated the record spec's
own property #1 from the day they were written. That is the root cause, not the symptom.

**Coverage went DOWN and that is the fix**: COUNT fell 32,781 → 8,373 because the blanket
"every noun is COUNT" assertion is gone. Countability is now known for 2% of entries and
honestly absent for the rest, instead of confidently wrong for all of them. Evidence comes
only from the 26-book cased corpus, so the ceiling is low until a larger cased source
lands. Known limits recorded in `tests/test_wordclass_features.py`: `music` reads BOTH
because noun-noun compounds ("a music teacher") contaminate the COUNT cue.

### 2026-08-29 — CORRECTION: `agency`/`direction` were not defaulted, I misread them

I reported `agency` as *"a default wearing coverage's clothes"* and `direction` as
*"46.9% OUTWARD"*. **Both statements were wrong, and the error was mine.**

`OUTWARD` is not in the direction vocabulary. The writer's enum is
`UNKNOWN·TOWARD·AWAY·STABLE·REVERSAL·NEUTRAL`, so value 5 is **NEUTRAL** and value 3 is
**STABLE**. `direction` is 68.2% NEUTRAL — a legitimate "no directional force" verdict,
not an unexplained default. Every decode table actually in the tree
(`vfacet_builder`, `verbalizer/reader.py`, `08-MCP/_probe_assent.py`) was already
correct and consistent; **the only wrong copy was the one I typed into an analysis
script**, and I reported its output as a finding.

`agency` is 92% OTHER among the 20% of qualifying surfaces that have a value. That
concentration is real, but *concentration is not evidence of defaulting* — the two look
identical from outside, and separating them needs ground truth this build has none of.
Calling it a default was an unsupported claim, not a measurement.

**Fix, addressing the class rather than the instance:** the census printed raw integers,
which required every reader to supply a mapping — so it invited exactly this. It now
imports value names from the modules that WRITE the records, and the saturation banner
reads `CONCENTRATION NOTICE … worth a look, NOT a verdict` instead of asserting
"a default, not data".

```
direction  {NEUTRAL: 40,987, STABLE: 20,899, AWAY: 4,830, TOWARD: 2,121, REVERSAL: 62}
agency     {OTHER: 12,684, SELF: 946, SYSTEM: 133}
```

> **Not done:** `wordclass` is not a cascade stage; no gold set with a pre-registered bar;
> `inherent_number` still has zero SG, so the pronoun path stays dormant; whether
> `agency`'s skew is real remains **UNVERIFIED**; generation morphology (step 2) is
> **blocked** on coverage, not on dominance.

**New 2026-08-07 — the `.elo` container names its own dictionary.** `FORMAT_VERSION` → **2**
(text) / `ELO_BIN_VERSION` → **3** (binary): the header now carries `build_id` + the dictionary
**fingerprint**. Decode verifies and raises `DictionaryMismatch` on a wrong dictionary; legacy
v1/v2 files still decode with a warning. Spec: `docs/format/spec-elo-dictionary-binding.md`.
`FORMAT_VERSION` was also decoupled from the corpus counters (new `COUNTS_FORMAT_VERSION`).

## 3. Current output

| artifact | shape | consumed by |
|---|---|---|
| dictionary build family | `db/builds/<name>/` — `dictionary.lmdb`, `facets.bin`, `epa.bin`, `neighbours.bin`, `meta.db`, `manifest.json` (fingerprint-bound) | ELO Browser, 06/`elo-recall`, verbalizer, llm-training |
| `.elo` / `.eloB` streams | dictionary-bound container (`build_id` + fingerprint header) | ELO Browser, any reader with the matching dictionary |
| `elo-dictionary` package | base block (config, codec primitives, facets, tokenizer) | `elo-compression`, `elo-verbalizer`, 06 |
| `elo-compression` package | codec/facet-reader surface (consumes `elo-dictionary`) | products (private layer) |

Active shipped build: **`elo-browser-v01c`** (status `staged`). Identity is read from each
build's own `manifest.json` / `assets.meta.json`; never restated by consumers.

## 4. Usage

```powershell
cd F:\Script-Projects\elo-dev\elo_dev\R-D-concepts
# build a dictionary family (one YAML -> core + asset cascade)
$env:PYTHONPATH="."; python -m semantic_compression.build_dictionary semantic_compression\builds\<name>.yaml --device cuda
# encode / decode
python -m semantic_compression.compressor encode <input> <output.elo>
python -m semantic_compression.compressor decode <input.elo> <output>
```

## 5. Tests

```powershell
cd F:\Script-Projects\elo-dev\elo_dev\R-D-concepts
$env:PYTHONPATH=".;semantic_compression"; python semantic_compression\verify_compressor.py   # expect: 10/10 byte-exact
python packages\elo-dictionary\export-package.py --check      # expect: CHECK: clean
python packages\elo-compression\export-package.py --check     # expect: CHECK: clean
```

| suite | count | last run | result |
|---|---|---|---|
| `verify_compressor.py` (round-trip) | 10 formats | 2026-08-07 | ✅ 10/10 byte-exact |
| `elo-dictionary` drift gate | `--check` | 2026-08-07 | ✅ clean |
| `elo-compression` drift gate | `--check` | 2026-08-07 | ✅ clean |
| `verify_lossless.py` (byte-exact) | corpus samples | see file | run per build |

## 6. Dependencies

| depends on | mechanism | note |
|---|---|---|
| `mneme` (Warriner EPA substrate) | ⚠ **`sys.path` reach-in** (`epa_match.py:27-36`) | should consume the `elo-memory`/`mneme` package; cleanup pending (§8) |

**Depended on by:** ELO Browser (dictionary bundle), 06 / `elo-recall`, verbalizer
(`elo-verbalizer` optional `discourse` extra → `elo-dictionary`), llm-training (vocab contract),
`elo-compression`.

## 7. Package + export

| | |
|---|---|
| Packages | `elo-dictionary` 0.3.1 (block, `publish=true`) · `elo-compression` 0.3.1 (system, `publish=false`) |
| `export-package.py` | **present on both** (elo-dictionary = curated subset; elo-compression = curated subset, dual-namespace transform) |
| `--check` (drift) | **green on both** (2026-08-07) |
| In `[tool.uv.sources]` | yes — `elo-dictionary`, `elo-compression` |
| Published to elo-dev | not yet (stage-12 publish pending) |

> Development happens **here**. `packages/*` is generated output — never edit it directly
> (Standard §2, §13). `[tool.eloai]` has been renamed `[tool.elo]` on both packages.

## 8. Known debt

| item | impact | pointer |
|---|---|---|
| `epa_match.py` reaches into mneme via `sys.path` | invisible coupling; breaks if mneme moves | `epa_match.py:27-36` · `handoffs/HANDOFF-mneme-base-block-and-epa-consumer.md` |
| **`dictionary_fingerprint` key mislabelled** | `tools/systems.toml [systems.dictionary_build]` and 5 downstream files store the **facets hash** (`9a77e623…`), not the dict hash (`4335d939…`). Harmless until facets are rebuilt independently without a full dict rebuild — then the fingerprint check passes a stale pairing | `tools/systems.toml` `[systems.dictionary_build]`; correct when keys are next touched |
| Stage-12 publish (R-D) not built | no publish step → every build is `staged`; five fingerprints disagree on `elo-browser-v01c` | `docs/compression/spec-publish-dictionary.md` §6 (needs two owner calls: raw vs `.tar.zst`; one bundle vs per-cut) |
| `ELO_FILE_FORMAT.md` diverged from the codec | paper spec describes a 64-byte header / `dict_version` / `0x1F` delimiter the code never used | `docs/format/spec-elo-dictionary-binding.md` §6 |
| `eloai_semantic_compression.data` reserved-noun | conformance warn (generic segment) | naming doc §4.2 — rename when convenient |
| Stale ELO-Browser loose `.bin`/`.json` copies | identity that can disagree with the bundle | ELO-Browser lane (repoint `bindings.toml` then delete) |

## 9. Changelog

| date | change |
|---|---|
| 2026-08-10 | **`vfacets` promoted to build stage** — previously run by hand; now a proper declared stage in the build pipeline. See `handoffs/2026-08-10-*.md`. |
| 2026-08-07 | `.elo`/`.eloB` header dictionary-binding (`FORMAT_VERSION` 2 / `ELO_BIN` 3); `COUNTS_FORMAT_VERSION` split off; `facet_builder` data path made module-relative |
| 2026-08-07 | packages renamed `compression-dictionary`→`elo-dictionary`, `eloai-semantic-compression`→`elo-compression` (dist names; import names frozen); `elo-compression` gained its `export-package.py` drift gate |
| 2026-08-05 | verbalizer graduated out to base `verbalizer` package; ops/response/conformance folded; packageability report authored |
