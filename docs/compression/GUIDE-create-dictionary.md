# Dictionary Create Guide

> How to create a new System-1 dictionary, start to finish. Task-oriented and
> spec-driven: a single YAML **build spec** declares everything, and one command
> turns it into a self-contained, provenance-stamped dictionary package.
>
> Companion docs: process/runbook → [`../../PROCESS.md`](../../PROCESS.md);
> selection + expansion theory → [`spec-vocab-strategy.md`](spec-vocab-strategy.md);
> sourcing → [`spec-corpus-sourcing.md`](spec-corpus-sourcing.md); test-group
> comparison → [`spec-dict-testgroups.md`](spec-dict-testgroups.md);
> the derived-asset cascade → [`spec-asset-pipeline.md`](spec-asset-pipeline.md);
> what a build *is* + how a consumer reads it → [`SPEC-BUILD-FAMILY.md`](SPEC-BUILD-FAMILY.md).
> Last updated: 2026-07-10.

---

## Quickstart — build a dictionary in three steps

Everything below is elaboration. To just build one (PowerShell, from the repo root
`R-D-concepts/`):

```powershell
# 1. Author the spec: copy an existing one and edit meta.name + corpus + build.suite.
copy semantic_compression\builds\elo-browser-v01.yaml semantic_compression\builds\my-dict.yaml

# 2. Preview what will build (the resolved asset suite; builds nothing):
python semantic_compression\build_suite.py semantic_compression\builds\my-dict.yaml

# 3. Build the dictionary AND its declared suite in one command:
python -m semantic_compression.build_dictionary semantic_compression\builds\my-dict.yaml
#    --dry-run to preview the full plan · --device cuda for the embedding stage
```

`build_dictionary` runs the **core** (dictionary + LLM profile cuts + the suite's
facets/meta) then the **asset cascade** (epa → meta_layer2 → vectors → browser →
stamp → verify) for whatever `build.suite` declares. The package lands in its own
folder, `db/builds/<name>/`, and never touches other builds.

**The three knobs** you set in the spec: **size** (`build.size` — how deep, §1a),
**corpus** (`corpus:` — what vocabulary exists, §2), **suite** (`build.suite` — what
ships: `minimal`/`standard`/`full`, §3b). Validate every build with §5.

**Worked example (this repo):** `builds/elo-browser-v01a.yaml` is a `standard`-suite
char-4 build (dictionary + facets + **meta.db**); build it with
`python -m semantic_compression.build_dictionary semantic_compression\builds\elo-browser-v01a.yaml`.

---

## 0. Mental model — the three choices

Every dictionary is defined by three decisions. They are also the three controls
the planned **web GUI** will expose, and they map one-to-one onto the build spec:

```
1. BASE          how deep + the core register      -> build.size + the base corpus source
                 ("select the base")
2. EXPANDED      extra knowledge to widen coverage  -> additional corpus sources
   KNOWLEDGE     ("select expanded categories")        (books, wikipedia, wiktionary topics)
3. FINE-TUNE     how the scarce IDs are spent        -> select_strategy, reserves, min_freq,
                 ("other selections")                   facets, held-out eval
```

Keep these separate in your head: **base + expanded = coverage** (what vocabulary
exists), **fine-tune = selection** (which vocabulary earns the short IDs). Today
you edit the spec by hand; later the GUI writes the same spec from your clicks.

---

## 1. Decide the build

### 1a. Size (build depth)

A dictionary's size is how deep into the ID tiers it is built. Capacity is fixed
per tier (`config.py`):

| size | tiers | ID width | cumulative entries | use case |
|---|---|---|---:|---|
| `char-2` | 0–1 | 1–2 char | ~1,333 | tiny: keyboard / edge |
| `char-3` | +2 | 3 char | ~83,253 | medium: on-device, apps |
| `char-4` | +3 | 4 char | up to ~5.25M | large: LLM / max atomicity |

Entries are **words + phrases combined** (they compete in one ranking). Anything
past ~83k entries (e.g. 135k or 235k words) is a char-4 build using part of Tier 3.

Word first chars are `g–z` (20 slots, `config.py:TIER_WORD_FIRST_CHARS`), so char-4
tops out at Tier 3 capacity **5,242,880 words** (`TIER_CAPACITY`). Going deeper — a
`char-5`/Tier-4 **word** depth — is **not wired**: Tier 4 is currently the phrase
partition (`-` prefix), so a larger word space needs a first-char-scheme change in
`config.py` + a `dictionary_builder_v03` branch, not just a spec value. Treat ~5.24M
words as the current per-dictionary ceiling.

### 1b. Selection strategy

| strategy | scorer | when to use |
|---|---|---|
| `frequency` (S0) | raw corpus count | **default.** Near-optimal for conversational/general text. |
| `bytes_saved` (S1) | `freq × (len − id_width)` | technical/literary corpora with long, rare words. |

**Measured guidance (validate per corpus):** on transcripts S0 ≈ S1 (±0.03%);
on technical prose S1 won ~+5%; on out-of-domain literary text S1 *raised* OOV and
lost. The three levers ranked by impact for a general dictionary: **coverage >
depth ≫ selection.** Spend effort on corpus, not the scorer, unless your domain is
long-word-heavy. Theory: [`spec-vocab-strategy.md`](spec-vocab-strategy.md).

---

## 2. Assemble the corpus

### 2a. Source library (physical folders)

```
Resources/transcripts/<channel>/*.json   conversational (have)
Resources/books/*.txt                     literary / reference (have)
Resources/wikipedia/ , wiktionary/ , …    encyclopedic / authority (planned)
```

Sources live in labelled folders permanently. A build *selects* from them; it does
not move them. License-tag every source in the spec.

### 2b. Base vs expanded knowledge

- **Base** = the core register at your chosen size (e.g. all transcripts).
- **Expanded knowledge** = extra sources that add vocabulary the base lacks. Adding
  books cut held-out-book OOV from 8.4% → 3.9% and improved ratio +9.3% — coverage
  is the dominant lever for out-of-domain text.
- **Authority/coverage (Wiktionary categories)** guarantees inclusion of
  correct-but-rare terms at a floor frequency without inflating counts; the future
  GUI's "knowledge categories" are Wiktionary `Category:en:*` subtrees
  (`wiktionary_categories.py`). See [`spec-corpus-sourcing.md`](spec-corpus-sourcing.md) §8.

### 2c. Held-out eval (avoid leakage)

Reserve some material the dictionary never sees, so efficiency numbers reflect
generalization, not memorization. The split is **logical**, declared in the spec
(`eval.held_out_books`), not physical — one `/books` library serves every build.
Never put a file in both the corpus and the eval.

---

## 3. Author the build spec

One YAML file fully defines the build. Copy an existing spec from
`semantic_compression/builds/` and edit. Every field below is real and consumed by
the resolver.

```yaml
meta:                       # documentation + identity (GUI: the "about" panel)
  name: general_v0.4_char4
  version: "0.4.0"
  date: "2026-06-20"
  author: "Your Name"
  llm_model: "elo-3B"       # target / bound model (optional)
  status: staged            # staged | frozen | locked
  purpose: > …
  goals: [ … ]
  use_cases: [ … ]

build:                      # FINE-TUNE (GUI: sliders/toggles)
  size: char-4              # char-2 | char-3 | char-4
  select_strategy: frequency  # frequency | bytes_saved
  min_freq: 1
  tier1_word_reserve: 1024
  suite: standard           # SUITE preset: minimal | standard | full  (§3b)
  assets:                   # OPTIONAL explicit overrides on top of the preset
    epa: true               #   e.g. add affect without going full
  # with_facets: true       # legacy toggle; still honoured (== assets.facets)

corpus:                     # BASE + EXPANDED (GUI: source checkboxes)
  - type: transcripts
    precomputed: semantic_compression/data/word_frequencies.txt
    weight: 1.0
    license: per-channel derived stats
  - type: books
    path: Resources/books
    include: "*.txt"
    weight: 1.0
    license: Public domain (Project Gutenberg / user-supplied)

eval:                       # held-out TESTS (GUI: "hold out for testing")
  harness: bench_dict_efficiency
  transcript_eval: { channels: semantic_compression/data/eval_channels.txt, top: 10 }
  held_out_books: [ moby_dick.txt, pride_and_prejudice.txt, … ]

instructions: >            # free prose: rationale, reviewer notes
  …
```

Notes: `precomputed` lets a huge source (the 186M-token transcript counts) enter
without recounting; raw-text sources (books) are tokenized and counted at build
time. `weight` controls the register mix (pool, don't concatenate). Files in
`held_out_books` are excluded from the corpus automatically.

---

## 3b. The asset suite — what ships with the dictionary

A dictionary is a **shipped product**, and different products need different
supporting assets: the browser ships facets + affect + neighbours; a keyboard ships
the bare dictionary; a per-language coding pack ships dictionary + meta. `build.suite`
(a preset) plus optional `build.assets` (explicit on/off) declare that set. One module
is the source of truth — `build_suite.py` — and the package `manifest.json` `artifacts`
block records what actually got built, so the declaration and the result always agree.

**Presets** (each adds to the one above):

| preset | assets | for |
|---|---|---|
| `minimal` | `dictionary` | edge / keyboard — smallest, fastest |
| `standard` *(default)* | `+ facets + meta` | normal build; apps, coding packs |
| `full` | `+ epa + meta_layer2 + vectors + browser` | the whole stack (browser, LLM, analysis) |

**Assets, in build order, with prerequisites:**

| asset | what | needs (declared) | needs (environment) |
|---|---|---|---|
| `dictionary` | the LMDB + LLM profile cuts (always on) | — | — |
| `facets` | deterministic affordance sub-DB | dictionary | — |
| `meta` | `meta.db` layer 1 (+`complement`) | dictionary | — |
| `epa` | affect sub-DB | dictionary | `epa_substrate.lmdb` |
| `meta_layer2` | fills epa/polarity/concreteness in `meta.db` | meta, epa | — |
| `vectors` | 768-d denotative index | meta | `sentence-transformers` + `faiss` |
| `browser` | `epa.bin` / `facets.bin` / `neighbours.bin` product export | facets, epa | `export_browser_assets.py` (neighbours also wants `vectors`) |
| `templates` | System 2 (reserved — never auto-enabled) | — | not built |

**Two prerequisite classes, two behaviours:**

- **Declaration deps fail fast.** Enabling `browser` without `epa` and `facets`
  raises at resolve time — a build never half-derives. Enable the missing asset or
  pick a higher preset.
- **Environment deps block softly.** If the EPA substrate or the embedding model
  isn't present, the asset is marked `blocked` with a reason and skipped; the rest of
  the build proceeds, and the registry records it `present:false`. This is the same
  degrade the driver (`build_assets.py`) reports as `SKIPPED (missing dep …)`.
  The heavy assets `vectors` + browser `neighbours` need `sentence-transformers` +
  `faiss-gpu` (+ a CUDA torch) — install those and run with `--device cuda`; without
  them the build still produces everything else and just skips the 768-d index.

Preview any spec's resolved suite before building:

```bash
python build_suite.py builds/<name>.yaml     # prints per-asset build/blocked/off + reasons
```

The legacy `build.with_facets` boolean still works (it just toggles the `facets`
asset), so every existing spec resolves unchanged — `elo-browser-v01` (`with_facets:
true`, no `suite:`) resolves to `standard` = dictionary + facets + meta, exactly what
it builds today.

---

## 4. Build it

```bash
# from the repo root (R-D-concepts/)
python -m semantic_compression.build_from_spec semantic_compression/builds/<name>.yaml
```

This resolves the corpus (hashing every source into a reproducible
`corpus_fingerprint`), builds the sized dictionary, derives facets if requested,
and writes a self-contained **package**:

```
db/builds/<name>/
  dictionary.lmdb        forward / reverse / facets / meta
  dict_stats.json        tier occupancy + build provenance
  token-ids.csv.gz       LLM vocab contract (this build's own copy)
  special-tokens.json  byte-fallback.csv  profile-cuts.json
  facets_stats.json
  manifest.json          the FULL spec + corpus fingerprint + per-source hashes
```

Each build co-locates its own artifacts, so builds never clobber each other.
(Lower-level: `dictionary_builder_v03.py --new-build <name>` makes a unique
timestamped package; `--build-dir <path> --overwrite` rebuilds in place but is
refused if the target is `locked`.)

`build_from_spec` builds the **core** — dictionary + profile cuts, plus facets + meta
when the suite declares them. The rest of the declared suite (epa, meta_layer2,
vectors, browser) is derived by the **asset cascade**, `build_assets.py`, which reads
the same package and rebuilds only stale assets:

```bash
python build_assets.py db/builds/<name>            # derive the declared suite
python build_assets.py db/builds/<name> --dry-run  # preview the plan
```

**The end-to-end command already exists** — `build_dictionary.py` runs core *then* the
cascade in one call (it's the Quickstart command), so you rarely invoke the two stages
separately. Use `build_from_spec` + `build_assets` directly only when re-deriving a
subset (e.g. re-run facets + browser after a facet fix). The cascade's stages, wire
formats, and correctness rules are the [asset-pipeline spec](spec-asset-pipeline.md); the
consumer-facing family contract is [SPEC-BUILD-FAMILY.md](SPEC-BUILD-FAMILY.md).

> **`facet_builder` needs `--overrides` explicitly.** Its default overrides path is
> relative to the current directory, so running it by hand *without* the flag silently
> loads **0 overrides** and produces a worse dictionary (e.g. `the` mis-tagged) with no
> error. `build_dictionary`/`build_assets` pass it correctly; a manual `facet_builder`
> call must include `--overrides data/facet_overrides.tsv`, and the first log line should
> read `Loaded N overrides` with N > 0. (Distinct from the spec's `build.assets`
> overrides in §3b — same word, different thing.)

---

## 5. Validate (gates before calling a build good)

```bash
python verify_lossless.py        # byte-exact round-trip decode(encode(x)) == x
python verify_facets.py --db db/builds/<name>/dictionary.lmdb
python verify_faiss.py           # if the suite built a faiss index: index bound to its substrate
# family alignment — every derived asset's fingerprint matches the dictionary:
python ../tools/verify_substrate_chain.py db/builds/<name>
# efficiency on the held-out set (records OOV + ratio):
python -m semantic_compression.bench_dict_efficiency select  --source books   # held-out
python -m semantic_compression.bench_dict_efficiency measure --source books \
        --lmdb new=db/builds/<name>/dictionary.lmdb
python -m semantic_compression.bench_dict_efficiency report  --source books
```

`verify_substrate_chain.py` is the "is this build internally consistent" gate: it walks
the `manifest.json` artifacts registry and flags any asset whose recorded fingerprint has
drifted from the dictionary — including a stale manifest that wasn't re-emitted after an
asset was re-derived. A build that fails it is not shippable, even if the lossless and
facet gates pass.

Reading the numbers: **ratio = raw_bytes / encoded `.eloB` bytes**. Ratio > 1
compresses; **ratio < 1 means the format expanded the text** — that happens when
OOV is high (~25%+), because OOV tokens fall to byte-fallback. If you see
expansion, the fix is coverage (add sources / go deeper), not the scorer. Track
**OOV%** as the leading indicator; depth and corpus both lower it.

---

## 6. Lifecycle & locking

`meta.status` and `stamp_meta.py` move a build through its lifecycle (meta-only;
the content fingerprint never changes):

```
staged   IDs provisional; fingerprint NOT a contract. Default for new builds.
frozen   immutable release; fingerprint IS the contract.
locked   frozen AND bound to an LLM retrain (bound_model set) — must not be rebuilt.
```

```bash
python stamp_meta.py --db db/builds/<name>/dictionary.lmdb --release v0.4.0 --status frozen --version 4
# bind to a retrain (protects the dictionary<->model pairing):
python stamp_meta.py --db db/builds/<name>/dictionary.lmdb --release v0.4.0 \
        --lock-for-model elo-3B-2026-06-20
```

Once `locked`, an overwrite build is refused unless forced — a model is paired to
that exact fingerprint (the same pairing rule as `llm-training/RETRAINING.md`).

---

## 7. Adopt / cascade

A build in `db/builds/` is a candidate. Adoption is deliberate: re-run the
PROCESS.md §7 cascade (facets → embeddings/FAISS → LLM profiles → retrain), and
remember an `.elo`/model must be decoded/paired with the **same dictionary
fingerprint** it was built under. Don't persist raw S1 IDs while status is `staged`.

---

## 8. Worked example

`builds/general_v0.4_char4.yaml` (in the repo) built `db/builds/general_v0.4_char4/`:
char-4, frequency, facets on; corpus = transcripts (206,642 tokens) + 21 books,
5 books held out; **250,565 unique / 191M tokens**, corpus fingerprint
`68cf632d…`, **417,841 entries**, byte-exact. That single command produced the
package and stamped the entire recipe into its `manifest.json`.

---

## 9. Toward the web GUI

The spec is the contract between the GUI and the builder; the GUI is a spec editor.

| GUI control | spec field |
|---|---|
| Select base (size + core source) | `build.size` + base `corpus` source |
| Select expanded knowledge categories | additional `corpus` sources (books, wiki, wiktionary topics) |
| Fine-tune (strategy, reserves, depth) | `build.select_strategy`, `tier1_word_reserve`, `min_freq` |
| Choose what ships (suite / individual assets) | `build.suite` preset + `build.assets` overrides (§3b) |
| Facets on/off | `build.assets.facets` (legacy `build.with_facets`) |
| Hold out for testing | `eval.held_out_books`, `eval.transcript_eval` |
| Name / author / purpose / model | `meta.*` |
| Build button | `build_from_spec.py <spec>` |
| Lifecycle (stage/freeze/lock) | `meta.status` + `stamp_meta.py` |

Because the build is fully declared by the spec and stamped into the manifest,
every dictionary the GUI produces is reproducible and auditable by construction.

---

## 10. File map

```
Spec authoring     semantic_compression/builds/<name>.yaml
Resolver           semantic_compression/build_from_spec.py
Suite model        semantic_compression/build_suite.py (presets, asset deps, prereqs)
Asset cascade      semantic_compression/build_assets.py (derives the declared suite; docs/compression/spec-asset-pipeline.md)
Builder            semantic_compression/dictionary_builder_v03.py (build, build_package, scorers)
Facets             semantic_compression/facet_builder.py
Meta DB            semantic_compression/meta_builder.py + meta_fields.py -> meta.db
Verb complements   semantic_compression/verb_complements.py (canonical -> meta `complement` column)
Lifecycle          semantic_compression/stamp_meta.py
Eval harness       semantic_compression/bench_dict_efficiency.py (+ data/eval_channels.txt)
Lossless gate      semantic_compression/verify_lossless.py ; verify_facets.py ; test_complement_meta.py
Family gate        tools/verify_substrate_chain.py (asset↔dictionary fingerprint alignment) ; verify_faiss.py
End-to-end build   semantic_compression/build_dictionary.py (core + cascade in one command)
Family contract    docs/compression/SPEC-BUILD-FAMILY.md (what a build is; how consumers read it)
Sources            Resources/transcripts/ , Resources/books/
Theory             docs/compression/spec-vocab-strategy.md ; spec-corpus-sourcing.md
Process / cascade  PROCESS.md (§1 cascade, §7 adoption)
```
