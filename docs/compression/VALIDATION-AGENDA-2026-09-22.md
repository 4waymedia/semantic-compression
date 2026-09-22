# Validation agenda — what the dictionary's numbers do and do not support

> **Lane:** dictionary (`semantic_compression`) · **Opened:** 2026-09-22
> **Build in hand:** `elo-v5r3` · dict fp `fda06969…` · bundle fp `7237ac95…`
> **Evidence tags:** `[M]` measured this lane, with the command · `[R]` read from source or a
> published artifact · `[U]` unverified — believed, not shown.
>
> This is not a task list. It is the list of things this lane **claims** and the specific test
> that would make each claim safe to act on. An item leaves this document when a measurement
> retires it, not when someone is confident.

---

## 0. The governing statement

**Every number this lane publishes is a property of the instrument, not of the world.**
Coverage, population, presence, entropy, effective cardinality, channel entries — none of them
is accuracy. No channel has ever been compared against a gold set, because none exists.

elo-sdm put the general form better than we had: *a green suite is evidence about the
instrument, not about the system.* Everything in Part 1 is a consequence.

The practical rule until a gold set exists: **do not let a coverage figure stand in for a
correctness claim in any document leaving this lane**, and mark every number with what it is a
share of.

---

## Part 1 — Measurement validity

### 1.1 No gold set for any channel — **the largest open item** `[M]`

Nothing in `facets`, `epa`, `vfacets`, `wordclass`, `morph` or `neighbours` has been scored
against human-adjudicated truth. `FACET_GOLD_GAP.md` has said so since August and nothing has
moved.

**Why it outranks everything else here:** every other item in Part 1 asks whether a number means
what it says. This one asks whether the channel is *right*, and no amount of the former answers
the latter. A channel can be 100% covered, deterministic, gate-passing and wrong.

**What would settle it:** a stratified sample per channel (by id segment — word/phrase/name/
symbol/web_structure/numeric — not a flat sample, see 1.2), adjudicated by two people
independently, with inter-annotator agreement reported. Start at ~300 records per channel;
report agreement before reporting accuracy. If two adjudicators cannot agree, the channel's
definition is the defect, not the data.

**Owner:** dictionary lane. **Blocked on:** nobody. This is a decision not to have done it.

### 1.2 The id space is heterogeneous and most published denominators ignore it `[M]`

`coverage_census.json` partitions: word 205,114 · phrase 174,628 · name 33,444 · symbol 12,980 ·
web_structure 7,367 · numeric 4,462.

A pooled denominator misreports every sparse channel — `epa` reads 54.0% pooled against 57.2%
where expected. But the correction has its own failure mode (1.3), so **neither figure should be
published alone**.

**Concrete residue:** `coverage()` in `elo-dictionary` still returns the pooled number without
naming its denominator. The value is correct; the field does not say what it is a share of.

**What would settle it:** make `coverage()` return the segment breakdown, or refuse to return a
single number. Owed.

### 1.3 "Coverage where expected" can be a tautology — **one confirmed** `[M]`

elo-sdm's rule: *a claim that holds under both behaviours is not a claim.* 100%-where-expected is
consistent with "measured every qualifying record" **and** with "cannot express absence over that
class". The number cannot separate them; only a mutation can.

Run 2026-09-22 (`probe_absent_tautology.py`):

| cell | verdict | evidence |
|---|---|---|
| `temporal` 100% | **TAUTOLOGY** `[M]` | 398/398 evidence-free probes → `PROCESS`; 0 of 28,167 qualifying records carry UNKNOWN; `_aspect_of_inflection` is total, final line `return TEMPORAL['PROCESS']` |
| `direction` 100% | **genuine near-total measurement** `[M]` | the builder *does* emit `DIRECTION['UNKNOWN']` on a substrate miss (`vfacet_builder.py:430`); covers 66,749 of ~66,807 qualifying because the substrate was populated for essentially all NAME/NOUN/VERB |
| `facets` bucket 100% | **UNEXAMINED** `[U]` | the `0xFF` sentinel is declared and has never been emitted on any build |

**The lesson worth keeping:** `direction` looked structurally identical to `temporal` and the
fast call — "same shape, same verdict" — would have been wrong. It took reading two more files.
Structural similarity is a hypothesis, not a finding.

**Next:** run `--field facets`. Part B is not yet wired for it; wiring it is the work.

### 1.4 `direction`'s `NEUTRAL` may be a fallback wearing a verdict's name `[U]`

`NEUTRAL` is 58% of `direction`'s values and is a **distinct code from `UNKNOWN`**. Whether the
LLM sweep that populated the substrate emits NEUTRAL as a real assessment or as its
no-evidence answer is unknown from this side.

This is precisely the distinction `polarity` already needed `polarity_known` to express — and
`direction` has no such bit. If NEUTRAL is a fallback, `direction`'s 99.9% collapses to ~42% of
qualifying and 1.3's verdict for that cell flips.

**What would settle it:** inspect the sweep prompt and its cached verdicts, or add a
`direction_known` bit and rebuild. **Owner:** reasoning lane (owns the substrate sweep), with
this lane's contract.

### 1.5 `agency` at 20.4% is a real measurement, and the "anomaly" is explained `[M]`

Carried as an open anomaly for a week: *"`direction` 100% vs `agency` 20.4% from one substrate."*

Resolved. `vfacet_substrate.py:88-99` stores an entry when **either** field has a non-UNKNOWN
verdict — *"an UNKNOWN cache entry is noise, not knowledge."* Both are assigned from one tuple
(`vfacet_builder.py:426`), but direction is almost always the field supplying the verdict that
caused the row to be stored. Agency's sparseness is the data, not a bug.

**Retires from this document.** Recorded because it was open and because the explanation is
non-obvious.

### 1.6 Class membership that is definitional, not measured `[M]`

- **`wordclass.OTHER` = 174,628 = the `phrase` segment, to the record.** Not a residual class —
  the phrase segment wearing a class name.
- **`coverage_classed` (261,575) is 66.8% `OTHER`.** A consumer sizing a word-level gate by it
  overestimates by ~3×. `coverage_classed_words` (86,947) added 2026-09-17, lands next export.
- **Effective cardinality:** only `wordclass.dominant` over the word-level classed set (4.03) can
  rank. `utility` is 1.17, `facets.bucket` ~2.0, `polarity` 2.0 — filters, not sort keys. The API
  enforces this (`eff >= 3.0`); the prose did not, and was corrected.

**Generalised concern:** a class assigned by construction inflates every statistic computed over
it. `OTHER` was found by noticing two numbers matched exactly. **Nothing systematically checks
for this.** A cheap standing check: for each categorical field, test whether any value's count
equals a segment count exactly.

### 1.7 Census `anomaly: true` cells are unexplained `[U]`

Many `by_segment` entries carry `"anomaly": true` and nobody has worked through them. They are
off-class values — a field carrying a value in a segment where it is not expected. Unknown
whether they are real signal, scoping errors, or the same definitional-class artefact as 1.6.

**What would settle it:** enumerate them, sample each, classify. Not yet started.

### 1.8 `saturated` is reported and never interrogated `[U]`

`agency` has `saturated: true`, `direction` `saturated: false`. Nothing in this lane reads the
field or acts on it, and its definition is not in any doc I can find.

---

## Part 2 — Contracts, identity, absence

### 2.1 `epa` absence has two encodings and one published rule `[M]`

`epa.bin` fills unrated slots with `NaN,NaN,NaN` (`export_browser_assets.py:242`). The LMDB
writes **no row at all** (236,645 of 437,995). Both read as "no value" through `epa()`, so a
reader that checks for the wrong one **is wrong silently** — it gets the right answer for the
wrong reason until it does not.

**What would settle it:** publish which representation is authoritative per surface, and state
the other as derived. elo-sdm's I12 analogue: one projection, not two that agree.

### 2.2 Declared-but-never-emitted absent sentinels `[M]`

| sentinel | status |
|---|---|
| `facets` bucket `0xFF` | declared, never emitted on any build `[M]` |
| `wordclass` all-zero | declared; `records_written` == count, so never emitted `[M]` |
| `epa` NaN | declared; emitted in the `.bin`, never in the LMDB `[M]` |

A contract describing a mechanism the data does not use misleads every porter who implements it —
and **05-ExtractionPipeline and this lane both shipped a bug reading `0xFF` as bucket 255**, on
a build where it never appears. The contract was right and unexercised; that is worse than
wrong, because nothing fails.

**What would settle it:** mutate a record so the absent path must fire (the 1.3 method), or
retire the sentinel and declare the actual rule.

### 2.3 One identity, three spellings, one of them wrong `[M]`

In `BUNDLE.json`: `source_build_fingerprint`, `provenance.manifest_dictionary_fp`, and (over in
`STANDARD.json`) `dictionary_fingerprint` all carry the same value. An `identity` block naming
the canonical field was added 2026-09-16 and lands next publish.

**Worse, unfixed:** `morph_map.json`'s `meta.bundle_fingerprint` carries the **dictionary**
fingerprint (`fda06969…`), not the bundle fingerprint (`7237ac95…`), and its `note` directs
consumers to `meta.fingerprint`, which does not exist. A consumer verifying the asset set against
the field named for it **gets a false green**.

Correction sequence agreed with ELO-Browser (additive first, so their live gate never breaks):
add `meta.dictionary_fingerprint` → they migrate → then correct `bundle_fingerprint`, announced.
**Owner:** joint — the generator is reasoning's sweep, the contract is this lane's.

### 2.4 `morph`'s invalidants do not cover what invalidates it `[M]`

Declared invalidants: `corpus_fingerprint` + the vectors. Measured 2026-09-22: between v04 and
v5 the corpus fingerprint was **identical** while the vector file differed — though only at
**2.2 ULP**, i.e. the vectors were *recomputed*, not changed.

Consequence: scores identical to within ~1e-06, so only pairs inside that band of τ can move.
One veto in 2,332,632 pairs is exactly that size of effect.

**A bit-exact `vectors_digest` would be the wrong fix** — it would refuse a carry-forward on
every rebuild while reporting a change that is not one, and a gate that cries wolf gets turned
off. What is needed: a digest **stable under recomputation** (quantise before hashing) plus
explicit **τ-margin** handling for pairs inside the noise band.

### 2.5 `morph`'s `restamps[]` provenance was dropped `[R]`

Between v04 and v5 the array went to `null`, so a map that was *carried forward and checked* is
no longer distinguishable from one that was *re-derived*. Same defect class as 2.6.
Recommendation (reasoning's): run `restamp` when the corpus fingerprint matches, and treat
`restamps: null` on a superseding build as a lint failure.

### 2.6 A revision can still reuse its predecessor's reason `[M]`

`elo-v5` r2 and r3 carry a **byte-identical** `content_change`, 30 seconds apart, so the log
cannot say what r3 changed. A gate now refuses an exactly-equal reason — **but only string
equality.** "update" defeats it. Disclosed, not solved.

### 2.7 `templates` is declared and built by nothing `[R]`

In the registry with a stated reason, so it stays visible as owed rather than forgotten. Its
absence is also a live consumer trap: on a **read-only** env, `open_db(b'templates')` with
py-lmdb's `create=True` default raises `ReadonlyError: mdb_dbi_open: Permission denied`, naming
neither the sub-db nor the cause. Cost 04-Verbalizer a day. **mneme owns the fix.**

---

## Part 3 — Packaging and distribution

### 3.1 No build-and-install gate `[M]`

`export-package.py --check` is a **drift** gate (source vs package copy). Nothing builds a wheel,
installs it into a clean environment and imports it **from an unrelated directory**.
`test_dictionary_api.py` inserts `packages/*/src` on `sys.path`, so it tests the shipped source
and never a wheel: packaging faults — missing `__init__` export, a data file not included, a
module that only resolves from the repo — cannot be caught by any test in this lane.

elo-sdm's `verify_package.py` does exactly this and found two import-time bugs a green suite
structurally could not see.

### 3.2 Six `sys.path.insert(0, '.')` in the shipped wheel `[M]`

In `packages/elo-dictionary/src/compression_dictionary/`: `dictionary_builder.py`,
`dictionary_builder_v03.py`, `generate_essentials.py`, `phrase_miner.py`, `ngram_counter.py`,
`word_frequency_counter.py`.

**The caller's current working directory, prepended to `sys.path`, from inside an installed
library.** A consumer's `config.py` silently shadows ours. 05 removed this class from
`elo-extraction` in 0.18.0; this lane had it six times in the package it has been telling every
lane to import.

Latent — these are builder modules, so a reader-API consumer never imports them. **Not
blind-fixed:** module-scope statements with imports beneath them, and removing them unrun could
break the builders six ways. Goes in with 3.1, which is the only thing that would prove the fix.

> **Standing pattern worth naming:** "latent, not live" has now been this lane's verdict on the
> `create=False` instances, these six, and the unused sentinels. Three times is not reassurance;
> it is a description of how this package was assembled.

### 3.3 Oracles are generated into a consumer's repository `[M]`

All four (`epa/facets/vfacets/vectors.json`) are written to `ELO-Browser/poc/conformance/`, so
the browser's conformance inputs sit where this lane's publish gates cannot see them — the 09-10
exporter defect with the direction reversed. Agreed fix: ship them in the bundle and enter them
in `BUNDLE.json`'s `files` map. **Browser is holding `poc/conformance/` until it lands.**

Also: the oracle set is not schema-uniform — `vectors.json` keys `dictionary_fingerprint`, the
three channel oracles key `fingerprint`.

### 3.4 Fixtures that cannot see an encoder change `[M]`

`conformance-verbs.json` pins build + fingerprint only, and **every expected value in it is
encoder output**. `BUNDLE.json`'s own codec note says a fixture pinning only build/fingerprint
cannot see an encoder change. The cased-entry fix will bump `CODEC_POLICY_VERSION` to 2 and
change those bytes under an unchanged build and fingerprint — passing green by omission.

Fixed for the codec oracle (`codec_policy_version`, 2026-09-16) and the three channel oracles
(`bundle_id` + `package_revision`, 2026-09-20). **Not yet for `conformance-verbs.json`** — needs
`FIXTURE_BUNDLE_ID`, `FIXTURE_PACKAGE_REVISION`, `FIXTURE_CODEC_POLICY`. This is one of
ELO-Browser's two reds.

### 3.5 The parent repo has no remote `[M]`

`git remote -v` is empty. The submodule is pushed to GitHub, so the dictionary source is backed
up; everything in the parent tree — `packages/`, `handoffs/`, and six other lanes — exists on one
machine only.

---

## Part 4 — Build machinery

### 4.1 `_EXEC_ORDER` is authored, not derived `[R]`

Integration's §4, still open: stage order is hand-declared, and the guard checks only that every
stage appears. It does not check that the order matches what the builders actually require.
A stage reordered relative to its true dependency passes. The cascade reorder that changed
`vfacets` data is the evidence this can happen.

**What would settle it:** derive the order from which sub-dbs each builder opens, or gate the
authored declaration against that.

### 4.2 `except Exception` at `vfacet_builder.py:350` `[R]`

After the cascade reorder, absence is impossible in a normal build, so the broad catch now hides
a condition that should refuse with a stable code.

### 4.3 The cascade reaches reasoning's sweep by path `[R]`

`MORPH_SWEEP` points at `Reasoning/morph_sweep.py`, a deprecation shim, reached by path — which
the 09-11 ruling explicitly said must not happen. Reasoning is providing a console-script entry
point; until then the shim stays, and it is recorded as debt rather than allowed to become the
design.

### 4.4 `STATUS.md` header fields must be re-edited on every publish `[M]`

`Standard`, `Liveness` and `Updated` were stale for four days and told consumers to pin a
superseded bundle. Per elo-sdm's rule — *a field that must be re-edited on every upstream publish
is a defect in the field, not a maintenance task* — they should be derived from `STANDARD.json`
at render time, or not name a value.

---

## Part 5 — Method

These are not code defects. They are the failure modes that produced most of Parts 1–4, and they
recur faster than the individual bugs do.

### 5.1 Claims about the instrument, stated as claims about the system

Publishing `temporal` at 100% as coverage. Telling ELO-Browser their finding was a misread.
Reporting "the vectors differ" from a bit-exact comparison whose worst delta was 2.2 ULP. Each
time the measurement was real and the sentence was about something else.

### 5.2 Reading the current artifact and assuming it is the one under discussion

Three times: a wrong owner in a handoff four lanes acted on; the `neighbours.bin` header misread
twice; quoting r3's `wordclass.names.json` at a finding filed against r1 — in which case **my own
source file credited the reporter with the bug I was denying.**

**Mitigation that is actually working:** report the build identifier with every measurement. It
is one string and it would have prevented all three.

### 5.3 Guards built from one observed instance match that instance's accidents

elo-sdm's: a pin guard written to match abbreviated fingerprints would have missed the next one,
which was full-length. Mine: the revision-reason gate matches string equality only, from exactly
two observed instances.

### 5.4 The one method that keeps working: print both numbers

The read-back's `11 payload` vs `12 counted`. The vector probe's verdict against its own
`2.6e-07`. Both defects were found by two numbers disagreeing in the same output, not by anyone
being clever. **Where a value can be derived two ways, derive it two ways and print both.**

---

## Priority

1. **1.1 gold set** — nothing in Parts 1–4 substitutes for it, and it has been deferred longest.
2. **3.1 install gate** — prerequisite for 3.2, and the class it catches is invisible today.
3. **1.3 `facets` probe** + **1.4 `direction`/NEUTRAL** — cheap, and they decide whether two more
   published numbers mean anything.
4. **3.3 / 3.4** — ELO-Browser is blocked on both.
5. **2.1 `epa` absence**, **2.3 `morph` identity** — live consumer traps, one of which fails open.
6. Everything else.

---

*Every item above carries its evidence tag for a reason: this lane's recurring failure is not
being wrong, it is being more certain than the measurement supports. An item with `[U]` is not a
small item — it is an item nobody has checked.*
