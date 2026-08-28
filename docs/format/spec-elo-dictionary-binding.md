# Spec — Binding the dictionary into the `.elo`/`.eloB` header

> **Status: PROPOSAL, 2026-08-06.** The linchpin for *switchable dictionaries*: a `.elo`
> file must name the dictionary that produced its ids, so decode can verify (or resolve)
> the right one instead of trusting whatever the reader happens to have loaded.
>
> Identity source of truth: [`spec-build-family.md`](../compression/spec-build-family.md)
> (the dictionary **fingerprint** is the family identity) + `artifact_identity.py`
> (`_dictionary_fingerprint`). This spec only concerns the **container header**; it carries
> an opaque identity value, never dictionary internals (keeps `elo_file_format/` publishable).

---

## 1. Why this is needed (and why now)

Ids are assigned by frequency rank, so one added corpus document renumbers the vocabulary.
A `.elo` decoded against the wrong build attaches the wrong surface to every id — **and
nothing crashes**. `spec-build-family.md §1` already calls fingerprint identity "the only
thing that turns that silent corruption into a hard stop," and §4 describes the `.elo`↔
dictionary pairing as "the same lock that ties an `.elo`/model to its dictionary."

That lock is specified at the build layer and **absent from the file**. The MCP and the
browser now need to hold several dictionaries and switch between them; without the file
naming its own dictionary, "switch" is one wrong pick away from silent garbage. This spec
adds the field that closes the gap.

---

## 2. Ground truth — the header as IMPLEMENTED today

Read from `compressor.py` (not from the older `ELO_FILE_FORMAT.md`, which diverged — see §6).

**Text `.elo`** (`encode_text`, `_parse_header`), delimiter `ELO_DELIMITER = '|'` (0x7C):
```
ELO | FORMAT_VERSION | ext | stream
 ^magic  ^=1 (config) ^src  ^token stream
```

**Binary `.eloB`** (`encode_bytes_binary` / `decode_bytes_binary`):
```
b'ELO'              3 bytes   ELO_MAGIC_BIN
ELO_BIN_VERSION     1 byte    = 2
len(ext_bytes)      1 byte
ext_bytes           N bytes
stream              rest
```

Neither carries any dictionary identity. Decode only checks the reader's own
`FORMAT_VERSION`/`ELO_BIN_VERSION` matches the file's.

---

## 3. The change — add the dictionary `build_id` + fingerprint

Two fields, two jobs:
- **`build_id`** — the human/registry handle (e.g. `general_v0.4_char4`), from
  `manifest.spec_meta.name` (or the build-dir name). Answers *"which dictionary?"* — the
  lookup key a system uses to **locate** the build, and what a person reads in the header.
- **`dict_fp`** — the dictionary artifact fingerprint from `manifest.json`
  (`artifacts.dictionary.fingerprint`; `artifact_identity._dictionary_fingerprint` = sha256
  over sorted `(surface, id)`). Answers *"is this exactly it?"* — the value that **verifies**
  a loaded dictionary and can't be faked or accidentally reused.

Both, because a name can be reused or moved while a fingerprint cannot: the name gets you to
a candidate, the fingerprint proves it. A few extra header bytes buy that guarantee.

### 3.1 Text `.elo` — `FORMAT_VERSION` 1 → 2
```
ELO | 2 | build_id | dict_fp | ext | stream
```
- `build_id`: utf-8, no `|` (encode asserts this, same rule the stream parts already follow).
- `dict_fp`: full lowercase sha256 hex (64 chars) — kept whole; trivial next to a text stream.
- `_parse_header` dispatches on the version field (parts[1]): `1` → 4-part legacy layout;
  `2` → 6-part layout `[magic, ver, build_id, dict_fp, ext, stream]` (`split('|', maxsplit=5)`).

### 3.2 Binary `.eloB` — `ELO_BIN_VERSION` 2 → 3
```
b'ELO'              3 bytes   ELO_MAGIC_BIN
version             1 byte    = 3
build_id_len        1 byte    (0 = no build_id)
build_id_bytes      B bytes   utf-8 build handle
fp_len              1 byte    (32 = full sha256; 0 = unstamped)
fp_bytes            fp_len    the sha256 digest (full 32 bytes by default)
ext_len             1 byte
ext_bytes           N bytes
stream              rest
```
- **`fp_len = 32` (full digest) by default.** The header is a one-time per-file cost, so we
  store the whole fingerprint — it compares directly against the manifest with no prefix
  logic and leaves zero ambiguity. `fp_len` is explicit so the field stays self-describing
  (a future 0/8/32 variant needs no version bump).
- `fp_len = 0` / `build_id_len = 0` mean "unstamped" (e.g. a tool with no manifest); readers
  treat that exactly like a legacy file (§4, legacy branch).

---

## 4. Decode semantics

```
read version
if version is legacy (text 1 / binary 2)         -> UNSTAMPED
    warn "dictionary binding unverifiable (legacy .elo)"; decode with the loaded
    dictionary exactly as today. No behavior change for existing files.

else read build_id + dict_fp (either empty -> UNSTAMPED, as above)
    case A — a dictionary is already loaded:
        VERIFY: compare file dict_fp against the loaded dictionary's fingerprint (full,
        exact). match -> decode. mismatch -> raise DictionaryMismatch(build_id,
        file_fp, loaded_fp) — a hard stop, naming both. (build_id is diagnostic here;
        the fingerprint is the authority.)
    case B — no dictionary loaded AND a registry/map is available (the switchable path):
        LOCATE by build_id in the registry -> load that build -> VERIFY its fingerprint
        == file dict_fp before decoding. build_id not found -> fall back to a
        fingerprint scan of known builds. no fingerprint match anywhere ->
        raise DictionaryUnavailable(build_id, file_fp).
```

Case B is the payoff: the file names its dictionary (`build_id` locates it) and proves it
(`dict_fp` verifies it), so the loader picks the right one without the caller pre-selecting —
exactly what the MCP/browser "switch" needs. The two-step (locate then verify) is why we
carry both: the name is fast lookup, the fingerprint is the guarantee, so a renamed or
rebuilt-under-the-same-name dictionary can't slip through. The fail-loud in case A is the
generalization of Reasoning's `require_dictionary()` guard.

---

## 5. Encode — where the fingerprint comes from

The codec already opens a dictionary from a build dir (`dictionary.lmdb`); `manifest.json`
is its sibling. At encode, read both from that manifest:
1. **`build_id`** ← `manifest.spec_meta.name` (fallback: the build-dir name).
2. **`dict_fp`** ← `manifest.artifacts.dictionary.fingerprint`, **or** compute via
   `artifact_identity._dictionary_fingerprint(lmdb_path)` and cache it.

Stamp both into the header per §3. The codec must know its build dir (it already loads the
LMDB from one); no new dependency on dictionary internals — `build_id` is a label and the
fingerprint is an opaque identity value.

> **Staged vs frozen:** a `staged` dictionary's ids may still move, so its fingerprint can
> change between builds. That is fine and correct — a `.elo` stamped by a staged build binds
> to *that* staged fingerprint and will (rightly) refuse a later, renumbered staged build.
> Long-lived `.elo` artifacts should be produced from `frozen`/`locked` dictionaries.

---

## 6. Divergence to reconcile (recorded, not silently overwritten)

`docs/format/ELO_FILE_FORMAT.md` (v1.0, 2026-06-19) already specifies dictionary binding —
a **fixed 64-byte header**, magic `"ELO1"`, a `dict_version uint32` at offset 4, and a
`DICT_CHECKSUM uint64 (xxHash64)` metadata field. **None of that is implemented.** The live
codec uses the simpler delimiter/byte layout in §2, with no dictionary binding at all. Two
things follow:

1. **This spec binds to the implemented format, not the v1.0 paper design.** The identity is
   the artifact **fingerprint** (sha256, `spec-build-family`), which supersedes both the
   monotonic `dict_version` and the `xxHash64` checksum from v1.0 — those predate the
   uniform identity convention.
2. **`ELO_FILE_FORMAT.md` needs a broader reconciliation pass** (it also still documents the
   delimiter as `0x1F` while the code uses `|` — already flagged in
   `elo_file_format/README.md`). That pass is out of scope here; this spec is the header
   change, and should fold into `ELO_FILE_FORMAT.md` when that reconciliation happens.

---

## 7. Compatibility & rollout

- **Readers stay back-compatible:** legacy `.elo` (v1 text / v2 binary) still decode, with a
  one-line "unverifiable binding" warning. No existing file breaks.
- **New writers stamp by default** once the codec is bumped; a `--legacy` escape can emit the
  old versions during transition if needed.
- **Version bump is the gate:** text `FORMAT_VERSION` 1→2, binary `ELO_BIN_VERSION` 2→3. A
  reader refuses a *newer* version it doesn't understand (existing behavior), so a v2 reader
  won't silently mis-read a v3 file.

---

## 8. Test vectors (the real contract — required before this is "done")

1. **Round-trip, stamped:** encode→decode a fixture under a known build; assert byte-exact
   source recovery and that the header carries that build's `build_id` **and** fingerprint.
2. **Mismatch detector:** stamp with build A's fingerprint, decode against build B → must
   raise `DictionaryMismatch`, not return garbage — even if `build_id` happens to match
   (fingerprint is the authority).
3. **Resolve path (case B):** with a registry present and no dictionary preloaded, a stamped
   file locates its build by `build_id`, verifies the fingerprint, and decodes.
4. **Legacy:** a v1/v2 fixture still decodes, emits the unverifiable-binding warning, and is
   byte-exact.

These belong with the format when it graduates to `elo_file_format/` (per that README, test
vectors are "the format's real contract").

---

## 9. Scope boundary

This spec changes **only the container header** and decode/encode wiring. It does **not**:
- change the token stream encoding, tiers, or `caps`/whitespace codecs;
- move or expose any dictionary internals (the fingerprint is opaque);
- design the `Dictionary` loader object or the registry/map — those consume this field but
  are specified separately. This is the anchor they stand on.
