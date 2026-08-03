# Code Ontology Addressing — Spec

> **Where L-SDF code-ontology ids live, and what the general dictionary owes them.**
> Spec 2 replaces a design that duplicated an already-built subsystem.
>
> | | |
> |---|---|
> | **Spec version** | **2** (spec-1 proposed a 256-slot reserved Tier-1 band; see §5) |
> | Status | mostly **recorded fact**; one open question (§4) |
> | Written | 2026-08-02 (moved from repo root same day — subsystem-folder convention) |
> | Normative for tiers | [`spec-tier-system.md`](spec-tier-system.md) |
> | The ontology itself | `08-MCP-ToolInterface/eloai_lsdf/` (parent repo, not this submodule) — **LOCKED at 0.1.0** |

---

## 1. Classifier addressing is already solved — by a separate, built system

`eloai_lsdf` owns the code ontology. It is not a plan:

```
08-MCP-ToolInterface/eloai_lsdf/
  ├── dictionary.py                       Dictionary / Classifier / OntologyError
  ├── dictionaries/base.dictionary.json   format=eloai-base-dictionary
  │                                       version=0.1.0  status=locked
  └── tests/test_base_dictionary.py
```

**62 classifiers** (entity 16, function 10, relationship 10, type 9, layer 7,
access 5, structural 5), **7 sigils**, 5 L-SDF type aliases, single inheritance
via `extends`, and `sigil_for_id()` resolving up to the nearest sigil-bearing
ancestor (`elo:class` → `@` through `elo:entity`).

**The `elo:` prefix is a validated invariant**, not a convention
(`dictionary.py:123`):

```python
if not cid.startswith("elo:"):
    raise OntologyError(f"classifier id must start with 'elo:': {cid!r}")
```

It ships as its **own LMDB** (keys `C<id>` / `S<sigil>` / `A<alias>` / `meta:*`)
with its **own fingerprint and version**. Classifier ids are keys in that store.
**They never pass through the compression tokenizer**, so the fact that
`tokenize('elo:function')` splits three ways is irrelevant to them — see §5 for
how that fact was briefly mistaken for a defect.

**Consequently the general dictionary reserves nothing for classifiers.**
Stability across dictionary builds is free, because ontology ids don't come from
the dictionary at all.

---

## 2. What the general dictionary DOES owe the ontology: seven sigils

Sigils are the one ontology element that appears as **literal text** in L-SDF
source, so they are compressed like any surface. All seven verified single
tokens. Their v01c ids:

| sigil | classifier | v01c id | tier | cost |
|:--:|---|---|:--:|---|
| `@` | `elo:entity` | `6` | 0 | 1 char — optimal |
| `!` | `elo:function` | `n` | 0 | 1 char — optimal |
| `?` | `elo:schema` | `o` | 0 | 1 char — optimal |
| `$` | `elo:annotation` | `4` | 0 | 1 char — optimal |
| `#` | `elo:route` | `5` | 0 | 1 char — optimal |
| `^` | `elo:root` | `yhy` | 2 | 3 chars — **mispriced** |
| `~` | `elo:dependency` | `Aq7z` | 3 | **4 chars — mispriced** |

Five of seven already sit at Tier 0 because they're common punctuation. The two
stragglers are rare in *prose*, which is what ranked them — but `~` marks every
dependency in L-SDF source.

**The change (2 slots, not 256):** add `^` and `~` to `force_include` in the
next build spec. The rank floor lands them in Tier 1 (2-char ids) and in every
profile cut. Both are verified single tokens.

```yaml
  force_include:
    # ... existing structural floor ...
    - "^"        # L-SDF sigil: elo:root
    - "~"        # L-SDF sigil: elo:dependency
```

---

## 3. Success criteria

1. `^` and `~` hold Tier ≤ 1 ids in the next build and appear in every profile
   cut including `tiny`.
2. `eloai_lsdf` tests stay green, untouched — nothing in the dictionary build
   references classifier ids.
3. Every sigil in `base.dictionary.json` `sigils` round-trips
   `tokenize(s) == [s]`; assert this in the build gate if the sigil set ever
   grows.

---

## 4. The one open question — sigil-id stability

Tier 1–3 ids re-mint every build (`|` was `gA` in v01b, `AA` in v01c). After §2,
five sigils have build-stable Tier-0 ids; `^` and `~` will hold Tier-1 ids that
**move with every rebuild**.

Whether that matters depends on one thing: **is a compressed L-SDF file ever
read against a different dictionary build than wrote it?** The current answer is
no — `.eloB` decode uses the writing dictionary, and the ontology LMDB carries
its own versioning. If a future design wants archived `.elo` code artifacts to
outlive dictionary rebuilds, the sigil pair needs either Tier-0 slots (none
free — would mean re-purposing two of the six stage holds, a System-2 decision)
or the file format needs to stamp the dictionary fingerprint it was written
under (it already does: `assets.meta.json` / `meta:fingerprint`). **Default
position: the fingerprint stamp is sufficient; revisit only if cross-build
portability becomes a requirement.**

---

## 5. What spec-1 got wrong (kept for the record)

Spec-1 proposed reserving a 256-slot Tier-1 band and assigning classifier ids by
ontology position, and asserted that *"the `elo:` classifier syntax cannot
work"* because `tokenize('elo:function')` → `['elo', ':', 'function']`.

The measurement was correct; the conclusion confused **two different id
systems**. Classifier ids are LMDB keys in `eloai_lsdf`'s own store — the
tokenizer test applied a constraint from the wrong system. Spec-1's "required
change" to `elo_function` would have violated `OntologyError` validation on all
62 classifiers of a locked artifact.

The process failure is worth more than the correction: the session's search for
existing L-SDF material **timed out and was read as "none found."** A timed-out
search is no result, not a negative result — the built ontology sat in
`08-MCP-ToolInterface/eloai_lsdf/` the entire time. Same failure class as the
2026-07-31 paper-mechanism incident (`WRITING-PAPERS-AND-FIELD-NOTES.md` §4):
confident reasoning from an unestablished absence.

What survives from spec-1: the sizing analysis (a 256-band was affordable
post-expansion at 8.5% of the Tier-1 remainder — true, just unnecessary), the
carve-before-counter mechanism (correct, if a reserved band is ever actually
needed), and the tokenizer gate on force-included surfaces (§3.3, now aimed at
the right target).

---

## 6. Change log

| spec | date | change |
|---|---|---|
| 1 | 2026-08-02 | 256-slot reserved Tier-1 band; classifier surfaces in the general dictionary. **Superseded same day** — duplicated the built `eloai_lsdf` ontology. |
| 2 | 2026-08-02 | Recorded `eloai_lsdf` as owner of classifier addressing. General-dictionary ask reduced to force-including `^` and `~`. Stability question scoped to the sigil pair; fingerprint stamp deemed sufficient. |
