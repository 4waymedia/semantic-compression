# semantic_compression packageability — 2026-08-05

> Answer to `Handoff — Which Submodules Can Ship?` (2026-08-05). Every mechanism
> below was verified at the named file:line (house rule §8). Method: grep the whole
> R-D-concepts tree for importers, then read the line. Disagreements are called out
> first, as requested.

---

## Layering correction (2026-08-05, per Paul)

**Dependency direction is `eloai-* → base packages`.** The Eloai systems are
**consumers** of foundation/base packages; the customized `eloai-verbalizer` should
**import** a base `verbalizer` package, not be the implementation. This inverts what
the code does today and what an earlier draft of this report recommended.

- Intended: base `verbalizer` (foundation, the dev home) holds the implementation +
  ops + conformance. `eloai-verbalizer` is the thin Eloai consumer/wiring on top of it.
  06-RecalEngine, PipelineLab, ExtractionPipeline, the MCP → import the **base**.
- Actual today (the drift): there is **no base `verbalizer/` dev home** (SYSTEMS.md
  "⚠ no dev home"; `ls verbalizer/` → absent). The only impl is
  `packages/eloai-verbalizer/src/eloai_verbalizer`, which imports **itself**
  (`__init__.py:11`). The `semantic_compression/verbalizer.py` shim then forwards the
  base name `verbalizer` **into** the consumer (`from eloai_verbalizer import *`) via a
  hidden `sys.path.insert` (verbalizer.py:20-22). That is the arrow pointing the wrong
  way — foundation name resolving to the Eloai consumer.
- The fix is therefore **not** "fold everything into `eloai-verbalizer` and delete the
  base name." It is: **establish the base `verbalizer` foundation package** (give
  Verbalizer the missing dev home), relocate the implementation + `verbalizer_ops` +
  `response.py` + the conformance file there, and make `eloai-verbalizer` a consumer
  that imports it. Consumers importing top-level `verbalizer` are then importing the
  foundation (correct); the shim disappears because `verbalizer` becomes a real
  installed base package, not because everyone moves to `eloai_verbalizer`.

The §4/runtime analysis below is unchanged by this — those are base packages that
Eloai systems consume, which is consistent with the corrected direction. Only the
Verbalizer verdict and the closing recommendation are rewritten accordingly.

---

## Headline

The high-value seam is **not** a new library — it is a **read-only runtime package**
that the whole tree already wants and cannot cleanly get. Everything else in §4 is
either already covered by the two curated packages or is a build-time tool with no
consumer outside this submodule. The verbalizer shim **cannot die yet** — it is
load-bearing across ~20 import sites in 4 subsystems, not the single consumer the
handoff estimated.

---

## Disagree with (most useful part, per §7)

- **§4 "tokenizer … already needed by callers — the L-SDF lane had to import it."**
  REFUTED as a *code* dependency. No module under `08-MCP-ToolInterface/eloai_lsdf/`
  imports `tokenizer`. What `eloai_lsdf/epa_adapter.py:35-44,100` actually reaches in
  for is the **verbalizer** (`import verbalizer as mod`), via `sys.path` into
  `semantic_compression/`. The sigil-survival check was a one-off inspection, not a
  standing import. So the tokenizer has **zero external code consumers** — it is not a
  seam today.

- **§5.2 "recal_engine.py:273 is the only consumer we found."** REFUTED. It is one of
  ~20 sites (list below). The shim is a cross-subsystem contract, not a single reach-in.

- **§4 candidate list generally.** Applying the handoff's own test ("something outside
  this submodule needs it") to real imports: **none** of `tokenizer`, `facet_reader`,
  `facets`, `meta_fields`, `epa_match`, `epa_validity`, `epa_phrase_composer`,
  `artifact_identity`, `stamp_meta`, `source_adapters` is imported by any module
  outside `semantic_compression/`. Every importer is internal (builders, verifiers,
  tests). They are library-internals and tools — leave them internal.

---

## Seams

```
tokenizer (tokenizer, normalize, caps_codec)  -> internal / already covered
    normalize + caps_codec already in compression-dictionary 0.3.1. No external
    importer of tokenizer.py (verified: grep whole tree, only comments + .venv).

facets reader (facet_reader, facets, meta_fields) -> already covered / internal
    facets in compression-dictionary; facet_reader + facet_builder in
    eloai-semantic-compression. All importers internal (facet_builder.py:39,
    verify_facets.py:121, meta_builder.py:23, test_*).

EPA (epa_match, epa_validity, epa_phrase_composer) -> internal (build-time)
    Importers all internal: hybrid_neighbors.py:129, meta_builder, test_epa_validity,
    test_hybrid_neighbors. Affective scoring is a BUILD-time surface; no runtime
    consumer. Do not package.

artifact identity (artifact_identity, stamp_meta) -> internal now / WATCH
    Importers internal: faiss_builder.py:63, verify_faiss.py:65, facet_builder,
    test_facets.py:46. BUT the map §1a says every artifact CONSUMER needs
    fingerprint/manifest verification. If a runtime read-surface is drawn (below),
    the fingerprint-verify half of artifact_identity belongs in it. Not yet.

format (format_adapters, source_adapters) -> already covered / build-time
    format_adapters already in eloai-semantic-compression. source_adapters is
    build-side (feeds pool_counter.py). No new package.

builders (build_*, *_builder, miners, counters, library_builder) -> internal (tools)
    Agree with §4: products do not build dictionaries. No external importer.
    Keep unpackaged.

RUNTIME READ-ONLY (NEW) -> the seam worth cutting
    compressor (decode path) + config + caps_codec + facet_reader, NO builders.
    See "Runtime vs build-time surface" below.
```

## Verbalizer

```
shim can die: NO. recal_engine.py:273 is NOT the last consumer.
  Top-level `verbalizer` name is imported at (verified file:line):
    06-RecalEngine/recal_engine/recal_engine.py:273,295
    06-RecalEngine/bench_invindex.py:125
    06-RecalEngine/tests/test_recall_seeds.py:100,110,135
    06-RecalEngine/tests/test_facet_recall_integration.py:24,40,49,64
    05-ExtractionPipeline/extraction_pipeline/steps/step14_verbalizer.py:67
      (+ generated copy packages/eloai-extraction-pipeline/src/.../step14_verbalizer.py:67)
    08-MCP-ToolInterface/eloai_lsdf/epa_adapter.py:100
    PipelineLab/verify.py:19, tests/test_smoke.py:15, pipeline_lab/substrate.py:70,
      pipeline_lab/adapters/{compression,_template,topic_tracking,segmentation,
      memory_pipeline,context_assembly}.py
    semantic_compression/test_verbalizer_family.py:16 (in-submodule)
  ~20 sites across 4 subsystems + PipelineLab. Shim dies only after all migrate to
  `import eloai_verbalizer`.
  NOTE: the shim is a hidden reach-in AND points the wrong way. verbalizer.py:20-22
  does sys.path.insert(0, packages/eloai-verbalizer/src) — invisible to check_wires
  (§1.2 pressure) — then `from eloai_verbalizer import *`, i.e. the FOUNDATION name
  resolves into the Eloai CONSUMER. Under the corrected direction (eloai-* consume
  base), that arrow is backwards. Retire it by standing up the base `verbalizer`
  package, not by extending it or moving consumers to `eloai_verbalizer`.

verbalizer_ops belongs: in the BASE `verbalizer` foundation package (NOT eloai-*).
  It is pure/substrate-free tier-2 ops + absorbs response.py's ladder
  (verbalizer_ops.py:20 `from response import ...`). It has a real CROSS-BOUNDARY
  consumer: the MCP (08-MCP-ToolInterface/mcp_tools/tool_api.py:880,929). Because it is
  foundation surface that many systems consume, it belongs in the base package that
  `eloai-verbalizer` (and the MCP, 06, PipelineLab) import — not inside the Eloai
  consumer. Ship response.py with it as the composition dependency. A standalone
  verbalizer_ops package would just re-split one contract.

conformance harness belongs: SHIP WITH the base `verbalizer` package as its gate.
  gen_verbalizer_conformance.py + verbalizer_conformance.json +
  test_verbalizer_conformance.py + test_verbalizer_ops.py exist so a foreign impl
  (the browser) can port against the JSON (verbalizer_ops.py docstring: "the browser
  can port against a conformance file"). The JSON travels with the base package as a
  shipped artifact; the generator stays in the base dev home. `eloai-verbalizer`
  inherits conformance by consuming the base — it does not carry its own copy.
```

## Runtime vs build-time surface

```
what a CONSUMER needs at RUNTIME: the DECODE/READ path only.
  compressor (decode_bin) + config (charset/tiers/primitives/PRIMITIVES map) +
  caps_codec + facet_reader (read facets) + the built dictionary artifacts.
  This is DECODE + READ. It never builds anything.

what is BUILD-time (must NOT ship to a product):
  dictionary_builder*, library_builder, faiss_builder, meta_builder, phrase_miner,
  ngram_counter, pool_counter, epa_match, build_* — tools. Products consume
  dictionaries; they do not mint them.

THE GAP: a decode-only consumer today must pull eloai-semantic-compression, which
  bundles facet_builder + verify_lossless + data/ (builder-adjacent) alongside
  compressor. The single cross-boundary decode consumer proves it:
  08-MCP-ToolInterface/mcp_tools/gateway/codec.py:154
    `from semantic_compression import compressor as backend`  # guarded reach-in
  It wants ONLY compressor.decode; it reaches into the submodule namespace to get it.

RECOMMENDATION: cut a REAL package (own folder, wholesale mirror) for the read path
  — compressor-decode + config + caps_codec + facet_reader. Because it is small and
  stable, it mirrors cleanly and escapes the curated-drift problem entirely (§2 —
  the two curated packages have no export-package.py and no per-file include, so
  their drift is tool-invisible). This answers §5.4: prefer SPLITTING the stable
  read surface into a real package over building a per-file include mechanism for it.
  The builder tools stay unpackaged; the curated eloai-semantic-compression shrinks
  toward build-time helpers only.
```

## Blocked on / disagree with

- **Dependency inversion (§5.6): one, in the shim.** `semantic_compression` imports no
  sibling subsystem (grep `from|import (recal_engine|extraction_pipeline|mcp_tools|
  memory_seed|pipeline_lab|eloai_lsdf|...)` inside the submodule → 0 matches) — clean on
  that axis. BUT the verbalizer shim inverts the intended base←consumer direction: it
  reaches `sys.path.insert` into `packages/eloai-verbalizer/src` and re-exports the
  Eloai consumer under the foundation name `verbalizer`. Base should not resolve to
  eloai-*. Unwind by standing up the base `verbalizer` package (its missing dev home).

- **codec.py:154 reach-in.** Low-priority but real: it should prefer the installed
  package (`ELO_CODEC_MODULE` env already allows this) over
  `from semantic_compression import compressor`. Folds in once the read-only runtime
  package exists.

- **Not blocked.** All five §5 questions answered from code. The one judgment call is
  §5.4 (per-file include vs split): I recommend split for the read surface, keep
  per-file include off the table — the read surface is stable enough to be its own
  repo-clean package.

## One-paragraph recommendation

Do three things, in order: (1) draw ONE new seam — a real, wholesale-mirrored
**read-only runtime package** (compressor-decode + config + caps_codec + facet_reader)
— because it is the surface the whole tree reads and the only cross-boundary reach-in
(`codec.py:154`) that a package would erase; (2) **stand up the base `verbalizer`
foundation package** (Verbalizer's missing dev home) — relocate the implementation +
`verbalizer_ops` + `response.py` + the conformance JSON there, make `eloai-verbalizer`
a thin **consumer** that imports it, and let the ~20 top-level-`verbalizer` importers
resolve to that real base package so the `semantic_compression` shim (which currently
re-exports the Eloai consumer under the foundation name — the inverted arrow) can be
deleted; (3) leave the rest internal — the §4 EPA/facets/tokenizer/artifact-identity
candidates have no external importer, and the builders are tools. `eloai-* → base` is
the invariant; the only violation today is that shim.
```
