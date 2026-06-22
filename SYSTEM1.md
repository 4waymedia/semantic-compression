# EloAI — System 1: Base64 Canonical Library
### Status Document
> Reflects actual code at github.com/4waymedia/semantic-compression
> Last verified: 2026-06-19 -- v0.4.0 (Semantic Facets Layer) STAGED;
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