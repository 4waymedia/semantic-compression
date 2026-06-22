# Artifact Identity — Specification

> One identity convention for **every** build artifact, and a single `artifacts`
> registry in the package `manifest.json` that references them all. Makes the whole
> stack (dictionary → facets / epa / meta / templates) reproducible and pairable by
> fingerprint — the same rule that already pins dictionary↔model.
>
> Status: implemented (`artifact_identity.py`; emitted by `build_from_spec`).
> Last updated: 2026-06-20.

---

## 1. Why

Identity is scattered today: facets carry a content fingerprint + format version,
the build carries a `corpus_fingerprint`, lifecycle/`bound_model` live in the facet
`meta` sub-db, EPA has **nothing** (O-EPA2), meta will add its own. Five bespoke
schemes means no uniform way to ask "what produced this, what is it pinned to, can
I trust this pairing." One convention fixes that.

---

## 2. The identity record

```
kind         dictionary | facets | epa | templates | meta
present      true = built; false = reserved (declared, not yet produced)
version      schema/format version of THIS artifact
status       staged | frozen | locked
fingerprint  deterministic content hash (null if absent)
key_scheme   base64_id | surface | lang_surface | none
bound_refs   what it is pinned to: { dictionary, model, global_epa, corpus }
stamped_utc  when the identity was written
```

Reserved artifacts (`present:false`) are listed too, so a manifest always shows the
full intended stack and what is still missing.

---

## 3. The five artifacts

| kind | key_scheme | version from | fingerprint = sha256 over |
|---|---|---|---|
| dictionary | base64_id | builder format (v3) | sorted `(surface, id)` from forward DB |
| facets | base64_id | `facets_format_version` | `(id, surface, facet_record)` |
| epa | base64_id | epa format | `(id, E, P, A)` of the **matched** per-dict table |
| meta | surface | `meta_format_version` | deterministic meta subset (excl. S2 nulls) |
| templates | base64_id | template format | `(id, template_vector)` — System 2 |

The dictionary fingerprint (surface,id) is deliberately **distinct** from the facets
fingerprint (which folds in the facet record) — different content, different hash.

---

## 4. Bound-refs & pairing rules

```
dictionary  → bound_refs.corpus = corpus_fingerprint
            → bound_refs.model  = <model id>   (only when status = locked)
facets      → bound_refs.dictionary = dictionary.fingerprint
meta        → bound_refs.dictionary = dictionary.fingerprint
epa (per-dict) → bound_refs.dictionary  = dictionary.fingerprint
               → bound_refs.global_epa = <global EPA substrate version/fp>
templates   → bound_refs.dictionary = dictionary.fingerprint
```

**Pairing rule:** a dependent artifact (facets/epa/meta/templates) is valid only
with the dictionary whose fingerprint it bound to. This is the same lock that ties
an `.elo`/model to its dictionary — now uniform across the stack. The per-dictionary
EPA table additionally pins the **global EPA substrate version** it was matched
against (see `Memory/mneme/docs/EPA.md` — the global word-keyed substrate vs. the
per-dictionary id-keyed match).

---

## 5. The manifest registry

The package `manifest.json` gains:

```json
"artifacts": {
  "dictionary": { …identity… },
  "facets":     { …identity… },
  "epa":        { "present": false, … },
  "meta":       { "present": false, … },
  "templates":  { "present": false, … }
},
"artifacts_problems": []          // validate_registry output; [] == consistent
```

`build_from_spec` writes this as the final build step (after facets/meta), so every
dictionary declares its full artifact stack and pairings by construction.

---

## 6. Lifecycle

`status ∈ {staged, frozen, locked}` per artifact (reuses `stamp_meta.py`). A frozen
dictionary may still carry `staged` meta; `locked` means bound to an LLM retrain and
**requires** `bound_refs.model`. The build-package overwrite guard already refuses
to rebuild a `locked` dictionary; the registry makes that state explicit per layer.

---

## 7. Migration (existing fields → convention)

```
facets_stats.dictionary_fingerprint   -> artifacts.facets.fingerprint
manifest.corpus_fingerprint           -> artifacts.dictionary.bound_refs.corpus
facet meta: dictionary_status         -> artifacts.*.status
facet meta: bound_model / bound_at    -> artifacts.dictionary.bound_refs.model
(new) dictionary (surface,id) hash    -> artifacts.dictionary.fingerprint
EPA: (none today)                     -> artifacts.epa  (O-EPA2 — add at match step)
```

Existing fields stay for back-compat; the registry is the canonical, uniform view.

---

## 8. API

```python
from semantic_compression.artifact_identity import (
    make_identity, fingerprint_pairs, registry_from_package,
    write_registry, validate_registry)

write_registry(pkg_dir)          # compose from on-disk state + merge into manifest.json
validate_registry(reg) -> []     # [] == consistent (bound-ref + locked-model checks)
```

`registry_from_package` reads the package LMDB (computes the true dictionary fp),
`facets_stats.json`, presence of a matched `b'epa'` sub-db and `meta.db`, and the
manifest, then emits the full registry — reserved entries included.

---

## 9. Validation gates

```
G-bind   facets/epa/meta/templates, when present, bind to the dictionary fingerprint.
G-lock   any locked artifact has bound_refs.model.
G-repro  same package state -> identical registry (fingerprints deterministic).
G-pair   a dependent artifact opened against a different dictionary fp is rejected.
```

---

## 10. Pointers

```
Convention   semantic_compression/artifact_identity.py
Emitted by   semantic_compression/build_from_spec.py (final build step)
Facets fp    semantic_compression/facets.py ; spec-facets-db.md
EPA pairing  Memory/mneme/docs/EPA.md (global substrate vs per-dict match)
Meta fp      docs/compression/spec-meta-db.md
Lifecycle    semantic_compression/stamp_meta.py (staged/frozen/locked, bound_model)
```
