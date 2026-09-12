# STATUS + AUDIT — elo-v5, the first full build — 2026-09-11

> **Lane:** dictionary · **Goal:** a complete dictionary with every declared asset built,
> exported, contracted, gated and published in one cascade run.
>
> **Method note.** Everything below is tagged **[M]** measured from an artifact or a run,
> **[R]** read from source, or **[U]** unverified — written but never executed. My Linux
> sandbox has been dead since 2026-09-10 (a Sept 8 Windows update broke the workspace
> mount), so since then I write and Paul runs. That distinction has mattered: four of
> today's defects were caught by gates, and two were caught by Paul's terminal rejecting
> YAML I had only read.

---

## 1. Where elo-v5 actually is — 7 stages of 14 **[M]**

From `db/builds/elo-v5/assets_pipeline.json`:

| stage | | state |
|---|---|---|
| 1 facets · 2 meta-L1 · 3 epa · 4 meta-L2 · 13 vfacets | | **done** |
| 6 browser-vocab · 7 browser-epa+facets | | **done** |
| **5 denotative** | vectors | **FAILED** — `no such column: frequency` |
| 8 neighbours · 17 morph · 15/16 wordclass | | **never reached** |
| 9 registry · 10 stamp · 11/12 verify · 14 census | | **never reached** |

**Identity:** `fda069696b43e0f3…`, 437,995 entries, char-4, status `staged`.
**Staging:** `db/builds/elo-v5/bundle/` — 7 files, no longer the browser's tree.

### What the partial build already proves **[M]**

- **The facets/utility fix is real in a fresh build**, not a patch:
  `CONTENT 422,958 · STRUCTURAL 14,424 · FUNCTION 541 · FILLER 72`. Under v04 the
  STRUCTURAL count was **30**.
- **`generated_by` is derived**: `semantic_compression/export_browser_assets.py`. It
  said `ELO-Browser/tools/…` on v04r2, after the code had moved.
- **`assets.meta.json` declares what that stage wrote** — six files including
  `vfacets.names.json`, which shipped unbound for a month — and carries its
  `completeness.scope` block naming `BUNDLE.json` as authoritative.
- **Oracles are excluded from the bundle.** `epa.json`/`facets.json`/`vfacets.json` were
  written to `oracle_out` and are absent from `files` — the `path.parent == a.out` guard
  works.

---

## 2. Bugs — CLOSED and verified by execution **[M]**

| # | bug | evidence it is closed |
|---|---|---|
| 1 | `facets` UTILITY wrong on 14,394 surfaces — CSS/HTML/JS labelled CONTENT | v5 histogram above |
| 2 | `assets.meta.json` claimed files a later stage rewrote | G9 refused v04r2; v5's manifest lists only its own 6 |
| 3 | G11 threw `NameError`, rendering "could not run" as "failed" | now reports GATE ERRORED and still refuses |
| 4 | `coverage()` hardcoded "14,394 records carry the wrong UTILITY" in runtime output — false from r2 on | note now states the principle, no counts |
| 5 | Three stages gated on suite names no preset contained (`wordclass`, `morph`, `census`) — each skipped on every build while printing "off (not in suite)" | dry-run shows all three live; a name the suite cannot resolve now aborts |
| 6 | `__all__` omitted the entire verbs API | `selftest` passes; `import *` gets them |
| 7 | `cli.py` unrunnable as a script path | all three invocations work |
| 8 | publish read the bundle out of `ELO-Browser/` | v5 stages to `<build>/bundle/` |

---

## 3. Bugs — OPEN

### 3.1 BLOCKING the build

**B1 — `meta.db` has no `frequency` column; `denotative_index` queries it. [M]**
`meta_builder.COLS` has never contained it in this tree; nothing writes it. Two modules,
one implied contract, nothing keeping them equal — the nine-asset-lists defect in SQL.
v04's index exists because it was built before one of the two moved.
**Fixed [U]:** rank derived from `(tier, id)`, which *is* the frequency ranking by
construction. Coverage returns `n/a (no counts)` rather than a fabricated ratio.

**B2 — a SKIPPED stage was not fatal. [M]**
Missing `sentence_transformers` made stage 5 report SKIPPED; SKIPPED is not FAILED, so
the run continued and died at stage 8 with "index not built" — the true cause four
stages back and knowable in 50ms.
**Fixed [U]:** every declared dep of every selected stage is checked before any stage
runs, naming the pip install.

**B3 — WITHDRAWN. Not a bug. [M]**
I claimed `morph_vetoes.bin` is written to `elo_reasoning`'s package data and that
publish would refuse. **False.** `sweep.py:381`:

```python
veto_path = os.path.join(os.path.dirname(out_path), "morph_vetoes.bin")
```

The vetoes are written **beside `--out`**, so stage 17's
`--out <build>/bundle/morph_map.json` puts both files in the bundle already.

**How I got it wrong:** I read `_VETO_PATH = data_path("morph_vetoes.bin")` in
**`lemma.py`** — the READER's default location — and asserted it about the WRITER. Fifth
instance this week of the same error: a property of an artifact inferred from something
other than the thing that produces it. The rule has a name in `LANES.md` now and I still
broke it, inside an audit whose own method note warns about it.

**B4 — stage 17's module path. NOT BLOCKING. [M]**
`MORPH_SWEEP = "elo_reasoning.morphology.sweep"` and the import check passes with
`packages/elo-reasoning/src` on `PYTHONPATH` (verified: `sweep importable: True`). So
stage 17 runs as wired. A repo-wide glob finds `morph*` only under `Reasoning/` and
`packages/elo-reasoning/`, so the physical move has not happened — but that is an
OWNERSHIP tidiness item, not a build blocker, and the module form is what the ruling
asked for either way ("expose it as a declared build entry point").

### 3.2 CORRECTNESS, not blocking

**B5 — `wordclass` PASS 2 non-determinism. [M, fix U]**
Three builds, three channels (`8dadf64e` / `6c9d52c7` / `8d7989aa`), non-absent count
identical at 274,202 every time. Cause: a `set` of candidate singulars iterated with
`break`; string hash order varies per process.
**Fixed [U]** (ordered list) **and narrowed [U]**: PASS 2 now writes only
`inherent_number = PL`; it no longer carries `proper`, countability or the class mask
across a guessed link. **Unproven until two builds produce one sha.**

**B6 — PASS 2 still does not consume `morph`. [R]**
The validated same-lemma map exists and the plural rule still guesses. This is the actual
fix for `roses`→`ros`; the ordering fix only made the guess reproducible.

**B7 — `neighbours` ships with no contract file. [M]**
`asset_registry` self-test prints it every run. CSR layout, `k`, `min_sim` and an
80-byte header this lane has misread twice live only in the exporter's source.

**B8 — reasoning's gate reads `assets.meta.json`, which no longer ships. [M]**
It existed in one published bundle of three, and only by accident. `BUNDLE.json` is now
the manifest of record and the stage manifests are excluded from payload. Owed by
reasoning: read `build` + `source_build_fingerprint` from `BUNDLE.json`.

**B9 — `templates` is declared and built by nothing. [M]** Registry records the reason.

### 3.3 UNEXPLAINED — needs an answer before elo-v5 is trusted

**Q1 — the id space moved. [M]**
elo-v5 is `fda06969…`; v04 is `b0164e50…`. The spec declares the **same three corpus
sources** as v04, and the deterministic-build claim says same corpus + same knobs ⇒ same
fingerprint. So either a corpus file changed since August (`word_frequencies.txt` is
regenerated; `Resources/books` may have grown) or a knob differs.
**Either answer is fine — it just has to be the one we think it is.** If the corpus
genuinely moved, elo-v5 is a new id space and nothing encoded against v04 decodes under
it without conversion.

---

## 4. Written but never executed **[U]** — the honest list

Everything in §2 is verified. These are not:

- the dep preflight (B2) and the `denotative_index` rank fix (B1)
- PASS 2's narrowing and the ordered candidate list (B5)
- `morph` in publish: `n_parallel` / `framed` / `self_describing`, `_read_self_described`,
  G1's n-parallel split, G2's framed-only gating, G7's no-denominator path
- `NOT_PAYLOAD` excluding the stage manifests
- stage 17 itself, and module-stage support in `_run` / `_stale`
- `CODEC_POLICY_VERSION` stamping into `manifest.json` and `BUNDLE.json`
- `_channel_census` capturing a pre-revision snapshot

**No publish has run since the registry gained `morph`.** The first one will exercise all
of it at once, which is why B3 and B4 should land first.

---

## 5. Actions, in order

**1. Unblock the build.** Install `sentence-transformers` + `faiss`; re-run. The ledger
resumes from stage 5. Confirm the rank line reads `rank source: id space (tier, id)`.

**2. Answer Q1 before anything is published.** Compare elo-v5's `corpus_fingerprint`
(`b80e87064c47be42…`) against v04's manifest. Same ⇒ a knob moved and the determinism
claim needs checking. Different ⇒ the corpus moved and elo-v5 is legitimately a new id
space. One command, and it decides whether v04-encoded data has a migration path.

**3. Nothing — B3 and B4 were both wrong.** Stage 17 runs as wired and puts both morph
files in the bundle. The only morph work left is the ownership move, which is tidiness.

**4. Prove B5 on a real build.** Two runs of stages 15+16, compare `wordclass.bin`. This
is the gate the spec makes step 2, and it outranks publishing.

**5. Publish and promote.** Eleven gates, `--verify` both directions, then
`dictionary_standard.py promote elo-v5 --allow-staged`.

**6. Then, and only then, the correctness work:** PASS 2 consuming `morph` (B6), a
contract file for `neighbours` (B7), and an owner for `templates` (B9).

---

## 6. The pattern worth naming

Four defects this week were the same shape: **two places that had to agree, with nothing
making them agree.** Nine asset lists. Five shipped-file lists. `meta.db`'s schema vs
`denotative_index`'s query. A stage table vs a suite preset. Each looked like a
one-off; together they are a missing habit, and the fix each time was to *derive* rather
than *restate*.

The counter-pattern is also worth keeping: **four of today's defects were caught by gates
written hours earlier**, not by review. G9 caught my own manifest bug, G11 caught its own
NameError, the export drift check caught a stale `config.py`, and the registry refused a
publish missing `wordclass`. The gates earned more than the fixes did.

**Still no gold set for any channel.** Every number in this report is coverage,
population, presence or identity. **None of it is accuracy**, and no amount of green
gates changes that.
