# Spec — The Dictionary Build Family (public contract)

> **The one document to hand another system.** A dictionary and its supporting assets
> are a single build family: an asset is valid only for the exact dictionary it was
> compiled against. This spec is the *contract* — what a build contains, how the pieces
> are bound by identity, what each asset provides, and how a consumer verifies a family
> before trusting it.
>
> This describes the fields that **actually ship today** in `manifest.json`. The
> build-family design notes use friendlier names (`build_id`, `dictionary_hash`,
> `asset_type`); those are recorded here as **aliases** with a planned rename (§6), so a
> reader of either vocabulary can find the field.
>
> Detail lives in the per-piece specs; this is the map. Status: contract. 2026-07-10.

---

## 1. The principle

A dictionary (`dictionary.lmdb`: forward/reverse) is one artifact. Everything else is a
**derived asset** — a deterministic or reproducible function of that dictionary plus a
fixed external input. Together they are a **build family**, and they are only meaningful
in each other's company.

> **A supporting asset must never load because its filename matches. It loads only when
> its recorded dictionary fingerprint matches the dictionary it is paired with.**

Why: IDs are assigned by frequency rank, so adding one corpus document can renumber the
entire vocabulary. An affect or facet file built against build A, loaded against build B,
attaches the wrong values to every word — and nothing crashes. Fingerprint identity is
the only thing that turns that silent corruption into a hard stop.

---

## 2. The shareable object — `manifest.json` -> `artifacts`

Every build emits `manifest.json` (via `build_from_spec` -> `artifact_identity`). Inside
it is an **`artifacts` registry**: one identity record per asset. **This registry is the
contract.** A consumer reads it, checks each asset's fingerprint against the dictionary's,
and knows what it may trust.

```jsonc
"artifacts": {
  "dictionary": { "kind": "dictionary", "present": true,  "version": 3,
                  "status": "staged", "fingerprint": "d535f184c5...",
                  "key_scheme": "base64_id", "bound_refs": {} },
  "facets":     { "kind": "facets",     "present": true,  "version": 1,
                  "status": "staged", "fingerprint": "8359c20fa8...",
                  "key_scheme": "id_bytes",  "bound_refs": {} },
  "epa":        { "kind": "epa",        "present": false, "version": 1,
                  "status": "staged", "fingerprint": null, "key_scheme": "id_bytes" },
  "meta":       { "kind": "meta", "...": "..." },
  "templates":  { "kind": "templates", "...": "..." }
}
```

### The identity record (every asset carries the same shape)

| field | meaning | notes' alias |
|---|---|---|
| `kind` | which asset: `dictionary` / `facets` / `epa` / `meta` / `templates` / ... | `asset_type` |
| `present` | is this asset actually built in this family? | — |
| `version` | asset format version (independent per asset) | `asset_version` |
| `status` | `staged` \| `frozen` — is the ID space still allowed to move? | — |
| `fingerprint` | content hash of the asset (`null` if `present:false`) | `dictionary_hash` (for the dictionary entry) |
| `key_scheme` | how the asset is keyed: `base64_id`, `id_bytes`, `n_index` | — |
| `bound_refs` | other artifacts this one is pinned to (e.g. epa -> substrate) | — |

The **dictionary's** `fingerprint` is the family identity. Every other asset is valid iff
it descends from that fingerprint. `bound_refs` records secondary pins — e.g. an EPA asset
also records the Warriner substrate version it was projected from.

---

## 3. What each asset provides (the "why load it" table)

One vocabulary, many answers. A consumer picks the channels it needs.

| asset | question it answers | key | provides |
|---|---|---|---|
| `dictionary` | *what are the words?* | surface <-> id | the ID space; longest-match phrase atoms |
| `meta` | *what kind of word?* | `id_bytes` | POS, complement, layer-2 columns |
| `facets` | *what may a machine do with it?* | `id_bytes` | bucket · logic-cue set · utility (CONTENT/FUNCTION/...) |
| `epa` | *how does it feel?* | `id_bytes` / `n` | 3-d affect (Evaluation·Potency·Activity), `[-4,4]` |
| `faiss` / `neighbours` | *what does it mean?* | id / `n` | denotational nearest neighbours (planned; §5) |
| `relations` | *what does it connect to?* | id | typed concept links (planned) |
| `constraints` | *what does it expect around it?* | id | roles / compatibility (planned) |
| `templates` | *how is it phrased?* | — | segment/phrase templates |

Browser runtime variants (`epa.bin`, `facets.bin`, `neighbours.bin`) are the same data
re-keyed by the **vocab index `n`** from `<build>.browser.json` — flat arrays,
memory-mapped, `channel[n]`. See `spec-asset-pipeline.md` §3 for the `.bin` wire format.

---

## 4. How a consumer uses a build (read + verify)

```python
import json
manifest = json.load(open(f"{build}/manifest.json"))
arts = manifest["artifacts"]

dict_fp = arts["dictionary"]["fingerprint"]          # the family identity

def usable(kind):
    a = arts.get(kind)
    if not a or not a["present"]:
        return False                                  # asset not in this family
    # an asset must descend from THIS dictionary. For sub-DB assets the check is
    # structural (same lmdb); for standalone files compare recorded vs live.
    return a["fingerprint"] is not None

if usable("facets"):
    ...  # route by facets
if usable("epa"):
    ...  # read affect; but gate the dense core (epa is affect, not semantics)
```

**Rules for any consumer:**

1. **Bind to surfaces, or re-derive.** Until `status == "frozen"`, never persist a raw ID
   or `n` as a long-lived reference across builds. IDs move.
2. **Check `present` before use.** A sparse family legitimately omits assets (§5).
   Absent != broken.
3. **Verify the family, don't assume it.** Run `tools/verify_substrate_chain.py <build>`
   (read-side) before trusting a family you didn't build. It walks the registry and
   reports drift in rebuild order.
4. **EPA is affect, not denotation.** Never use `epa` for synonymy or meaning search;
   that's `faiss`/`neighbours`. `car`~`automobile` is no closer in EPA than
   `murder`~`cancer`. Gate the neutral core.

---

## 5. Assets are allowed to be sparse

A family does **not** need every asset to cover every ID. Each asset declares its own
coverage, and structural tokens, punctuation, byte fallbacks and many code symbols
rightly have no affect record or search vector. `present: false` in the registry means
"this family did not build that channel" — a valid, common state, not an error. This is
what keeps a specialized build (medical, programming, tiny-device) compact: it enriches
only the IDs its function needs.

---

## 6. Known gap — vocabulary drift (validate before trusting)

The build-family design notes and the shipped `manifest.json` name the same ideas
differently. **The shipped names in §2 are authoritative.** Planned rename (tracked, not
yet done — a rename cascades into every consumer, so it is deliberate):

| notes' name | ships today as |
|---|---|
| `build_id` | `corpus_fingerprint` + the dictionary artifact `fingerprint` |
| `dictionary_hash` | `artifacts.dictionary.fingerprint` |
| `asset_type` | `artifacts.<name>.kind` |
| `record_count` / `covered_ids` | not emitted yet — add to each identity record |
| `build_family` | implicit (the registry itself) |

> **Staleness caveat:** a `manifest.json` is only as fresh as its last emit. If the facets
> or epa sub-DB was rebuilt (e.g. the 2026-07 FUNCTION_WORDS facet fix) without
> re-emitting the manifest, the registry fingerprint will trail the live asset. Always
> cross-check against `facets_stats.json` / `epa_stats.json`, or re-run the registry
> writer. This is exactly the drift the identity check exists to catch — including on the
> manifest itself.

---

## 7. Pointers

- Identity record + registry mechanics: [`spec-artifact-identity.md`](spec-artifact-identity.md)
- Build-side DAG, rebuild order, `.bin` wire format: [`spec-asset-pipeline.md`](spec-asset-pipeline.md)
- The facets asset: [`spec-facets-db.md`](spec-facets-db.md) · meta: [`spec-meta-db.md`](spec-meta-db.md)
- Read-side verifier + cascade map: `../../../tools/verify_substrate_chain.py`, `../../../SUBSTRATE_CASCADE.md`
- EPA is affect not semantics: [`probe-epa-embedding-results.md`](probe-epa-embedding-results.md)
- Public narrative: `../../../eloai-blog-posts/7-10-26-the-build-family.md`
