# EloAI — System 1: Base64 Canonical Library
### Status Document
> Reflects actual code at github.com/4waymedia/semantic-compression
> Last verified: 2026-07-08 -- v0.4.0 (Semantic Facets Layer) STAGED; meta DB now
> carries a `complement` (verb-subcategorization) column.
> Create a dictionary: [docs/compression/GUIDE-create-dictionary.md](docs/compression/GUIDE-create-dictionary.md).
> v0.3.0 dictionary intact, 13/13 round-trip byte-exact

---

## Status: v0.4.0 STAGED -- Semantic Facets Layer (on the v0.3.0 dictionary)

System 1 has shipped two production milestones and has a third version
(v0.4.0) open and staged, whose first landed feature is the semantic facets
layer. The v0.4 Compression and LLM tracks remain open in parallel.

| Tag | Date | Headline | Avg ratio | Stream tokens |
|---|---|---|---:|---:|
| `v0.2` | 2026-06-06 | Predictive Binary Token Stream | 1.84x | 988k |
| `v0.3.0` | 2026-06-07 | Phrase Dictionary + LLM Vocab Contract | 1.99x | 449k (-55%) |
| **`v0.4.0`** | **2026-06-19** | **Semantic Facets Layer (STAGED)** | **—** | **unchanged** |

The v0.3 milestone was reframed from a compression target to a
vocabulary target after measured results revealed the structural ceiling
of the fixed-tier byte scheme. See `docs/compression/v0.3-analysis.md`
for the theory-vs-practice retrospective.

---

### Session progress — 2026-07-08 (meta `complement` column + downstream polarity)

**Meta DB — verb-complement facet (`complement` column).** Graduated the
complement-aware POS-resolution table into the dictionary as a per-surface meta
column. Canonical source `verb_complements.py` (`to_infinitive` | `to_noun` | both,
plus a light lemmatizer handling e-drop / y-ied / consonant-doubling —
`decided→decide`, `tried→try`, `mapped→map`); `meta_fields.derive_meta` populates
`complement` for verb surfaces (`method="lexicon"`), and `meta_builder` adds it to
the deterministic `DET_COLS`. This is the POS instruction set consumers read to
disambiguate a "to X" homograph (`seem`→infinitive verb vs `map`→noun object).
Deterministic, re-derived per build, part of `meta_fingerprint`. New gate
`test_complement_meta.py`. Spec: `docs/compression/spec-meta-db.md` §3/§4.

> **Materialized (verified 2026-07-08).** `general_v0.4_char4/meta.db` carries the
> column populated for **343 surfaces** (218 infinitive-only, 83 noun-only, 42 both);
> `elo-browser-v01` carries **353**. Anchors resolve correctly (`seem/seemed →
> to_infinitive`, `map/maps → to_noun`; `code`/`change` → NULL, since the facet lives
> on the *governing verb*, not the homograph). Coverage is a **seed**: 47 verb lemmas
> and their inflections — grow it from corpus failures.

**Two build-integrity issues found and handled (2026-07-08):**

- **`elo-browser-v01/meta.db` was corrupt** (`database disk image is malformed`)
  even though its `meta_stats.json` recorded a successful run. Cause: `meta_builder`
  overwrites the target via `shutil.copyfile` onto a filesystem where `unlink` is
  blocked, which can leave a partially-written DB. **Repaired + layer-2 restored
  (verified 2026-07-09):** `integrity_check = ok`, `meta_layer 2`, 437,990 rows,
  `epa_e` filled 236,645 (54%), complement 353, and the S1 fingerprint `790379b2…`
  carried through the layer-2 pass untouched (layer-2 fills S2 columns only).
  The build's previously recorded `c5ae75c8…` was irreproducible and is superseded.
- **Root cause fixed — never overwrite a live SQLite file.** Both `meta_builder.py`
  and `meta_layer2.py` did `try: os.replace(...) except OSError: shutil.copyfile(tmp,
  live_db)`. On a filesystem that blocks rename-over, they silently took the
  corrupting branch. The fallback is **removed**; both now stage → `fsync` →
  `PRAGMA integrity_check` on the staged copy → atomic `os.replace`, and raise
  loudly (leaving a *valid* `.db.tmp`) if the rename is refused.
- **`meta_fingerprint` was silently cwd-sensitive.** `build_meta` loaded
  `data/facet_overrides.tsv` by a *relative* path inside a bare `except: ov = None`,
  so running from the repo root (as `build_from_spec` does) skipped the overrides and
  produced a **different fingerprint** with no warning (`e1328234…` vs `790379b2…`).
  Fixed: the path now resolves **module-relative**, a missing explicit overrides file
  **raises**, and `overrides_applied` + `overrides_sha` are recorded in `meta_info`.
  Consequence: any previously recorded `meta_fingerprint` built from the wrong cwd is
  **not reproducible** and should be re-derived (e.g. `elo-browser-v01`'s old
  `c5ae75c8…` matches neither path and is superseded by `790379b2…`).

**Downstream consumers (context — outside `semantic_compression`).** The
extraction pipeline (`05-ExtractionPipeline`) now reads the meta `complement`
facet via `meta_complements.py` — a bound dictionary is authoritative, with a
bundled fallback so it still runs offline. Separately, a `polarity`
(affirmed | negated) field was added to the extraction claim and to `MemorySeed`
(serialization bumped v2→v3, backward-compatible: old seeds backfill to
`affirmed`) and wired into the mneme contradiction store, so a claim and its
negation are distinct seeds that form a CONTRADICTS pair. These **consume**
System-1 output; they do not touch forward/reverse/facets.

---

### Session progress — 2026-06-25 (O-EPA4 phrase composition + verbalizer)

**O-EPA4 — phrase EPA composition (`epa_phrase_composer.py`).** Iterated over all
373,918 dictionary entries without an existing EPA rating; composed EPA vectors for
multi-word entries by taking the mean EPA over rated content tokens (stopword-stripped,
alpha-only, 2–6 tokens). Result: **13,042 strong-tier phrases** written to
`epa_substrate.lmdb b'epa'`; FAISS index rebuilt to **67,936 vectors** (was 54,894,
+24%). Coverage note: the remaining 82% gap is corpus n-grams with no clean content
words — no further composition is possible with single-word Warriner/NRC-VAD data.
STRONG tier (≥2 rated tokens, weight=1.0); WEAK tier (1 rated token, weight=0.8).

**Verbalizer** (`verbalizer.py`, built prior session). `VerbalizerSubstrate` lazy-loads
the 67,936-vector FAISS index; `Verbalizer.expand(inp)` → `SemanticField` with
`nodes`, `pole_nodes`, `cross_nodes`, `centroid`. Smoke test suite `verify_verbalizer.py`:
42/42 gates (T1–T11, covers substrate, `semantic_add`, well-formed SemanticField,
axes, poles, crosses, direction, determinism, suggest top_n, render/encode, edge cases).

**Step 14 wired into ExtractionPipeline** (`step14_verbalizer.py` + `pipeline.py`
`verbalizer_expander` param). Optional injection — fully backward-compatible.
`SemanticAtom.semantic_field` populated per sentence when injected.

### Session progress — 2026-06-22 (v0.4 infra + affect layer)

Build/provenance infrastructure and the first affect layer landed. All new;
binary build artifacts are gitignored, regenerable from specs.

**Build system.** `build_from_spec.py` builds a dictionary from one declarative
YAML spec (corpus + eval + params), emitting a self-contained package with a
`corpus_fingerprint`. `dictionary_builder_v03.py` gained per-build deliverables
packages (`--new-build` / `--build-dir --overwrite`), the `bytes_saved` selection
strategy + `pmi` in the scorer contract, and a repaired CLI. `stamp_meta.py` adds
a `locked` lifecycle + `bound_model` (LLM-retrain lock). `artifact_identity.py` +
`spec-artifact-identity.md` give every artifact (dictionary/facets/epa/meta/
templates) one identity shape (fingerprint+version+status+bound_refs), referenced
in the package manifest.

**Evaluation.** `bench_dict_efficiency.py` measures `.eloB` ratio on a held-out,
channel-balanced transcript+books eval. Findings: on transcripts S0≈S1; depth
(char-3→char-4 +11.5%) and coverage dominate selection; at high OOV the codec
*expands* (books at char-3 < 1.0); adding a book corpus cut held-out-book OOV
8.4%→3.9%.

**Meta + EPA (affect).** `meta_fields.py` = System-1 deterministic richer meta
(abstraction/causality/temporality/scope), reserved System-2 columns
(`spec-meta-db.md`). EPA affect layer: `epa_match.py` joins the global EPA
substrate to dictionary IDs (id-keyed table + coverage). Phrase EPA via
composition + lemmatize + the **NRC-VAD v2.1 union** took content coverage
4.4%→**56.7%** and phrase coverage 0→**97.8%**, AI residual 167k→**3,645**.
`Memory/mneme/substrate/epa_substrate.py` builds the versioned, provenance-tagged
merged substrate (V1 agreement gate caught Warriner↔NRC Potency disagreement
r=0.33; per-axis confidence). difflib fallback measured unusable (63% sign
agreement) and rejected. Specs: `Memory/mneme/docs/{EPA,phrase-epa-strategy,
spec-epa-expansion,spec-epa-substrate}.md`.

**New docs.** `GUIDE-create-dictionary.md`, `spec-vocab-strategy.md`,
`spec-meta-db.md`, `spec-artifact-identity.md` (System 1);
EPA suite (mneme). Status: STAGED — IDs provisional; EPA/meta layers are
System-1-deterministic now, System-2 columns reserved until EPA finalization.

### Facets layer (2026-06-19) — static semantic annotation, BUILT + VERIFIED

A 4-byte facet record (`semantic_bucket` + composable `logic_cue_mask` + `flags`,
keyed by `id_bytes`) now annotates every dictionary entry, added in place as the
`facets` + `meta` sub-databases (`max_dbs` 2→4) without touching `forward`/`reverse`.
Deterministic, no model inference. 373,918 entries faceted in ~2s; forward/reverse
verified byte-exact; deterministic content fingerprint `2d2feb69…`; gates
T1/T3/T4/T5/T6/T9/T10/T12 pass.

- Code: `config.py`, `normalize.py`, `facets.py`, `facet_builder.py`, `facet_reader.py`,
  `verify_facets.py`, `data/facet_overrides.tsv`.
- System-1 spec: `docs/compression/spec-facets-db.md` (v2).
- Cross-dictionary STANDARD: `../Memory/docs/SEMANTIC_FACETS_SPEC.md` (v1).
- The record currently keys on existing Base64 IDs; binary storage optimization
  is a deferred phase, after the dictionary/file-format spec locks.

Release: `dictionary_release=v0.4.0`, `dictionary_status=staged`. S1 IDs are not
yet final — only 1 dictionary test group has run (5–10 planned for char-2/char-3;
char-4 deferred to stabilization). Facets are re-derived per build: re-run
`facet_builder.py` after every dictionary rebuild. Fingerprint is not a frozen
contract until status flips to `frozen`. Downstream consumers: bind to surfaces,
re-map per build, don't persist raw S1 IDs yet (see `Memory/docs/SEMANTIC_FACETS_SPEC.md` §10b).

### v0.4 corpus front-end + sized builds (in progress)

The vocabulary pipeline is being rebuilt for v0.4 — better vocabulary from a
**multi-source pooled corpus** and **use-case-sized** dictionaries:

- **Multi-source ingest:** `source_adapters.py` (contract) + `transcripts`
  (`Resources/transcripts/`) and `wikipedia` adapters; `pool_counter.py` pools
  sources with per-source counts + **dispersion** (one source vs many).
- **Authority/coverage:** `wiktionary_categories.py` harvests a topic-labeled
  **word base** (inclusion list) so valuable terms missing from frequency sources
  still get entries.
- **Builder:** `dictionary_builder_v03.build()` now takes a pluggable
  `select_strategy` (default = frequency) + `max_tier` build depth —
  **size = char-2 / char-3 / char-4**.
- Specs: `docs/compression/spec-corpus-sourcing.md`,
  `docs/compression/spec-dict-testgroups.md`. Status: corpus front-end + builder
  hooks built + tested (synthetic); full pooled rebuild pending real source dumps.

---

## Core Design Decisions — Locked

```
1. NO LEMMATIZATION
   Surface form = dictionary key. Direct lookup. No NLP processing.
   "running", "ran", "run" each get their own ID.

2. UNIFIED FREQUENCY MODEL
   All tokens compete in one frequency ranking -- words, phrases,
   contractions, punctuation, whitespace runs.

3. UNIVERSAL CHARACTER-CLASS TOKENIZER
   tokenizer.tokenize(text) -> list[str]
   Invariant: ''.join(tokenize(s)) == s for any UTF-8 string.

4. WHITESPACE PRESERVED, NOT COLLAPSED
   Required for byte-exact round-trip on .json .yaml .html .xml.

5. CASE PRESERVED VIA caps_codec
   Dictionary stores lowercase canonical forms.
   In-vocab cased tokens emit '<cap>:<ID>' in the stream.
   OOV tokens emit 'OOV:<cap>:<lower>' (caps_codec.encode_oov).

6. PHRASE ATOMS (v0.3)
   Common multi-word sequences ('you know', 'i don't know',
   'at the end of the day') get single dictionary IDs.
   167,275 phrases mined via PMI + maximal-phrase filtering.
   Longest-match scan in encoder; decoder unchanged.

7. TIER 0 = 58 LOSSLESS POINTERS
   26 words + 27 structural + 5 system.
   ~67% of corpus token coverage, dominated by single-space.

8. 4-CHAR (TIER 3) IS THE PRODUCTION BOUNDARY
   Tier 1 (2-char): 1,280 IDs    -- top words + 256 high-freq phrases
   Tier 2 (3-char): 81,920 IDs   -- mid-frequency mix
   Tier 3 (4-char): 5.2M IDs     -- long tail (~290k used in v0.3)

9. LMDB IS PRODUCTION STORAGE
   ~100ns lookup. Memory-mapped. C-readable directly.
   Two named DBs in one env: forward + reverse.

10. 100% LOSSLESS -- PROVEN
    decode(encode(file_bytes)) == file_bytes for every v1 format.
    13/13 test files (10 v1 samples + 3 transcripts) byte-exact.

11. STANDARDIZED LLM VOCAB PROFILES (v0.3)
    Five frozen subsets of the dictionary published as the
    public LLM-training contract. See docs/v1/profiles.md.

12. C/C++ PORTABILITY ENFORCED
    FORMAT_VERSION + STREAM_ENCODING locked in config.py.
    struct.pack('<I', n) for stored integers.
    Stateless codec functions, pure character-class tokenizer.
```

---

## v1 Format Coverage -- PROVEN

```
Format   Round-trip   v0.3 ratio  Notes
-------- ----------   ----------- ------------------------------------
.txt        PASS      1.38x       natural language baseline
.md         PASS      0.97x       markdown with code blocks
.json       PASS      0.84x       structured (OOV-dominated at <1 KB)
.csv        PASS      0.87x       tabular with quoted fields
.xml        PASS      0.90x       RSS feed sample
.html       PASS      0.80x       full document with tags + entities
.yaml       PASS      0.86x       indentation preserved exactly
.log        PASS      0.99x       timestamps + levels
.srt        PASS      1.12x       subtitles with timestamps
.vtt        PASS      0.89x       WEBVTT with cue blocks

3 transcript stress test:
times_now   PASS      1.99x       2.4 MB news transcript
jocko       PASS      2.02x       1.0 MB military podcast
julian      PASS      1.97x       0.8 MB interview
```

Small-sample sub-unity ratios are header-overhead amortisation, not
correctness issues. Sample files are <1 KB.

---

## Tier 0 Layout (64 slots)

```
SLOT  TOKEN         ROLE
----  -----------   -----------------------------------------
-- SYSTEM (5) --
0     STREAM_START
1     STREAM_END
2     CHUNK_BOUNDARY
-     ATTR_DELIMITER         (cap-prefix separator)
_     CONTINUATION           (reserved)

-- ESSENTIAL 26 WORDS (26) --
A 'a'    B 'be'   C 'we'   D 'do'   E 'he'   F 'of'   G 'to'
H 'have' I 'in'   J 'on'   K 'for'  L 'they' M 'i'    N 'and'
O 'or'   P 'not'  Q 'all'  R 'she'  S 'this' T 'the'  U 'it'
V 'with' W 'will' X 'but'  Y 'you'  Z 'that'

-- STRUCTURAL (27) -- whitespace + punctuation + symbols --
g <space>  h <newline>  i <tab>
j '.'  k ','  l ':'  m ';'  n '!'  o '?'  p "'"  q '"'
r '('  s ')'  t '['  u ']'  v '/'  w '\\'  x '-'  y '—'  z '&'
3 '%'  4 '$'  5 '#'  6 '@'  7 '*'  8 '+'  9 '='

-- RESERVED FOR SYSTEM 2 PROCESS STAGES (6) --
a PERCEPTION   b NOVELTY   c GOAL_PLAN
d ACTION       e PROGRESS  f RESULT
```

Tier detection by LENGTH (1 = Tier 0, 2 = Tier 1, 3 = Tier 2, 4 = Tier 3).
g-z double-duty as both Tier 0 single-char IDs AND Tier 1/2/3 first chars
without ambiguity because length resolves the disambiguation.

---

## Stream Format

```
lowercase in-vocab:   <ID>                  e.g. "T"           -> "the"
cased in-vocab:       <cap>:<ID>            e.g. "g:T"         -> "The"
phrase atom:          <ID>                  e.g. "wB"          -> "you know"
lowercase OOV:        OOV:A:<word>          e.g. "OOV:A:foo"   -> "foo"
cased OOV:            OOV:<cap>:<word>      e.g. "OOV:g:foo"   -> "Foo"

tokens separated by   '|'                   (PIPE_BYTE = 0x7C)
internal field sep    ':'                   (OOV_SEP_BYTE = 0x3A)

Forced dictionary seeds:
   '|'  -> 'gA'   (must be in dictionary to prevent stream-parsing breakage)
```

Binary wire format (`.eloB`): tier-tagged variable-length encoding,
1-4 bytes per ID, no delimiters. See `compressor.py` for the byte spec.

---

## Components

```
File                          Status         Verification
----                          ------         -----------------------
config.py                     v0.3 LOCKED    verify_config.py
corpus_scanner.py             v0.2 LOCKED    (YouTube-specific adapter)
tokenizer.py                  v0.2 LOCKED    verify_tokenizer.py
format_adapters.py            v0.2 LOCKED    verify_adapters.py
caps_codec.py                 v0.2 LOCKED    verify_caps.py
word_frequency_counter.py     v0.2 LOCKED    -- 186M tokens, 207k forms
ngram_counter.py              v0.3 LOCKED    -- 233k phrase candidates
phrase_miner.py               v0.3 LOCKED    -- PMI + maximal filter
dictionary_builder.py         v0.2 LOCKED    -- words only, reproducible
dictionary_builder_v03.py     v0.3 LOCKED    -- words + 167k phrases
compressor.py                 v0.3 LOCKED    verify_compressor.py
verify_lossless.py            v0.2 LOCKED    -- inline proof harness
samples/ (10 files)           v0.2 LOCKED    -- one per v1 format
```

---

## v0.3 Dictionary Stats (current production)

```
Format version:   3
Total entries:    373,918

  Tier 0 (1-char):       53    pre-seeded (words + structural)
  Tier 1 (2-char):     1,280   1,024 reserved words + 256 high-freq phrases
  Tier 2 (3-char):    81,920   mid-frequency mix
  Tier 3 (4-char):   290,665   long tail

Words in dict:    206,616
Phrases in dict:  167,275    (after dropping 601 zero-savings Tier-0 bigrams)

Profile cuts (LLM vocabulary subsets):
  Tiny       content= 32,496   total= 32,768   (LLaMA 2 size)
  Compact    content= 65,264   total= 65,536   (GPT-2 size)
  Standard   content=130,800   total=131,072   (LLaMA 3 size)
  Full       content=261,872   total=262,144   (research scale)
  Reference  content=373,891   total=374,163   (full dictionary)

Forced seed:  '|' -> 'gA'   (stream-delimiter safety)
```

See `docs/v1/profiles.md` for the full profile contract.

---

## What Remains -- v0.4 Tracks (parallel)

The v0.3 milestone closes System 1's vocabulary work. Two parallel
v0.4 tracks open:

```
v0.4 -- COMPRESSION TRACK
        Structure-Aware Transcript JSON Codec.
        Target the JSON-specific redundancies that fixed-tier
        dictionary compression cannot reach:
          - repeated JSON field keys
          - segment record te