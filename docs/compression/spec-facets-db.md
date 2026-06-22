# Semantic Facets Database + Expert Dictionaries — Specification (v2)

### A static annotation layer over the frozen dictionary, plus domain-specialized expert builds

> Status: IMPLEMENTED + VERIFIED on the System 1 dictionary (2026-06-19). This is
> the System-1 implementation reference; the cross-dictionary STANDARD that
> governs it is `Memory/docs/SEMANTIC_FACETS_SPEC.md` (v1). Code: `config.py`,
> `normalize.py`, `facets.py`, `facet_builder.py`, `facet_reader.py`, `verify_facets.py`.
> Build result: 373,918 entries faceted, forward/reverse byte-exact, fingerprint
> 2d2feb69…, all gates (T1/T3/T4/T5/T6/T9/T10/T12) pass.
> Originally drafted pre-code; incorporates external review (ChatGPT + Grok rounds 1–2).
> Predecessor context: v0.3.0 (dictionary frozen) + v0.4 (compression / LLM tracks).
>
> **What this is:** a *dictionary annotation layer* — three new id-keyed
> records (`facets`, `meta`) beside the existing `forward`/`reverse`. It does
> **not** emit facets into the `.elo` token stream.
> **What it touches in the file format:** exactly one thing — any stream encoded
> with a non-general (expert) dictionary MUST carry a dictionary **identity +
> fingerprint** so the correct codebook can be recovered. See §Identity and the
> revised N4. Everything else about the wire format is unchanged.
>
> This feature was scoped during the v0.3 design session as an option to
> revisit once compression and the dictionary were proven. v0.3 proved them
> (1.99× compression, 13/13 byte-exact round-trip, 167,275 phrase atoms).

---

## Changelog v1 → v2 (what review changed)

```
- Record: logic_role single byte  →  logic_cue_mask uint16 (composable roles).
- Renamed logic_role → logic_cue and ROLE → *_CUE: facets are lexical AFFORDANCE,
  not asserted analysis. Preserves the System 1/2 boundary explicitly.
- Bucket: dropped SKIP (reasoning-hostile); added UNKNOWN/RELATION/STRUCTURAL.
  Utility class (CONTENT/FUNCTION/STRUCTURAL/FILLER) moved into flag bits.
- Multiword no longer forces CONCEPT — phrase structure is a property (flag),
  not a semantic class.
- METHOD heuristic made conservative (curated lexicon + manual + clear verb
  form). Default-uncertain → TOPIC. Suffix-only METHOD removed (high error rate).
- Reserved byte removed. id_bytes is the universal join key: System 2 (EPA,
  process stage) lives in separate id-keyed databases, not packed into this record.
- New: dictionary identity + deterministic content fingerprint + `meta` sub-db
  (max_dbs = 4). Schema/identity validated from the DB itself, not a JSON sidecar.
- New: normalize_surface() contract; override file exact:/normalized: modes.
- New: explicit structural-token facets; descriptor-returning router with defined
  conflict precedence; verification gates T8–T13; storage/perf/timing corrected.
- New: §Future Updates & Refactors roadmap for deferred items.
```

---

## Strategic Intent

The dictionary answers *"ID for this token?"* (`forward`) and *"token for this
ID?"* (`reverse`). It says nothing about *what kind of thing* a token is or
*what role it can play in reasoning*. Every downstream consumer that needs that
— summarizer, relevance filter, context assembler, expert router — otherwise
re-derives it at runtime on every document. The `facets` database answers those
questions **once, at build time**, as a few bytes per entry. Consumers read a
tag the same way they read `reverse`: an indexed `txn.get(id_bytes, db=facets)`
with no model inference and no semantic recomputation.

Two capabilities ride on this:

1. **Semantic + logical-cue annotation** — each entry gets a semantic bucket
   (what kind of thing) and a composable logic-cue mask (what reasoning roles
   it can signal). Cheap, deterministic, directly actionable.
2. **Expert dictionaries** — a topic/subject expert is a *fully separate*
   dictionary+facets build over a domain-filtered corpus. The general dictionary
   stays frozen as the fallback; experts add domain coverage and domain facets
   without touching the base.

---

## Why This Matters (positioning)

Packaged together, the dictionary + `.elo` format + facets database is not an
incremental improvement to any one of compression, search, or tokenization. It
collapses three properties that normally trade off against each other into a
single artifact.

**The three-way decoupling.** Conventionally you pick two of {small, lossless,
queryable-meaning}:

```
gzip / zstd        small + lossless    but OPAQUE  (decompress + run a model to get meaning)
embeddings / vDB   queryable-meaning   but LARGE + LOSSY (cannot reconstruct the text)
this artifact      small + lossless + queryable-meaning, simultaneously
```

It achieves all three because **the codebook is the index**: the ID that
losslessly reconstructs a token is the same ID that carries its semantic bucket
and logic-cue mask (and, via the same `id_bytes` key, its future EPA/stage).

**Insight without inference.** The classification cost is paid once, at build
time. A consumer then gets semantic and logical structure at memory-lookup
speed with no model in the loop. This is the property that makes the rest
valuable.

**Why mobile / edge.** The expensive component at the edge is the model —
battery, latency, NPU dependence, no offline mode. With structure precomputed
into a RAM-resident, C-readable codebook, a device performs semantic operations
at the cost of a B-tree lookup, offline. This compounds the existing on-device
thesis (`docs/v1/profiles.md`): compressed tokens shrink the payload; facets add
queryable structure to it at near-zero marginal cost.

**Why desktop / OS file indexing.** Windows Search and macOS Spotlight pay a
continuous tax: a background service re-parses files, maintains a large separate
index, and competes for disk and CPU — a well-known source of system slowdown.
An ELO-packaged corpus inverts that. The structure index is *already inside* the
compressed artifact (the codebook is the index), so search and semantic
filtering read precomputed facets instead of rebuilding a shadow index. The result
is a smaller on-disk footprint, no always-on reindexing daemon, and
content-addressable lookups — desktop search without the indexing overhead that
slows the machine down.

**Why AI.** Two compounding, distinct wins: (1) phrase-atom tokens expand
effective context (existing thesis); (2) facets hand a model — or a cheap
pre/post-filter around it — a *free structural prior* for routing, retrieval,
attention bias, and relevance filtering, with no separate NLP pass. The
compressed stream and its structure travel together.

**Defensible-claim calibration.** The dictionary is corpus-bound (mined from the
target corpus), and the annotation is lexical-prior granularity — *affordance*,
not completed analysis (the System 1/2 boundary, held deliberately). The
strongest claim that survives scrutiny is therefore: *queryable lexical and
logical structure at zero inference cost, packaged with lossless ~2×
compression, portable and offline.* That is novel and defensible. "It
understands text" is not the claim and should not be made — it is the wrong
description of a System 1 artifact and invites an easy rebuttal.

---

## Scope Boundary — System 1 vs System 2 (read first)

CLAUDE.md locks: *in System 1, do not design or implement System 2–4 code.*
v0.4 restates it as non-goal N7 (EPA / process-stage / System 2 deferred).

```
IN  (System 1, this spec):
    - Semantic bucket               (UNKNOWN/TOPIC/METHOD/CONCEPT/RELATION/STRUCTURAL)
    - Logic-cue mask                (composable, static AFFORDANCE per entry)
    - Property + utility flags       (multiword / closed-class / provenance / utility)
    - Dictionary identity + fingerprint + schema metadata

OUT (System 2 — NOT packed into this record; reached via the same id_bytes key):
    affect      id_bytes → EPA prior (Evaluation/Potency/Activity)
    process     id_bytes → process-stage prior (Surov 6-stage cycle)
    instance    occurrence → contextual interpretation
```

The key architectural decision: **`id_bytes` is the universal join key.** Every
per-token layer — `forward`, `reverse`, `facets`, and future `affect`/`process` —
is keyed by the same ID. System 2 therefore *connects* to a token by opening its
own id-keyed database; it never widens the System 1 facet record. This keeps the
static lexical layer small and fixed, and lets cognition grow through
connections between fields rather than one swelling monolithic record.

> Naming note: the LMDB sub-database is `facets`. Unrelated to the transcript JSON
> `tags` field (transcript metadata). This doc always means the sub-database.

---

## Context: What Exists Today

`dictionary_builder.py`:

```
dictionary.lmdb/   (max_dbs = 2)
    forward:  word_bytes → id_bytes    (encode)
    reverse:  id_bytes   → word_bytes  (decode)
```

`config.py`, reused here unchanged:

```
FORMAT_VERSION = 1 ; STREAM_ENCODING = 'utf-8'
struct.pack('<I', n) convention for stored ints (C-readable)
WORD_IDS (26 universal words) ; STRUCTURAL_IDS (whitespace + punctuation)
FILLER_MAP (6 classes) + ALL_FILLERS ; RESERVED_IDS a–f (Surov stages, System 2)
detect_tier() (tier from ID, no DB lookup)
```

---

## Goals

```
G1   Add id-keyed `facets` (facet record) and `meta` (schema/identity) sub-databases
     to the dictionary LMDB, built in the same process as forward/reverse.
G2   Fixed-width, C-readable 4-byte facet record: semantic bucket + composable
     logic-cue mask + flags. No model inference at build time.
G3   Deterministic assignment pipeline: properties → manual override →
     closed-class → entity/domain → conservative lexical → TOPIC/empty fallback.
G4   Human-editable override file (exact + normalized modes) that wins over all
     automatic rules; ship a non-empty starter file for common heuristic misses.
G5   Pin a surface-normalization contract used for tag matching (forward/reverse
     surfaces stay byte-exact).
G6   Dictionary identity + deterministic content fingerprint + schema metadata,
     stored in `meta` and validatable from the DB itself.
G7   Expert dictionaries as fully separate builds over domain-filtered corpora,
     with a registry, isolation guarantees, descriptor-returning routing, and a
     hard rule that any expert-encoded artifact carries its codebook identity.
G8   Verification gates mirroring the project's per-step verify pattern (T1–T13).
```

---

## Non-Goals

```
N1   No EPA, process-stage, or embedding lookups at build time (System 2;
     reached via separate id-keyed databases, never packed into the facet record).
N2   No per-instance / context-dependent faceting. Facets are static per entry.
N3   No modification of forward / reverse, or of any existing v0.3 .elo stream.
N4   Tag records are NOT emitted into the compressed token stream. HOWEVER, any
     stream encoded with a non-general dictionary MUST carry or be accompanied
     by an immutable dictionary identity + fingerprint (header, sidecar manifest,
     or enclosing container). Without it, expert streams are not safely portable.
     [Revised from v1's "no change to the wire format." The stream need not carry
      facets; it MUST identify the codebook that gives its IDs meaning.]
N5   No automatic corpus-to-domain classification here. The corpus subset that
     defines an expert is an input to the expert build, not a learned step.
N6   No overlaying of expert facets onto the base dictionary. Experts are fully
     separate builds (locked decision). The expert_profile model (annotation
     over shared IDs) is a future concept — see §Future Updates.
```

---

## The Tag Record (v1 wire layout)

```c
/* sc_tags.h — DO NOT memcpy into this struct; padding around the u16 is
   compiler-dependent. Read fields by byte offset (see below). */
typedef struct {
    uint8_t  semantic_bucket;   /* byte 0      */
    uint16_t logic_cue_mask;    /* bytes 1-2, little-endian */
    uint8_t  flags;             /* byte 3      */
} sc_tag_v1;                    /* 4 bytes logical; never assume packed layout */
```

Pack / unpack (Python and C agree byte-for-byte):

```python
record = struct.pack('<BHB', bucket, logic_cue_mask, flags)         # write
bucket, logic_cue_mask, flags = struct.unpack('<BHB', value)        # read
```

```c
uint8_t  bucket   = value[0];
uint16_t cue_mask = (uint16_t)value[1] | ((uint16_t)value[2] << 8); /* LE */
uint8_t  flags    = value[3];
```

There is no reserved byte. Format growth is governed by `facets_format_version`
in `meta`; unused bucket values, cue bits, and flag bits provide System 1
headroom; System 2 attaches via separate id-keyed databases.

### Byte 0 — semantic bucket

```
0x00  UNKNOWN     unclassified (safe default; reader policy below)
0x01  TOPIC       noun-class thing / subject
0x02  METHOD      action / process (assigned conservatively — see D-METHOD)
0x03  CONCEPT     multi-word DOMAIN concept (not every multiword; see D-MULTIWORD)
0x04  RELATION    relational / connective unit (due to, is a, even though)
0x05  STRUCTURAL  whitespace, punctuation, byte-fallback, sentinels
0x06–0x0F         RESERVED (MODIFIER, NAMED_ENTITY, EVENT, … — see Future Updates)
Reader policy: unknown bucket value → treat as UNKNOWN (never silently drop).
```

### Bytes 1–2 — logic-cue mask (uint16 LE, composable)

A token may legitimately carry several cues at once; this is composition, not
uncertainty. Cues are static **affordances** — what role the token *can* signal
— not assertions that the role is active in any instance.

```
bit 0   0x0001  CLAIM_CUE
bit 1   0x0002  EVIDENCE_CUE
bit 2   0x0004  INFERENCE
bit 3   0x0008  CONTRAST
bit 4   0x0010  CAUSE
bit 5   0x0020  CONDITION
bit 6   0x0040  QUANTIFIER
bit 7   0x0080  NEGATION
bit 8   0x0100  CONJUNCTION
bit 9   0x0200  QUESTION
bit 10  0x0400  MODAL
bit 11  0x0800  DEFINITION_CUE
bit 12  0x1000  CONCESSION
bit 13  0x2000  COMPARISON
bit 14  0x4000  TEMPORAL
bit 15  0x8000  reserved
Reader policy: mask == 0x0000 means "no logic cue" (the NEUTRAL default).
Examples: because → CAUSE|EVIDENCE_CUE ; when → CONDITION|QUESTION|TEMPORAL.
```

The `AMBIGUOUS` flag (below) is reserved for genuinely *unreliable* assignment,
not for legitimate multi-cue tokens — the mask removes most need for it.

### Byte 3 — flags (properties + utility)

```
bit 0   0x01  MULTIWORD     surface contains a space (phrase atom)
bit 1   0x02  CLOSED_CLASS  matched a closed-class connective/function list
bit 2   0x04  MANUAL        assigned by override file (authoritative)
bit 3   0x08  HEURISTIC     assigned by automatic rule  (MANUAL ⊕ HEURISTIC: never both)
bit 4   0x10  AMBIGUOUS     assignment itself is unreliable
bit 5   0x20  reserved
bits 6-7 0xC0 UTILITY:  00 CONTENT | 01 FUNCTION | 10 STRUCTURAL | 11 FILLER
```

UTILITY is the orthogonal "meaningfulness" axis that replaces the old `SKIP`
bucket. A consumer building a topic index filters by `UTILITY == FUNCTION`
**as policy** — and crucially keeps logic-cue-bearing function words
(`not`, `if`, `because`, `but`, `unless`, `must`) instead of discarding them.
The builder asserts `MANUAL` and `HEURISTIC` are never both set.

---

## Why `SKIP` was removed (semantic safety)

`not / if / because / but / unless / all / some / must` have low standalone
topic meaning but enormous reasoning weight. A bucket named `SKIP` invites
summarizers and context assemblers to discard exactly the tokens that preserve
logic. v2 encodes "meaningfulness" as the UTILITY flag axis and the reasoning
weight as the logic-cue mask, so a consumer can drop a word from a *topic index*
while retaining it for *reasoning*. Verification gate T12 enforces that the
reference context-assembly policy never silently drops logic-bearing function
words.

---

## Assignment Pipeline (deterministic, no AI)

Properties and semantic class are resolved independently. First-match wins per
field; the logic-cue mask is the OR of all matching cue sets.

```
1. NORMALIZE         key = normalize_surface(surface)   (contract below)
2. PROPERTY FLAGS    MULTIWORD (has space), STRUCTURAL (in STRUCTURAL_IDS),
                     FILLER (in ALL_FILLERS), CLOSED_CLASS (in a logic seed list).
                     Set UTILITY from these (STRUCTURAL > FILLER > FUNCTION > CONTENT).
3. MANUAL OVERRIDE   exact: or normalized: match in facet_overrides.tsv wins
                     for bucket + cue mask. Sets MANUAL flag.
4. STRUCTURAL        STRUCTURAL token → bucket = STRUCTURAL; cue from a tiny map
                     (e.g. '?' → QUESTION). UTILITY = STRUCTURAL.
5. CLOSED-CLASS      key ∈ logic seed lists → OR in that list's cue bits,
                     CLOSED_CLASS flag. bucket = RELATION for pure connectives,
                     else keep going. AMBIGUOUS only if seed lists genuinely
                     conflict on bucket (not on cues).
6. FILLER            key ∈ ALL_FILLERS → UTILITY = FILLER. DISCOURSE fillers OR
                     in CONJUNCTION; other filler classes add no cue (affect is
                     System 2). bucket = UNKNOWN unless already set.
7. ENTITY / DOMAIN   reserved hook (off by default in v1; see Future Updates).
8. CONSERVATIVE      METHOD only if: MANUAL, OR in curated action/process lexicon,
     METHOD          OR an unambiguous infinitive/verb form. NO bare-suffix rule.
9. MULTIWORD CONCEPT remaining multiword that is NOT closed-class/relation and is
                     a known/likely domain term → bucket = CONCEPT.
10. DEFAULT          bucket = TOPIC, cue mask = 0, HEURISTIC flag.
```

Every test is string / membership / curated-lexicon — no embeddings, no model.

### D-METHOD — why the suffix rule is gone

A static `-ing/-tion/-sion/-ment/-ize/-ate/-ify` rule mislabels nouns at scale:
*information, government, movement, statement, organization, relationship,
cooking, learning, meaning, building, reasoning, understanding*. Multiplied
across ~374k entries that is tens of thousands of wrong METHODs, and the
override file should not have to repair an inherently high-error base rule. v1
therefore makes METHOD **sparse but trustworthy**; uncertain words default to
TOPIC (a false TOPIC is far cheaper than a false METHOD). Corpus-evidence METHOD
detection (no embeddings needed) is specced in §Future Updates as v1.1.

### D-MULTIWORD — structure is a property, not a class

Multiword surfaces span many functions: `due to` (CAUSE relation), `even though`
(CONCESSION/CONTRAST), `is a` (DEFINITION_CUE), `have been` (auxiliary),
`how many` (QUESTION|QUANTIFIER), `New York` (entity — reserved), `cast iron`
(domain CONCEPT), `in order to` (purpose relation). The `MULTIWORD` flag already
records phrase nature; bucket records function. So:

```
"due to"     bucket=RELATION  cue=CAUSE                flags=MULTIWORD|CLOSED_CLASS|FUNCTION
"cast iron"  bucket=CONCEPT   cue=0                    flags=MULTIWORD|CONTENT
"is a"       bucket=RELATION  cue=DEFINITION_CUE       flags=MULTIWORD|CLOSED_CLASS|FUNCTION
```

---

## Surface Normalization Contract

Tag keys are dictionary surface forms, so normalization must match what
`tokenizer.py` actually emits (apostrophe handling is already live — see
`check_apostrophes.py` and `STRUCTURAL_IDS["'"]`). Pinned for v1:

```
normalize_surface(surface) ->:
    Unicode:     NFC
    Case:        str.casefold()
    Whitespace:  collapse internal runs to single space; strip ends
    Apostrophes: preserve as emitted by tokenizer.py (do NOT strip)
    Hyphens:     preserve (do NOT split "due-to" into "due to")
    Punctuation: preserve (facets key on the token as tokenized, not de-punctuated)
    normalization_version = 1   (stored in meta; bump on any change)
```

`forward` / `reverse` surfaces remain byte-exact and untouched — normalization
applies only to tag matching and override-key resolution. The earlier seed-list
forms (`cant dont`) are replaced by whatever `tokenizer.py` emits for those
tokens; the seed lists are generated through `normalize_surface()` so
matching is consistent by construction.

### Override file (`facet_overrides.tsv`)

```
# facet_overrides.tsv   facets_format_version=1  normalization_version=1
# mode:key<TAB>bucket<TAB>cue[,cue...]<TAB># optional rationale
# mode is "exact" (raw surface) or "normalized" (post-normalize key)
exact:cooking          TOPIC      -              # -ing noun, not a method
normalized:due to      RELATION   CAUSE
exact:mise en place    CONCEPT    -
normalized:therefore   RELATION   INFERENCE
```

Names (not byte values) for editability; unknown bucket/cue names are a hard
build error (typo guard). `exact:` keys disambiguate when two surfaces normalize
to the same key. Builder warns/errors on `facets_format_version` /
`normalization_version` mismatch. Ship a non-empty starter file covering the
most common heuristic misses (the -ing/-tion nouns above).

---

## Identity, Fingerprint, and `meta` Sub-Database

### `meta` sub-database (max_dbs = 4: forward, reverse, facets, meta)

A reader must be able to validate a dictionary from the dictionary itself, not a
detachable JSON file. `meta` stores fixed keys (UTF-8 key → value):

```
facets_format_version     uint32   (struct '<I')
dictionary_format_version uint32
record_width            uint32   (4)
normalization_version   uint32
dictionary_family       utf8     "general" | "expert"
dictionary_id           utf8     "general" | "cooking" | ...
dictionary_version      uint32
override_sha256         utf8 hex
dictionary_fingerprint  utf8 hex (see below)
built_at                utf8 ISO-8601 Z
```

`dict_stats.json` remains for human reporting (tag histograms, manual count,
ambiguous count) but is NOT the source of truth for compatibility.

### Deterministic content fingerprint

Hashing the LMDB directory is non-deterministic (page layout, env specifics).
Define the fingerprint over content, sorted by id:

```
dictionary_fingerprint = SHA-256 over, for each entry sorted by id_bytes:
    uint32(len id_bytes) || id_bytes ||
    uint32(len surface_bytes) || surface_bytes ||
    tag_record(4 bytes)
(all lengths little-endian; surfaces in byte-exact forward form)
```

This is layout-independent and makes T10 (reproducibility) and T13 (expert
isolation) testable.

### Artifact identity (the portability fix)

Because expert dictionaries assign different IDs, a stream is meaningless
without its codebook. Any artifact encoded with a non-general dictionary MUST
carry an immutable identity block:

```
dictionary_family       general | expert
dictionary_id           e.g. "cooking"
dictionary_version
dictionary_fingerprint
```

Stored in the `.elo` header, an authenticated sidecar manifest, or an enclosing
ELO container (implementation choice; general-only deployments MAY omit it and
default to the general dictionary). **Decode rule: artifact identity always wins
over runtime metadata routing.** A fingerprint mismatch is a hard failure
*before* decode — never a plausible-but-corrupt decode (T8, T13).

---

## Logic Seed Lists (closed-class, frozen with this spec)

Small, stable closed-class tokens drive step 5. They live in `config.py` beside
`FILLER_MAP`, are lowercased through `normalize_surface()`, and are declared
in a **fixed, documented order** so overlap resolution is deterministic. Content
words are never seeded. (Illustrative; finalized at implementation, versioned
with `facets_format_version`.)

```
INFERENCE    therefore thus hence so consequently accordingly
CONTRAST     but however although though yet whereas nonetheless nevertheless
CAUSE        because since due cause causes caused owing
CONDITION    if unless when whenever provided assuming
QUANTIFIER   all some most many few none every each any both
NEGATION     not no never none cannot
CONJUNCTION  and also then furthermore moreover additionally plus besides
QUESTION     what why how when where who which whom whose
MODAL        can could must might may shall should would will ought
DEFINITION   is are means refers denotes defined constitutes
CONCESSION   admittedly granted regardless despite notwithstanding
COMPARISON   like than as similarly likewise versus compared
TEMPORAL     when while before after during until then
```

Overlap (`because` ∈ CAUSE ∩ EVIDENCE; `when` ∈ CONDITION ∩ QUESTION ∩ TEMPORAL)
sets *multiple cue bits* — it is not ambiguity. AMBIGUOUS is set only when seed
lists conflict on **bucket**, and such cases are override candidates.

---

## Example Tag Records

```
"therefore"     bucket=RELATION(04) cue=INFERENCE(0x0004)            flags=CLOSED_CLASS|FUNCTION  → 04 04 00 42
"because"       bucket=RELATION(04) cue=CAUSE|EVIDENCE(0x0012)       flags=CLOSED_CLASS|FUNCTION  → 04 12 00 42
"mise en place" bucket=CONCEPT(03)  cue=0                            flags=MULTIWORD|CONTENT      → 03 00 00 01
"um" (filler)   bucket=UNKNOWN(00)  cue=0                            flags=FILLER                 → 00 00 00 C0
"rabbit" (noun) bucket=TOPIC(01)    cue=0                            flags=HEURISTIC|CONTENT      → 01 00 00 08
"?"             bucket=STRUCTURAL(05) cue=QUESTION(0x0200)           flags=STRUCTURAL             → 05 00 02 80
(byte order shown as bucket, cue_lo, cue_hi, flags — little-endian cue)
```

---

## Storage Layout

```
semantic_compression/
  db/
    dictionary.lmdb/            general build   (max_dbs = 4)
        forward | reverse | facets | meta
    dict_stats.json             human report (histograms, counts) — not authoritative
    experts/
        registry.json           expert catalog + routing + fingerprints
        cooking/
            dictionary.lmdb/    forward | reverse | facets | meta
            dict_stats.json
            facet_overrides.tsv   domain-specific overrides (optional)
  data/
    facet_overrides.tsv           general-build overrides (global, non-empty starter)
```

### `experts/registry.json`

```json
{
  "registry_version": 1,
  "general_fallback": "db/dictionary.lmdb",
  "experts": [
    {
      "expert_id": "cooking",
      "name": "Cooking / Culinary",
      "path": "db/experts/cooking/dictionary.lmdb",
      "dictionary_format_version": 1,
      "facets_format_version": 1,
      "dictionary_fingerprint": "…",
      "priority": 100,
      "corpus_filter": { "channel_ids": [123, 456], "title_keywords": ["recipe", "kitchen"] },
      "build_stats": "db/experts/cooking/dict_stats.json",
      "built_at": "2026-06-19T00:00:00Z"
    }
  ]
}
```

`channel_ids` are authoritative; `title_keywords` are a soft/secondary signal.
`priority` breaks ties when multiple experts could match.

---

## Routing (descriptor-returning, defined precedence)

Selection returns a descriptor, not a bare path, so the encoder can stamp the
chosen identity into the artifact and routing is auditable:

```
DictionarySelection(dictionary_id, dictionary_version, fingerprint, path,
                    selection_reason, matched_rule, confidence)
```

Precedence (first hit wins):

```
1. artifact dictionary identity      (DECODE: always wins — never overridden)
2. explicit caller expert_id         (ENCODE)
3. exact channel_id → expert mapping
4. exact source mapping
5. weighted metadata rules (title/JSON-tag keywords) + expert priority
6. general fallback (always present)
```

v1 operational constraint (not a permanent law): **exactly one codebook per
stream** for encode and decode. Multi-profile / cross-domain / expert→general
translation are future work (§Future Updates).

---

## Build Order (verify each step; no System 2 work)

```
T1  config additions: FACETS_FORMAT_VERSION, NORMALIZATION_VERSION, BUCKET enum,
    LOGIC_CUE bit constants, FLAG constants (+UTILITY), LOGIC_SEED_LISTS (ordered),
    ACTION_LEXICON (curated METHOD list), STRUCTURAL cue map.
    Verify: no duplicate byte values/bits; seed lists normalize cleanly; name↔byte
            bijective; MANUAL/HEURISTIC exclusivity assertable.

T2  normalize.py: normalize_surface(surface) per the contract.
    Verify: Unicode/case/apostrophe/whitespace/hyphen/punctuation fixtures are
            deterministic and match tokenizer.py output forms.

T3  facets.py (stateless): assign_facet(surface, overrides) -> (bucket, cue_mask, flags);
    load_overrides(path) with exact:/normalized: + version check.
    Verify: ~50 hand-labeled tokens classify as expected; overrides beat every rule;
            multi-cue tokens (because/when) carry multiple bits, not AMBIGUOUS.

T4  builder integration: max_dbs 2→4; open facets + meta; write packed facet record
    per entry; write meta (versions, identity, override sha256); compute and write
    dictionary_fingerprint; accumulate histograms into dict_stats.json. Update
    spot_check() max_dbs and print each token's tag.
    Verify: tags_total == forward count; max_dbs=4 opens; faceting overhead ≤ 10%
            of baseline build time (record absolute timing separately).

T5  reader API (stateless): get_tag(env, id_bytes); get_meta(env); verify_fingerprint(env).
    Verify: round-trips; unknown id → None; fingerprint recomputed == stored.

T6  verify_facets.py (mirrors verify_library.py): every forward entry faceted; all
    bucket/cue/flag values in-enum; multiword handled by function not forced CONCEPT;
    all STRUCTURAL_IDS faceted STRUCTURAL; all ALL_FILLERS UTILITY=FILLER; histograms
    match dict_stats.json.

T7  expert harness build_expert.py: wrap scanner → frequency → builder with a corpus
    filter + output under db/experts/<id>/; write/refresh registry with fingerprint.
    Add --expert <id> to the main build entry point.
    Verify: small 2-channel expert builds end-to-end; registry validates; expert
            round-trips its own corpus byte-exact.

T8  expert_router.py: select_dictionary(metadata, registry) -> DictionarySelection
    per the precedence above, general fallback on no match.
```

---

## Verification Strategy (gate summary)

```
T1   enums/bits collision-free; seeds normalize; name↔byte bijective
T2   normalization deterministic across Unicode/case/apostrophe/ws/hyphen/punct
T3   hand-labeled table correct; overrides win; multi-cue ≠ AMBIGUOUS
T4   every entry faceted; max_dbs=4; overhead ≤10% baseline; fingerprint written
T5   get_tag/get_meta round-trip; fingerprint verifies
T6   zero orphan facets; in-enum; structural + filler families covered
T7   expert builds; per-expert lossless round-trip; registry valid
T8   router precedence correct; decode-identity wins; general fallback on no match
T9   normalization fixtures (incl. don't/can't, due-to/due to, "however,") match
T10  two builds from identical corpus+config+overrides+order → identical fingerprint
T11  corruption/mismatch (missing facets, wrong width, bad version, altered override
     hash, registry mismatch) all fail predictably
T12  consumer safety: not/if/unless/because/but never silently dropped by the
     reference context-assembly policy
T13  expert isolation: encode with A, decode with B → rejected via identity, never
     plausible corrupted text
```

---

## C/C++ Migration Notes

```
- Read tag fields by byte offset (above); do NOT memcpy into sc_tag_v1 (u16
  padding is implementation-defined). Ship sc_tags.h with the struct + inline
  read helpers + the bucket/cue/flag #defines (1:1 with config.py).
- facets and meta are named LMDB databases like forward/reverse:
  mdb_dbi_open(txn, "facets"/"meta", 0, &dbi); open env with max_dbs >= 4.
- facets_format_version, record_width, normalization_version, and identity live in
  meta; the C reader validates them from the DB and hard-fails on mismatch.
- assign_facet() and normalize_surface() are pure (input → output); the C port
  is a line-for-line translation of the precedence ladder and the contract.
- dictionary_fingerprint is the canonical content identity, layout-independent.
- System 2 attaches via additional id-keyed databases (affect/process); the
  System 1 facet record never widens.
```

---

## Risks + Mitigations

```
R1  Heuristic mislabels (residual). Mitigation: conservative METHOD + starter
    override file + MANUAL/HEURISTIC/AMBIGUOUS flags make misses auditable.
R2  Seed overlap. Mitigation: composable cue mask (multi-bit, not ambiguous);
    documented seed order; AMBIGUOUS only on bucket conflict.
R3  Expert duplication of common words. Mitigation: accepted (locked decision);
    common words are smallest entries; shared-common-dictionary is future work.
R4  Expert stream undecodable by base. Mitigation: artifact identity + fingerprint
    (N4); decode-identity wins; mismatch = hard fail (T8/T11/T13).
R5  `facets` vs JSON `tags` naming. Mitigation: FACETS_DB_NAME=b'facets' constant;
    comments disambiguate.
R6  Scope creep into System 2. Mitigation: facet record has no EPA/stage field at
    all; System 2 is a separate id-keyed db; reviewed as a hard rule.
R7  max_dbs not raised at a call site → open failure. Mitigation: grep all
    lmdb.open sites in T4; verify_facets opens 4 dbs as a gate; centralize names.
R8  Normalization drift between auto-tag and overrides. Mitigation: single
    normalize_surface(); normalization_version in meta; T9/T10 gates.
```

---

## Future Updates & Refactors (roadmap — not v1)

```
F1  Full dimensional split. Promote UTILITY and properties out of the flags byte
    into first-class fields if a wider record is ever justified:
        semantic_class  (ENTITY/ACTION/PROCESS/PROPERTY/RELATION/ABSTRACT/EVENT)
        utility_class   (CONTENT/FUNCTION/FILLER/STRUCTURAL)
    v1 keeps class in byte 0 + utility in flag bits to stay 4 bytes; the split
    is a versioned widening (bump facets_format_version) only if needed.

F2  Corpus-evidence METHOD detector (v1.1, no embeddings). The scanner records
    cheap contextual stats per token: preceded-by-"to", preceded-by-modal,
    post-subject-pronoun, imperative position, inflected-variant count. These
    promote words to METHOD with evidence instead of suffix guessing.

F3  Bucket promotions. MODIFIER (with a trustworthy assignment method) and
    NAMED_ENTITY (after casing + entity-extraction are formally designed —
    proper nouns are normalization-sensitive). Currently reserved 0x06–0x0F.

F4  expert_profile vs expert_dictionary. Distinguish two kinds of expertise:
        expert_profile     domain-specific interpretation/facets over STABLE shared IDs
        expert_dictionary  independent frequency-derived codebook (v1's model)
    Profiles let Context Assembly read a generally-encoded document through a
    domain lens (e.g. biological reading of "cell") without re-encoding the file.
    v1 ships expert_dictionary; expert_profile is the next layer.

F5  System 2 connected databases, keyed by the same id_bytes:
        affect    id → EPA prior (Evaluation/Potency/Activity)
        process   id → process-stage prior (Surov 6-stage)
        instance  occurrence → contextual interpretation (per-instance, dynamic)
    This is where EPA/stage live — connected, never packed into the facet record.

F6  Multi-codebook reads: one codebook + multiple semantic profiles, cross-domain
    documents, expert→general translation. Relaxes the v1 "one codebook per
    stream" constraint once the identity/fingerprint machinery is proven.

F7  Shared-common-dictionary optimization to remove common-word duplication
    across expert builds, if storage ever warrants it.
```

---

## Open Questions — Resolutions (from review)

```
O1  MODIFIER / NAMED_ENTITY: leave reserved (0x06–0x0F). Four+RELATION+STRUCTURAL
    buckets suffice for v1 and keep heuristics simple. (F3 when methods exist.)
O2  Facets in stream: NO — facets stay a side-table. BUT dictionary identity +
    fingerprint MUST accompany non-general artifacts (N4). The two are separate:
    don't emit facets; do identify the codebook.
O3  Corpus filters: channel_ids authoritative; title/JSON-tag keywords are a soft
    secondary signal; expert priority breaks ties.
O4  Multi-domain doc: general fallback in v1. One-codebook-plus-profiles later (F4/F6).
O5  Rebuild cadence: immutable expert releases built on demand / at explicit corpus
    milestones; never silently mutate a dictionary already referenced by artifacts;
    registry fingerprint validation on load.
```

---

## References

```
Project brief        CLAUDE.md
v0.4 spec            docs/compression/spec-v0.4.md
v0.3 spec/analysis   docs/compression/spec-v0.3.md ; v0.3-analysis.md
Builder / config     dictionary_builder.py ; config.py ; tokenizer.py ; check_apostrophes.py
SYSTEM1 status       SYSTEM1.md
EPA / stages         Surov (2022); Osgood et al. (1975)   [System 2 — connected dbs]
Repository           github.com/4waymedia/semantic-compression
```

---

## Version History

| Version | Date | Author | Changes |
|---|---|---|---|
| facets-db-draft-1 | 2026-06-19 | Claude | Initial: facets sub-db (bucket + logic role), assignment pipeline, override file, fully-separate experts |
| facets-db-draft-2 | 2026-06-19 | Claude (+ ChatGPT/Grok review) | Composable logic-cue mask; cue rename; SKIP removed (utility flags); conservative METHOD; multiword≠concept; identity + fingerprint + meta db (max_dbs=4); normalization contract; descriptor routing; T8–T13; id_bytes as System 2 join key; Future Updates roadmap |
