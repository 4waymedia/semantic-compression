# Dictionary lane — tasks

> Small, concrete, checkable. One line each. Why any of these matter is in
> `docs/compression/VALIDATION-AGENDA-2026-09-22.md`; **reasons do not belong here.**
>
> **Two rules for adding. Both exist because they were broken.**
>
> **1. One line.** If you cannot write it in one line, you have not found the task — you have
> found a concern, and concerns go in the agenda.
>
> **2. Cite `file:line`, read in the same sitting.** Not remembered, not inferred from a
> filename. Opening the file to get the citation *is* the check, and it is the only one that
> happens reliably.
>
> On 2026-09-22 two of three "Now" items named the wrong file. *"Add FIXTURE_* to
> `gen_verbalizer_conformance.py`"* — that file is a 13-line deprecation stub; the generator is
> `packages/elo-verbalizer/src/verbalizer/gen_conformance.py:249`. *"Add to `morph_map.json`"* —
> written by `Reasoning/elo_reasoning/morphology/sweep.py:365`, another lane's tree. Both were
> written from memory of a filename. One `Read` each would have caught both.
>
> The general form: this lane tags **findings** with measured/read/unverified and tags nothing
> else. Tasks, proposals, verdict branches and code comments carry no evidence marker, and that
> is precisely where the errors are. A task is a claim about the repository. Cite it like one.

## Now

- [ ] **reasoning lane:** name one known-VETOED morph pair so `morph.vetoed_pair` can be
      pinned. Without it a port whose fnv1a64 disagrees returns None where the reference
      returns False, and nothing catches it. *(`conformance/INDEX.json` → `not_yet_pinned`)*
- [ ] `wordclass()` returns `classes` as a **set** (`api.py`, `_chan_record` decode) — return
      a sorted list. A language-neutral contract cannot carry a Python set repr; the
      conformance serialiser works around it today.
- [ ] Carry `corpus[].license` from the build YAML into `manifest.json` at
      `semantic_compression/build_from_spec.py` — a published bundle currently **cannot state
      the terms of the corpora it was derived from**. `PACKAGE.md` says so explicitly.
- [ ] Ship `INDEX.json` (the build list) and the four oracles in the bundle, or expose
      `builds()` from the package — both are authoritative and unreachable to a consumer.
- [ ] Confirm r3 restored: `Remove-Item` its `PACKAGE.md` + `conformance/`, then
      `publish_dictionary.py --verify dist\dictionary\elo-v5r3` must print no
      `UNDECLARED ATTACHMENT` line.
- [ ] **browser:** drop `handoffs/2026-09-24-TEMPLATE-conformance-runner-for-elo-rs.rs` into
      `src-tauri/src/`, fill the `todo!()`s, `cargo test conformance`. Report green OR red.
- [ ] Regenerate `conformance-verbs.json` (verbalizer generator now emits `fixture_*`);
      tell ELO-Browser so they can drop the hardcoded pin in `conformance_recase.rs`.
- [ ] `build_tokenizer.py --profile full` for elo-v5, then
      `gen_ids_oracle.py --build elo-v5 --profile full`. *(browser panics on the missing
      `poc/conformance/elo-v5/ids.json`; only `compact` and `tiny` tokenizers exist)*
- [ ] Retire `facets` bucket `0xFF` from the contract at
      `semantic_compression/export_browser_assets.py:159` — it is declared, unreachable, and
      has already caused two consumers to decode it as bucket 255. `0x00` UNKNOWN plus
      `FLAG['HEURISTIC']` already carry both meanings.
- [ ] Give `temporal` a `FLAG['HEURISTIC']` equivalent — a defaulted PROCESS is currently
      indistinguishable from a measured one (`semantic_compression/vfacet_builder.py:224`).
      *This is the thing worth copying FROM facets, which marks its guesses.*

~~Give `assign_facet` a decline path~~ — **WITHDRAWN 2026-09-22 before implementing.**
`facets.py:215` already sets `FLAG['HEURISTIC']` on exactly that path; the flag name is
published in `facets.names.json` and 270,180 records (61.7%) carry it. A decline path would
duplicate the marker and destroy a deliberate rubric default (concrete→TOPIC). The task came
from a probe that read `bucket` and discarded `flags`.

## Next build

- [ ] Declare the 4 oracles in `asset_registry`, write them into the bundle tree, let G9/G10
      accept them, align their fingerprint key on `dictionary_fingerprint`.
- [ ] Tell browser when that lands so they can delete `poc/conformance/`.
- [ ] Add `morph.names.json`: key format, veto hash shape, τ pair, `invalidants`,
      `rebuild_policy: restamp`.

## Owed, unscheduled

- [ ] Build-and-install gate: build the wheel, install clean, import from an unrelated dir.
- [ ] Remove the 6 `sys.path.insert(0, '.')` from `compression_dictionary` *(after the gate)*.
- [ ] Derive `STATUS.md`'s Standard / Liveness / Updated from `STANDARD.json`.
- [ ] Narrow `except Exception` at `vfacet_builder.py:350` to a stable refusal code.
- [ ] Publish which `epa` absence encoding is authoritative (`.bin` NaN vs LMDB no-row).

## Waiting on someone else

- [ ] mneme: `open_db(b'templates', create=False)` + reachable fallback. *(blocks every
      `SemanticEncoder` consumer)*
- [ ] reasoning: sweep entry-point name; quantised `vectors_digest` + τ-margin.
- [ ] browser: confirm migration to `meta.dictionary_fingerprint`.

## Needs a decision from Paul, not a task

- [ ] Gold set: which channel, and is "no accuracy evidence" an acceptable standing position?
      See agenda §0.1 for shape and cost.

---

## Done

- [x] 2026-09-25 **`elo-v5r5` published + promoted** — `PACKAGE.md` and `conformance/**`
      declared under `attachments` with `attachments_fingerprint`; `BUNDLE.json` written
      last; read-back and `--verify` cover both maps in both directions. Payload
      byte-identical to r4 (same `bundle_fingerprint`, by construction). Found by ELO-Browser.
- [x] 2026-09-25 **LLM contract deprecated** in root + lane `CLAUDE.md`; `gen_ids_oracle.py`
      refuses; `conformance_ids` to be retired, not parked. Paul: "we have NO LLM contract."
- [x] 2026-09-25 `conformance generate` refuses a published directory. r3 had been mutated in
      place by this lane's standalone tools, twice.
- [x] 2026-09-25 **`elo-v5r4` published** — 13 payload files (+`epa.names.json`,
      `neighbours.names.json`), `PACKAGE.md`, `conformance/`, 11 gates, one clean run.
      The two runs before it crashed on `NameError`s in my hooks *after* every gate
      passed — names reached from scopes I had not read, one of them documented as a trap
      in the same file ten days earlier.
- [x] 2026-09-24 **Conformance suite** — `compression_dictionary.conformance`: 32 vectors
      across 18 verbs, generated by the reference, shipped in every bundle under
      `conformance/`, reference passes 32/32, `uncovered: none`. First run caught two
      suite-vs-itself bugs (set ordering in `wordclass.classes`; `require` missing from
      `Dictionary`).
- [x] 2026-09-24 **`neighbours()` and `morph()` verbs** on the reference — the two
      bundle-only channels the LMDB-backed API could not reach. Plus `has()`, `require()`,
      `fnv1a64()`. Every shipped channel now has a reference implementation.
- [x] 2026-09-24 `PACKAGE.md` generated per bundle from the registry; `PACKAGE-CONTENTS.md`
      native wire contract; `purpose`/`not_for` on every registry asset.
- [x] 2026-09-24 `gen_ids_oracle.py` — the tokenizer oracle had no generator.
- [x] 2026-09-22 `dictionary_identity()` in `compression_dictionary` — one source for the
      six-field pin block, read from `STANDARD.json` + the published `BUNDLE.json`.
- [x] 2026-09-22 `probe_absent_tautology.py --field facets` — **bucket is a tautology**,
      0/398 evidence-free probes decline.
- [x] 2026-09-22 `coverage()` states its denominator; test asserts the labelled form.
- [x] 2026-09-22 `probe_absent_tautology.py` — `temporal` 100% proven a tautology.
- [x] 2026-09-22 `probe_vector_identity.py` — vectors recomputed (2.2 ULP), not changed.
- [x] 2026-09-21 `STANDARD.json` split into `payload` / `manifests` / `build_local`.
- [x] 2026-09-21 Publish read-back: `promote` verifies the bundle from its own directory.
- [x] 2026-09-20 Channel oracles stamp `bundle_id` + `package_revision`.
- [x] 2026-09-18 `bundle_fingerprint` carried into `STANDARD.json`.
- [x] 2026-09-17 `create=False` on read-only envs (`compressor.py`, `verify_lossless.py`).
- [x] 2026-09-17 `coverage_classed_words` excludes `OTHER`.
- [x] 2026-09-16 `codec_policy_version` in the codec oracle; one verdict per `--check`.
- [x] 2026-09-16 `require()` consults bundle channels, not just LMDB sub-dbs.
- [x] 2026-09-16 Revision-reason gate (string equality only).
