# Spec — Markup & Domain-Structure Semantics (FUTURE / direction)

> **Status: FUTURE, not a priority.** An outline to build out later, captured now so
> the idea isn't lost. It defines a *third* similarity channel — structural/functional
> role — alongside affect (`epa.bin`) and word-denotation (`neighbours.bin`), and it
> is the first concrete definition of the build-family `relations.bin` asset
> ([`spec-build-family.md`](spec-build-family.md) §1, listed there as a gap). Nothing
> here is built yet; it slots into the existing meta.db + relations model when picked
> up.

---

## 1. Why — two payoffs, one new channel

Markup tokens (`<button>`, `<nav>`, `.btn`, `container`) are `STRUCTURAL` — correctly
excluded from EPA (no affect) and the 768-d index (no word-denotation). But they carry
real semantics of a *different kind*:

- **Role similarity** — `<button>` ≈ `<a class="btn">` ≈ `<input type="submit">` ≈
  `[role=button]` ≈ a framework `.btn`. They are *not* synonyms in meaning; they play
  the same **functional role**. A semantic lookup for "a button" or "a menu" should
  return the family of elements that fill that role.
- **Containment / structure** — `<header>`, `<nav>`, `<main>`, `<aside>`, `<footer>`,
  `<section>`, `<div class="container">` are *regions/containers*; `<ul>>li`, `<table>`,
  `<form>` have known content models. Knowing this lets a **semantic summary** describe
  a page's structure (regions, containers, lists, forms) without an LLM.

This is a **third similarity axis**. Keep it distinct (the "distinct channels, distinct
use" rule): EPA = how it feels, neighbours = what it means, **markup-relations = what
role it plays / what it contains.**

---

## 2. Where it lives (grounded in what exists)

- **meta.db gains structural columns** for markup surfaces (deterministic, like the
  `complement` column): `element_category`, `element_role`, `is_container`,
  `is_interactive`, `is_void`, `content_model`, `framework_of`. Populated only for
  `utility=STRUCTURAL` domain surfaces; NULL for everything else (selective coverage).
- **`relations.bin` carries the association graph** — the build-family typed-relations
  asset. Edge types (small, closed set): `SIMILAR_ROLE`, `ALIAS_OF` (framework class →
  semantic element), `CAN_CONTAIN`, `IS_A`. This is both the "similar names" lookup and
  the structure graph. Coverage = markup/domain IDs only.
- **Opt-in domain overlay.** Enabled per build (browser / web / a framework pack),
  re-derived per build, bound to `build_id` like every other asset. A general-language
  build simply doesn't carry it.

Nothing new architecturally — it's meta columns + `relations.bin` + a domain toggle.

---

## 3. Data model (sketch)

```
meta.db (markup rows only)
  surface           "<button>" | "<nav>" | "container" | ".btn"
  element_category  interactive | sectioning | flow | phrasing | embedded | form | metadata | table | framework
  element_role      button | link | menu | region | list | textbox | image | container | …   (ARIA-aligned)
  is_container      bool        is_interactive bool        is_void bool ( <img>,<br> )
  content_model     what it may contain (category ref)
  framework_of      "" | bootstrap | tailwind | …          (for class tokens)

relations.bin  (typed edges, CSR by n, like neighbours.bin)
  SIMILAR_ROLE   button  -> {a.btn, input[submit], [role=button], .btn}
  ALIAS_OF       .btn    -> button           (framework class -> semantic element)
  CAN_CONTAIN    nav     -> {ul, ol, a, button}
  IS_A           section -> sectioning-container
```

Records mirror `neighbours.bin`'s shape (80-byte header + CSR + `u32 n` + `u8 typed
weight`), so the browser reads it the same zero-parse way. Edge type packed in the
weight byte's high bits, strength in the low bits.

---

## 4. Sourcing — deterministic authority tables (not learned)

Like `verb_complements.py`, this is curated + deterministic, re-derived per build, its
inputs fingerprinted:

- **Element taxonomy** from the WHATWG/MDN **content categories** (metadata, flow,
  sectioning, heading, phrasing, embedded, interactive, form-associated) + the void-
  element list + each element's content model. One authored table.
- **Framework maps** (Bootstrap `.btn/.card/.nav`, Tailwind component patterns) →
  semantic element / role. Curated tables, versioned per framework.
- **Similarity edges** are then *derived* deterministically: two elements are
  `SIMILAR_ROLE` if they share `element_role` (+ interactive flag), plus a small curated
  seed for cross-type cases (`button`↔`a[role=button]`↔`input[submit]`). No embeddings,
  no inference — reproducible from the tables.

---

## 5. Use case A — semantic element lookup (no LLM)

Query "button" or "menu" → resolve to `element_role` → walk `SIMILAR_ROLE` + `ALIAS_OF`
in `relations.bin`:

```
"button" -> button role -> { <button>, <input type=submit>, <a class=btn>,
                             [role=button], bootstrap .btn, tailwind btn-* }
"menu"   -> menu/nav     -> { <nav>, <menu>, <ul role=menu>, .navbar, .menu }
```

A graph walk over a binary asset — fast, deterministic, offline. This is what makes
"return the similar names for a menu or button" work.

## 6. Use case B — page-structure summarization

Over a compressed page stream, use `element_category` + `is_container` to reconstruct
the **region tree** — `header / nav / main / aside / footer`, containers, lists, forms —
and emit a structural outline the summary layer can narrate ("a page with a top nav, a
main article region containing three sections, and a footer"). Structure insight the
affect/denotation channels can't give.

---

## 7. Determinism & coupling

- **Deterministic (S1-class).** Authority tables in, edges out; byte-identical across
  runs; folded into `meta_fingerprint` / a `relations_fingerprint`.
- **Per-build, `build_id`-bound.** Like all assets — a browser build's markup relations
  are stamped to that build; consumers reject on `dictionary_hash` mismatch.
- **Selective coverage.** Only markup/domain IDs carry these records; `covered_ids ≪
  total` is expected (§4 of the asset-pipeline spec).

---

## 8. Non-goals & fit

- **Not affect, not word-denotation.** Never route markup through EPA or the mpnet
  index; this channel is role/structure. A CSS `.btn` has no affect and no synonym —
  it has a *role family* and *aliases*.
- **First consumer of `relations.bin`.** This spec gives the build-family's typed-
  relations asset a concrete first definition and builder; a later medical/legal build
  would add its own domain tables to the same `relations.bin` machinery.

---

## 9. First increments (when picked up)

1. Author the element-taxonomy table (WHATWG categories + void + content model) as a
   canonical module (the `verb_complements.py` pattern).
2. Add the markup columns to `meta.db` (`meta_fields` + `DET_COLS`), populated for
   `STRUCTURAL` web surfaces via `facet_overrides`/a markup rule.
3. Build `relations.py` → `relations.bin` (CSR, `SIMILAR_ROLE`/`ALIAS_OF`/`CAN_CONTAIN`)
   as a `build_suite` asset (`relations`), gated + coverage-declared.
4. A tiny lookup API (`similar_roles(surface)`) + a structure-outline pass for summaries.

---

## Pointers

- Build-family / `relations.bin`: [`spec-build-family.md`](spec-build-family.md).
- Utility classes (CONTENT/FUNCTION/STRUCTURAL) + why markup is excluded from epa/faiss:
  `config.py`, [`GUIDE-create-dictionary.md`](GUIDE-create-dictionary.md) §3b.
- Deterministic authority-table precedent: `verb_complements.py`, `spec-meta-db.md`.
