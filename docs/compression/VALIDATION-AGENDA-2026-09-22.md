# Validation agenda — what the dictionary's numbers do and do not support

> **Lane:** dictionary (`semantic_compression`) · **Opened:** 2026-09-22
> **Build in hand:** `elo-v5r3` · dict fp `fda06969…` · bundle fp `7237ac95…`
> **Evidence tags:** `[M]` measured this lane, with the command · `[R]` read from source or a
> published artifact · `[U]` unverified — believed, not shown.
>
> **This document is two things, and conflating them was its first defect** (caught by Paul,
> 2026-09-22 — it opened by saying "this is not a task list" and closed with a ranked list of
> six things to do).
>
> **Parts 1–5 are a CLAIMS REGISTER.** Each entry is something this lane asserts, plus the
> specific measurement that would make it safe to act on. An entry leaves when a measurement
> retires it, not when someone is confident. A register entry is not work; it is a statement
> whose support is recorded.
>
> **§6 is a WORK QUEUE** — a small set of things to actually do, drawn from the register but
> not the same as it. Most register entries never become queue items; they sit as recorded
> uncertainty, which is their job.
>
> **The gold set (§0.1) is neither** and has been moved out of the register for that reason.
> It is not a claim awaiting a test — it is the absence of an entire evidence class, and it is
> why nothing in Parts 1–5 can speak to correctness. It is a project with a cost, not a check.

---

## 0.1 The gold set — not a register entry, and the only item here with a cost

**No channel has ever been compared against human-adjudicated truth.** Not `facets`, `epa`,
`vfacets`, `wordclass`, `morph` or `neighbours`. `FACET_GOLD_GAP.md` has said so since August.

This sits outside the register because it is not a claim awaiting a measurement. It is the
reason every entry below is about whether a number *means what it says* rather than whether the
channel is *right*. A channel can be fully covered, deterministic, gate-passing, and wrong —
and nothing this lane currently runs would notice.

**It is a decision, not a task**, which is why it needs one from Paul rather than a queue
position. The shape and its cost:

| | |
|---|---|
| **Scope** | one channel to start, not all six. `facets` has the most consumers and the most prior art (`facet_gold_editor.html`, `facet_accuracy.py` already exist) |
| **Sample** | ~300 records, **stratified by id segment** — a flat sample is 40% phrases and 3% symbols, so it would measure the wrong thing (§1.2) |
| **People** | two adjudicators, independently. One adjudicator measures one person's opinion |
| **First output** | **inter-annotator agreement, before any accuracy number.** If two people cannot agree on what a bucket means, the channel's *definition* is the defect and accuracy is not yet a meaningful question |
| **Cost** | the adjudication hours. Everything else exists |

**The decision to make:** whether to spend that, on which channel, and whether "we have no
accuracy evidence" is an acceptable standing position for a dictionary other lanes are building
products on. It has been the acceptable position for six weeks by default rather than by choice.

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

### 1.1 → moved to §0.1

Not a register entry. See above: it is the absence of an evidence class and a decision with a
cost, not a claim awaiting a measurement.

### 1.2 The id space is heterogeneous and most published denominators ignore it `[M]`

`coverage_census.json` partitions: word 205,114 · phrase 174,628 · name 33,444 · symbol 12,980 ·
web_structure 7,367 · numeric 4,462.

A pooled denominator misreports every sparse channel — `epa` reads 54.0% pooled against 57.2%
where expected. But the correction has its own failure mode (1.3), so **neither figure should be
published alone**.

**Concrete residue:** `coverage()` in `elo-dictionary` returns the pooled number without naming
its denominator. The value is correct; the field does not say what it is a share of.

**Corrected 2026-09-22 (Paul).** An earlier draft here said *"make `coverage()` return the
segment breakdown."* **That is not implementable from the bundle** — the segmentation lives in
`coverage_census.json` and `nonlexical_terms_<name>.txt`, both **build_local** (§3.3, §0.1), so a
consumer holding only the published bundle cannot reproduce it. Doing it would mean shipping a
segment map, which is the `segments.bin` proposal this lane raised and withdrew on 2026-09-16 for
a reason that still holds: **no consumer is computing coverage numbers.** The register had drifted
back into proposing an asset to solve a hazard nobody has hit.

**What is actually owed, and it is small:** `coverage()` should name its denominator rather than
report a bare percentage — "54.03% of all 437,995 ids, every id type pooled", plus a note that
`epa` is not expected for the symbol and numeric segments and that the per-segment figures are in
`coverage_census.json`, which is build-local and available on request. `test_dictionary_api.py:128`
asserts `54.03` as a self-describing fact and should assert the labelled form.

No new asset, no rebuild, no revision bump.

### 1.3 "Coverage where expected" can be a tautology — **one confirmed** `[M]`

elo-sdm's rule: *a claim that holds under both behaviours is not a claim.* 100%-where-expected is
consistent with "measured every qualifying record" **and** with "cannot express absence over that
class". The number cannot separate them; only a mutation can.

Run 2026-09-22 (`probe_absent_tautology.py`):

| cell | verdict | evidence |
|---|---|---|
| `temporal` 100% | **TAUTOLOGY** `[M]` | 398/398 evidence-free probes → `PROCESS`; 0 of 28,167 qualifying records carry UNKNOWN; `_aspect_of_inflection` is total, final line `return TEMPORAL['PROCESS']` |
| `direction` 100% | **genuine near-total measurement** `[M]` | the builder *does* emit `DIRECTION['UNKNOWN']` on a substrate miss (`vfacet_builder.py:430`); covers 66,749 of ~66,807 qualifying because the substrate was populated for essentially all NAME/NOUN/VERB |
| `facets` bucket 100% | **total but HONEST** `[M]` | bucket never returns UNKNOWN on evidence-free input (0/398), but `facets.py:215` sets `FLAG['HEURISTIC']` on that exact path — published in `facets.names.json`, carried by **270,180 records (61.7%)**. A consumer can separate a finding from a guess, through `flags` not `bucket` |

**Three cells, three different answers, and the fast call was wrong twice.** `direction` looked
structurally identical to `temporal`; it is a real measurement. `facets` was called "a stronger
tautology than temporal's" on the first probe pass; the probe had read `bucket` and discarded
`flags`, and the real result **inverts the lesson** — facets marks its guesses and `temporal`
does not. Structural similarity is a hypothesis. So is a verdict from a probe that reads part of
what it measures.

**What this hands to `temporal`:** facets' `FLAG['HEURISTIC']` is the mechanism `temporal` lacks.
A defaulted `PROCESS` is currently indistinguishable from a measured one; a flag on the
`_aspect_of_inflection` fallback path would fix that without touching the assignment. Queued.

**Residual defect, unchanged:** bucket `0xFF` is declared and has no code path that emits it.
**Decided 2026-09-22 — retire it.** `0x00` UNKNOWN plus `FLAG['HEURISTIC']` already express both
"not known" and "guessed", both published and both populated; a third state existing only in
prose is what let two independent consumers decode `0xFF` as bucket 255.

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

### 5.5 Proposing an artifact to solve a hazard nobody has hit

Three instances, all caught by Paul, all in six days:

| proposed | actual problem | ratio |
|---|---|---|
| `segments.bin` — a tenth shipped asset, exporter, gate, revision bump | `coverage()` doesn't name its denominator | one label |
| bit-exact `vectors_digest` as a missing invalidant | vectors were recomputed, not changed; a bit-exact gate would cry wolf every rebuild | a quantised digest |
| "make `coverage()` return the segment breakdown" (§1.2) | same as row one — **and not implementable from the bundle** | same one label |

The shape: find a real discrepancy, then reach for the artifact that would make the *class* of
discrepancy impossible, without first asking whether anyone has been harmed by this instance.
Row three is the worst of the three because it re-proposed row one **inside the document written
to stop this lane from overclaiming**, five days after withdrawing it for the correct reason.

**The check, and it costs one question:** *who has hit this, and what did it cost them?* If the
answer is "nobody yet", the item is a register entry (recorded uncertainty), not a build. The
register exists precisely so that "this could bite someone" has somewhere to live that is not a
work queue.

### 5.4 The one method that keeps working: print both numbers

The read-back's `11 payload` vs `12 counted`. The vector probe's verdict against its own
`2.6e-07`. Both defects were found by two numbers disagreeing in the same output, not by anyone
being clever. **Where a value can be derived two ways, derive it two ways and print both.**

---

## 6. Work queue → moved to `semantic_compression/TASKS.md`

**Tasks left this document 2026-09-22 (Paul).** The register format has a "what would settle it"
field on every entry, which invites designing a fix at writing time — before anyone has checked
whether it is needed or possible. That is where all three §5.5 overreaches came from, including
the one committed inside this document.

§1.2 is the demonstration: three revisions of one section — ship a segment map, then a segment
breakdown, then (correct) a one-line label — to arrive at a task that was small from the start.
**Prose about a concern and the concrete next action are different artifacts and were fighting.**

So: concerns live here, tasks live in `TASKS.md`, one line each. The rule there is the useful
one — *if you cannot write the task in one line, you have not found the task yet; you have found
a concern.*

The queue as it stood:

**This is the task list**, and it is deliberately short. Everything in Parts 1–5 that is *not*
here is recorded uncertainty, which is a finished state — not a backlog item awaiting triage.
"Everything else" was the wrong closing line; there is no everything-else tier, because most of
the register is not work.

An item enters this queue only when it is blocking a person, or when a number this lane
publishes is being acted on and might not mean what it says.

| # | item | why it is queued, not just recorded |
|---|---|---|
| Q1 | **3.4** `FIXTURE_*` pins on `conformance-verbs.json` | ELO-Browser red; small; the cased-entry fix will otherwise pass green by omission |
| Q2 | **3.3** oracle set into the bundle | ELO-Browser holding `poc/conformance/` on it; prerequisite (§4c read-back) now done |
| Q3 | **1.3** `facets` `0xFF` probe | one published number, currently unexamined; probe exists, needs Part B wired |
| Q4 | **2.3** `morph_map` additive `dictionary_fingerprint` | fails **open** today — a consumer verifying correctly gets a false green |
| Q5 | **3.1** build-and-install gate | prerequisite for 3.2, and the whole class is invisible without it |

**Not queued, deliberately:** 1.4, 1.7, 1.8, 2.1, 2.2, 2.4–2.7, 4.1–4.4, and all of Part 5.
Each is real. None is blocking anyone this week, and putting them in a queue would turn a register
of honest uncertainty into a backlog nobody finishes — which is how the census `anomaly` cells
(§1.7) have sat unexamined since they were first written down.

**Separately, and above the queue:** §0.1 needs a decision, not a queue position.

---

*Every item above carries its evidence tag for a reason: this lane's recurring failure is not
being wrong, it is being more certain than the measurement supports. An item with `[U]` is not a
small item — it is an item nobody has checked.*
