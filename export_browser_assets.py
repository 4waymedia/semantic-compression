"""
export_browser_assets.py -- emit the browser's EPA + FACETS assets from a dictionary
build, as BINARY ARRAYS INDEXED BY THE VOCAB INDEX `n`.

Why indexed by `n`
------------------
`elo-browser-v01.json` gives every surface a contiguous index n = 0..content_size-1,
and `CanonDict.iid` (already resident) maps surface -> n. So EPA and facets need no
keys of their own: they are parallel arrays over that one index. Three consumers,
one vocabulary -- the physical form of the idea.

    before   epa.json 7.36MB + facets.json 5.99MB of embedded text,
             parsed through serde_json::Value into two more HashMap<String,_>
             (~60MB heap, and a large transient Value tree at startup)
    after    epa.bin  ~3.1MB + facets.bin ~1.0MB, include_bytes!, ZERO parse,
             ZERO heap -- a bounds-checked read out of the binary image

Encodings (read from the source of truth, not assumed):
    forward   surface(utf-8) -> id_bytes
    epa       id_bytes -> '<fff'  3 x float32 LE      (epa_projector.EPA_STRUCT)
    facets    id_bytes -> '<BHB'  bucket, cue_mask, flags   (config.unpack_facet)
    utility   (flags & UTILITY_MASK) >> UTILITY_SHIFT       (config.utility_of)

Wire format (both .bin files):
    magic        8 bytes   b"ELOEPA\\x01\\x00" / b"ELOFCT\\x01\\x00"
    count        u32 LE    == content_size
    fp_len       u32 LE    == 64
    fingerprint  64 bytes  ascii hex, the build's dictionary_fingerprint
    payload      count * stride
                 epa    stride 12  -> 3 x f32 LE; absent = NaN,NaN,NaN
                 facets stride  4  -> bucket u8 | cue_mask u16 LE | flags u8
                                      absent = bucket 0xFF

Outputs (into elo-browser/src-tauri/dictionary/ unless --out given):
    epa.bin              include_bytes! by epa.rs
    facets.bin           include_bytes! by facets.rs
    facets.names.json    the enum contract (bucket/cue/utility/flag names). Tiny.
    assets.meta.json     counts + fingerprint + a per-file sha256 for EVERY file this
                         tool writes (the binding record; every pin authored here so
                         bindings.toml can READ identity, not restate it)
    ../../poc/conformance/<build>.{epa,facets}.json            oracles (test-only,
                                                               named after --build)

Run (PowerShell, from the repo root):
    python ELO-Browser\\tools\\export_browser_assets.py --dry-run
    python ELO-Browser\\tools\\export_browser_assets.py

NOTE: run `facet_builder.py` against this build FIRST if its facets are stale.
Facets are re-derived per dictionary build (idempotent, surface-driven, additive --
never touches forward/reverse).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import random
import shutil
import struct
import sys
from pathlib import Path


def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "semantic_compression").is_dir() and (p / "Memory").is_dir():
            return p
    raise SystemExit("cannot locate R-D-concepts root (needs semantic_compression/ + Memory/)")


ROOT = _find_root(Path(__file__).resolve())
sys.path.insert(0, str(ROOT / "semantic_compression"))

#: Where this script actually lives, for the manifests it stamps. Derived so that moving
#: the file cannot leave a manifest claiming the wrong owner.
_GENERATED_BY = str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/")

# VFACET ENUMS ARE IMPORTED, NEVER RETYPED.
# `vfacet_builder.py` is the one Python implementation of the record (spec-vfacets-db.md
# is normative for the layout). Copying its tables here would create the second
# implementation this repo keeps being bitten by -- facets shipped a hand-typed Rust
# port that drifted to 13 cues where the substrate had 15, and nobody knew. The names
# are exported as DATA below so the Rust reader never types them either.
from vfacet_builder import (                                   # noqa: E402
    AGENCY, DIRECTION, TEMPORAL, DOMAIN, POLARITY,
    AGENCY_SHIFT, AGENCY_MASK, DIRECTION_SHIFT, DIRECTION_MASK,
    TEMPORAL_SHIFT, TEMPORAL_MASK, DOMAIN_SHIFT, DOMAIN_MASK,
    POLARITY_SHIFT, POLARITY_MASK,
    POLARITY_KNOWN_SHIFT, POLARITY_KNOWN_MASK,
)

def _vft_decode(b0: int, b1: int) -> dict:
    """The one decode, mirroring vfacet_builder.decode. Used for the oracle only."""
    inv = lambda t: {v: k for k, v in t.items()}
    return {
        "agency":    inv(AGENCY).get((b0 & AGENCY_MASK) >> AGENCY_SHIFT, "UNKNOWN"),
        "direction": inv(DIRECTION).get((b0 & DIRECTION_MASK) >> DIRECTION_SHIFT, "UNKNOWN"),
        "temporal":  inv(TEMPORAL).get((b0 & TEMPORAL_MASK) >> TEMPORAL_SHIFT, "UNKNOWN"),
        "domain":    inv(DOMAIN).get((b1 & DOMAIN_MASK) >> DOMAIN_SHIFT, "GENERAL"),
        "polarity":  inv(POLARITY).get((b1 & POLARITY_MASK) >> POLARITY_SHIFT, "NEUTRAL"),
    }
sys.path.insert(0, str(ROOT / "Memory"))

import lmdb  # noqa: E402

from config import (  # noqa: E402
    BUCKET_NAME, FACETS_DB_NAME, FLAG_NAME, FORWARD_DB_NAME, LOGIC_CUE_NAME,
    UTILITY_MASK, UTILITY_NAME, UTILITY_SHIFT, unpack_facet, utility_of,
)

try:
    from mneme.substrate.epa_projector import unpack_epa   # reuse, don't reimplement
except Exception:                                          # pragma: no cover
    _EPA = struct.Struct("<fff")
    def unpack_epa(b):  # noqa: E306
        return _EPA.unpack(b)

# ROOTS, not destinations. The per-build subfolder is appended by the tool from
# `--build`, so a versioned layout is a property of the exporter rather than of the
# operator remembering to type the full path.
#
# Before 2026-07-30 `--out` WAS the destination: running this with defaults dropped
# epa.bin/facets.bin loose into dictionary/ with no build in the path, and the oracle
# filenames were the literal string "elo-browser-v01" regardless of --build. Exporting
# v01b therefore overwrote the v01 oracles in place (235 -> 249 cases, fingerprint
# b1790799 -> cd8f56f4) while the Rust still read the v01a .bin files. The oracle
# silently began describing a different artifact than the one it tested.
BUILDS_ROOT = ROOT / "semantic_compression" / "db" / "builds"

# THE CASCADE STAGES ITS OWN OUTPUT (2026-09-11, integration's ruling, order-of-work 1).
#
# This defaulted to `ELO-Browser/elo-browser/src-tauri/dictionary/`, so the dictionary's
# build wrote its payload INTO A CONSUMER'S TREE and `publish_dictionary` then read it
# back out of there -- `bundle src: ELO-Browser/.../dictionary/elo-browser-v04` on every
# publish, including v04r2's. Moving the exporters here on 09-10 fixed where the CODE
# lives and left the WRITE PATH pointing at the old owner, which is why v04r2's own
# `assets.meta.json` still stamps `generated_by: ELO-Browser/tools/...`.
#
# Adding the `morph` stage before fixing this would have put a new dictionary channel in
# the browser's repository by construction -- the defect this week removed, one layer up.
#
# The bundle now stages inside the build package it belongs to. `publish_dictionary`
# reads from here; the browser INSTALLS the published bundle from `dist/` rather than
# being written into. Override with --out-root; nothing about the layout is implied.
DEFAULT_OUT_ROOT = BUILDS_ROOT                      # <build>/bundle/ -- see main()
DEFAULT_ORACLE_ROOT = ROOT / "ELO-Browser" / "poc" / "conformance"

MAGIC_EPA = b"ELOEPA\x01\x00"
MAGIC_FCT = b"ELOFCT\x01\x00"
MAGIC_VFT = b"ELOVFT\x01\x00"    # vfacets channel (2026-08-10): 2-byte '<BB' records
VFT_REC = struct.Struct("<BB")   # agency/dir/temporal | domain/polarity
VFT_ABSENT = b"\xff\xff"         # not a valid packed record (temporal 0b111 unused + reserved bits set)
HEADER = struct.Struct("<8sII")          # magic, count, fp_len   (16 bytes) + 64 fp = 80
EPA_REC = struct.Struct("<fff")          # 12 bytes
FCT_REC = struct.Struct("<BHB")          # 4 bytes
BUCKET_ABSENT = 0xFF


def _header(magic: bytes, count: int, fp: str) -> bytes:
    fp_b = fp.encode("ascii")
    if len(fp_b) != 64:
        fp_b = fp_b.ljust(64, b"0")[:64]
    return HEADER.pack(magic, count, 64) + fp_b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", type=Path, required=True,
                    help="build package dir, or a bare build name resolved under "
                         f"{BUILDS_ROOT}. Required: there is no 'current' build, and "
                         "defaulting to one is how the wrong dictionary gets exported.")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT,
                    help="assets are written to <out-root>/<build-name>/")
    # --out, explicit, matching export_neighbours.py and export_wordclass.py. The
    # <out-root>/<build-name> convention silently mis-resolves once the bundle stages in
    # a `bundle/` subdirectory, and three exporters in one cascade taking three different
    # destination arguments is its own small trap.
    ap.add_argument("--out", type=Path, default=None, dest="out_explicit",
                    help="exact output directory; overrides --out-root")
    ap.add_argument("--oracle-root", type=Path, default=DEFAULT_ORACLE_ROOT,
                    help="oracles are written to <oracle-root>/<build-name>/")
    ap.add_argument("--oracle-n", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # Accept a bare name ("elo-browser-v01b") as well as a full path.
    if not a.build.exists() and a.build.parent == Path("."):
        a.build = BUILDS_ROOT / a.build.name
    a.build = a.build.resolve()

    # THE BUILD NAME IS THE FOLDER. Both destinations are derived, never passed in, so
    # no invocation can put one build's assets where another build's already live.
    #
    # When staging inside the build package (the default), the bundle gets its own
    # `bundle/` subdirectory rather than sitting loose beside `dictionary.lmdb`,
    # `meta.db` and the stats sidecars -- publish's G4 deny-list exists precisely because
    # build-time artifacts and shipped payload must not share a directory, and
    # `payload_files()` would otherwise sweep the whole build package into the manifest.
    if a.out_explicit is not None:
        a.out = a.out_explicit.resolve()
    elif a.out_root.resolve() == BUILDS_ROOT.resolve():
        a.out = a.out_root / a.build.name / "bundle"
    else:
        a.out = a.out_root / a.build.name
    a.oracle_out = a.oracle_root / a.build.name

    vocab_path = a.build / f"{a.build.name}.browser.json"
    lmdb_path = a.build / "dictionary.lmdb"
    for p in (vocab_path, lmdb_path):
        if not p.exists():
            raise SystemExit(f"missing: {p}")

    vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
    entries = vocab["entries"]
    count = vocab["content_size"]
    if len(entries) != count:
        raise SystemExit(f"entries {len(entries)} != content_size {count}")
    for i, e in enumerate(entries):
        if e["n"] != i:
            raise SystemExit(f"vocab index not contiguous at {i}: n={e['n']}")
    print(f"vocab      {vocab['vocab_version']}  cut={vocab['cut']}  n=0..{count-1}")

    env = lmdb.open(str(lmdb_path), readonly=True, lock=False, max_dbs=16, subdir=True)
    fwd = env.open_db(FORWARD_DB_NAME, create=False)     # handles OUTSIDE the txn
    fac = env.open_db(FACETS_DB_NAME, create=False)
    epa = env.open_db(b"epa", create=False)
    meta = env.open_db(b"meta", create=False)
    # vfacets is an OPTIONAL channel: exported when the build carries it, absent
    # otherwise. Its absence is a fact about the build (see spec-asset-pipeline).
    try:
        vft = env.open_db(b"vfacets", create=False)
    except Exception:
        vft = None

    epa_arr = bytearray(count * EPA_REC.size)
    fct_arr = bytearray(count * FCT_REC.size)
    vft_arr = bytearray(count * VFT_REC.size) if vft is not None else None
    vft_sample: dict = {}
    nan3 = EPA_REC.pack(math.nan, math.nan, math.nan)
    absent = FCT_REC.pack(BUCKET_ABSENT, 0, 0)
    for i in range(count):
        epa_arr[i * 12:(i + 1) * 12] = nan3
        fct_arr[i * 4:(i + 1) * 4] = absent
        if vft_arr is not None:
            vft_arr[i * 2:(i + 1) * 2] = VFT_ABSENT

    n_epa = n_fct = n_vft = id_mismatch = 0
    epa_sample: dict[str, list[float]] = {}
    fct_sample: dict[str, list[int]] = {}
    buckets: dict[str, int] = {}
    utils: dict[str, int] = {}
    cues: dict[str, int] = {}

    with env.begin() as txn:
        fingerprint = (txn.get(b"dictionary_fingerprint", db=meta) or b"").decode() or "unknown"
        for i, e in enumerate(entries):
            surface = e["surface"]
            idb = txn.get(surface.encode("utf-8"), db=fwd)
            if idb is None:
                continue
            if idb.decode("utf-8", "replace") != e["id"]:
                id_mismatch += 1

            v = txn.get(idb, db=epa)
            if v is not None and len(v) == 12:
                epa_arr[i * 12:(i + 1) * 12] = v          # already '<fff' -- copy verbatim
                n_epa += 1
                epa_sample[surface] = [round(x, 4) for x in unpack_epa(v)]

            v = txn.get(idb, db=fac)
            if v is not None and len(v) == 4:
                fct_arr[i * 4:(i + 1) * 4] = v            # already '<BHB'
                n_fct += 1
                b, mask, flags = unpack_facet(v)
                fct_sample[surface] = [b, mask, flags]
                bn = BUCKET_NAME.get(b, "UNKNOWN")
                buckets[bn] = buckets.get(bn, 0) + 1
                un = UTILITY_NAME.get(utility_of(flags), "CONTENT")
                utils[un] = utils.get(un, 0) + 1
                for bit, name in LOGIC_CUE_NAME.items():
                    if mask & bit:
                        cues[name] = cues.get(name, 0) + 1

            if vft is not None:
                v = txn.get(idb, db=vft)
                if v is not None and len(v) == 2:
                    vft_arr[i * 2:(i + 1) * 2] = v        # already '<BB'
                    n_vft += 1
                    vft_sample[surface] = [v[0], v[1]]

    print(f"fingerprint {fingerprint}")
    print(f"epa        {n_epa:,} / {count:,}  ({100*n_epa/count:.1f}% of vocab)")
    print(f"facets     {n_fct:,} / {count:,}  ({100*n_fct/count:.1f}% of vocab)")
    if vft is not None:
        print(f"vfacets    {n_vft:,} / {count:,}  ({100*n_vft/count:.1f}% of vocab)")
    else:
        print("vfacets    (channel absent in this build -- not exported)")
    if id_mismatch:
        print(f"WARNING    {id_mismatch:,} surfaces whose forward id != vocab id")
    print("  buckets ", dict(sorted(buckets.items(), key=lambda kv: -kv[1])))
    print("  utility ", dict(sorted(utils.items(), key=lambda kv: -kv[1])))
    print(f"  cued     {sum(1 for v in fct_sample.values() if v[1])} surfaces carry >=1 cue")

    old = a.out / "epa.json"
    if old.exists():
        _o = json.loads(old.read_text(encoding="utf-8"))
        n_old = len(_o.get("epa", _o)) if isinstance(_o, dict) else 0
        print(f"\nlegacy epa.json {n_old:,} entries, {old.stat().st_size/1e6:.2f} MB -> replaced by epa.bin")

    epa_bytes = _header(MAGIC_EPA, count, fingerprint) + bytes(epa_arr)
    fct_bytes = _header(MAGIC_FCT, count, fingerprint) + bytes(fct_arr)
    print(f"\nepa.bin    {len(epa_bytes)/1e6:6.2f} MB   facets.bin {len(fct_bytes)/1e6:6.2f} MB"
          f"   (was 13.35 MB of JSON)")

    if a.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    a.out.mkdir(parents=True, exist_ok=True)
    a.oracle_out.mkdir(parents=True, exist_ok=True)

    # Files THIS STAGE writes, recorded as they are written. Not a list kept by hand,
    # and not an enumeration of the output directory either -- see the note on
    # `_assets_meta["files"]` for why the directory is the wrong source here.
    _written: dict[str, str] = {}

    def _wb(path: Path, blob: bytes) -> str:
        path.write_bytes(blob)
        h = hashlib.sha256(blob).hexdigest()[:16]
        # ONLY files landing in the bundle dir. The oracles go to `a.oracle_out`, which
        # is a test fixture directory, not part of the shipped bundle -- recording them
        # by bare name would put fixtures in the bundle manifest.
        if path.parent.resolve() == a.out.resolve():
            _written[path.name] = h
        print(f"  wrote {path.name:<28} {len(blob)/1e6:6.2f} MB  sha256:{h}")
        return h

    # THE VOCAB JSON SHIPS WITH ITS OWN ASSETS.
    #
    # epa/facets/neighbours are parallel arrays indexed by the vocab index n that this
    # json defines — they are meaningless without it, and meaningless with a DIFFERENT
    # one. Exporting the three .bin files while leaving the json behind in the build
    # package produced an asset folder that looked complete and could not be loaded
    # (v01b, 2026-07-30: build.rs now refuses to compile on exactly this).
    _vocab_dst = a.out / vocab_path.name
    shutil.copyfile(vocab_path, _vocab_dst)
    print(f"  copied {_vocab_dst.name:<27} {_vocab_dst.stat().st_size/1e6:6.2f} MB  (vocab index n)")
    h_vocab = hashlib.sha256(_vocab_dst.read_bytes()).hexdigest()[:16]   # the DICTIONARY binding pin
    # Copied rather than written through _wb, so record it by hand -- it IS a file this
    # stage puts in the bundle, and the vocab json is the index every channel is
    # parallel to. Leaving it out would drop the one file the others are meaningless
    # without.
    _written[_vocab_dst.name] = h_vocab

    def _wj(path: Path, obj) -> str:
        return _wb(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    h_epa = _wb(a.out / "epa.bin", epa_bytes)
    h_fct = _wb(a.out / "facets.bin", fct_bytes)
    h_vft = None
    if vft_arr is not None:
        vft_bytes = _header(MAGIC_VFT, count, fingerprint) + bytes(vft_arr)
        h_vft = _wb(a.out / "vfacets.bin", vft_bytes)

    h_names = _wj(a.out / "facets.names.json", {
        "version": 1,
        "dictionary_fingerprint": fingerprint,
        "count": count,
        "utility_shift": UTILITY_SHIFT,
        "utility_mask": UTILITY_MASK,
        "bucket_absent": BUCKET_ABSENT,
        "bucket_names": {str(k): v for k, v in BUCKET_NAME.items()},
        "cue_names": {str(k): v for k, v in LOGIC_CUE_NAME.items()},
        "utility_names": {str(k): v for k, v in UTILITY_NAME.items()},
        "flag_names": {str(k): v for k, v in FLAG_NAME.items()},
    })

    _assets_meta = {
        # DERIVED, never typed. This said "ELO-Browser/tools/..." for a day after the
        # file moved to semantic_compression/ -- a manifest asserting, in writing, that
        # it was produced by a lane that no longer owns it. Exactly the class of stale
        # self-description this repo keeps catching (`wordclass` "reserved, ships with
        # v05, 2 bytes" while shipping at 3 bytes in v04). A path a human maintains is a
        # path that goes wrong the first time anything moves.
        "generated_by": _GENERATED_BY,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "build": a.build.name,
        "dictionary_fingerprint": fingerprint,
        "vocab_version": vocab["vocab_version"],
        "vocab_cut": vocab["cut"],
        "vocab_entries": count,
        "format": "binary arrays indexed by vocab index n",
        "epa_entries": n_epa,
        "facets_entries": n_fct,
        "vfacets_entries": (n_vft if vft is not None else None),
        "epa_bin_sha256_16": h_epa,
        "facets_bin_sha256_16": h_fct,
        "vfacets_bin_sha256_16": h_vft,      # None = channel absent in this build
        # Every shipped file's pin, AUTHORED HERE so bindings.toml can READ identity
        # instead of hand-typing it. `browser_json_sha256_16` is the DICTIONARY
        # binding's pin, which previously had no artifact home -- the root defect in
        # the 2026-08-08 bindings handoff.
        "browser_json": vocab_path.name,
        "browser_json_sha256_16": h_vocab,
        "facets_names_sha256_16": h_names,
        # `files` is filled from the OUTPUT DIRECTORY at the end of main(), not from a
        # list written here -- see _seal_assets_meta. This dict is written last so that
        # every file this stage produces already exists when it is enumerated.
        #
        # WHAT THIS MANIFEST IS: a record of the files THIS STAGE wrote. It is NOT a
        # completeness claim for the bundle, and it never could be: `neighbours.bin` is
        # authored by stage 8, which runs after this one, so it does not exist yet at
        # any point in this script. Read `completeness` below before treating this as
        # the manifest of record -- BUNDLE.json (publish_dictionary.py, stage 12) is.
        "completeness": {
            "scope": "files written by this stage only",
            "authoritative_manifest": "BUNDLE.json",
            "why": "later stages write more files; only publish sees them all",
        },
        "bucket_histogram": buckets,
        "utility_histogram": utils,
        "cue_histogram": cues,
    }

    # stale JSON assets are no longer read by Rust -- remove so they cannot rot
    for name in ("epa.json", "facets.json"):
        p = a.out / name
        if p.exists():
            p.unlink()
            print(f"  removed {name:<27} (superseded by .bin)")

    rng = random.Random(20260709)          # deterministic oracle sample
    surfaces = sorted(set(epa_sample) | set(fct_sample))
    sample = rng.sample(surfaces, min(a.oracle_n, len(surfaces)))

    # ORACLES ARE NAMED AFTER THE BUILD THEY CAME FROM.
    #
    # These filenames used to be the literal string "elo-browser-v01", so exporting ANY
    # build silently overwrote the v01 oracles. Measured 2026-07-30: exporting v01b
    # rewrote poc/conformance/elo-browser-v01.{epa,facets}.json with v01b values
    # (235 -> 249 epa cases, fingerprint b1790799 -> cd8f56f4) while epa.rs/facets.rs
    # still include_bytes! the v01a assets — so the oracle no longer described the
    # asset it was testing. An oracle that follows the newest build cannot detect a
    # regression in an older one; it just relabels the target and stays green.
    #
    # The oracle name is now derived from the build directory, so each build gets its
    # own file and no build can overwrite another's evidence.
    build_name = a.build.name

    # THE REVISION STAMP (2026-09-20, asked for by ELO-Browser -- twice, and correctly).
    #
    # `fingerprint` is the DICTIONARY fingerprint, which is IDENTICAL across revisions by
    # design -- that is what keeps stored .elo files readable. So an oracle stamped with
    # build + fingerprint alone cannot tell r1 from r3. r3 rebuilt vfacets.bin
    # (09466e8b -> b093130f) under an unchanged fingerprint, which left the browser unable
    # to distinguish a REAL vfacets regression from an oracle that simply predates the
    # rebuild -- i.e. unable to trust the suite either way.
    #
    # This is the same defect as the codec oracle's missing codec_policy_version, in the
    # three channel oracles instead of the vector one. An oracle must pin every identity
    # that can move under it.
    _rev, _bid = 1, build_name
    _mf = a.build / "manifest.json"
    if _mf.is_file():
        try:
            _m = json.loads(_mf.read_text(encoding="utf-8"))
            _rev = int(_m.get("package_revision", 1))
            _bid = _m.get("bundle_id") or (build_name if _rev <= 1
                                           else f"{build_name}r{_rev}")
        except Exception as e:                               # noqa: BLE001
            print(f"  WARNING: cannot read package_revision from {_mf} "
                  f"({type(e).__name__}) -- oracles stamped revision 1")
    _identity = {"build": build_name, "fingerprint": fingerprint,
                 "bundle_id": _bid, "package_revision": _rev}

    _wj(a.oracle_out / "epa.json", {
        **_identity,
        "cases": [{"surface": s, "epa": epa_sample[s]} for s in sample if s in epa_sample],
    })
    _wj(a.oracle_out / "facets.json", {
        **_identity,
        "cases": [{
            "surface": s,
            "raw": fct_sample[s],
            "bucket": BUCKET_NAME.get(fct_sample[s][0], "UNKNOWN"),
            "utility": UTILITY_NAME.get(utility_of(fct_sample[s][2]), "CONTENT"),
            "cues": sorted(n for bit, n in LOGIC_CUE_NAME.items() if fct_sample[s][1] & bit),
        } for s in sample if s in fct_sample],
    })

    # VFACETS: names contract + oracle. Both were MISSING until 2026-08-21 -- the
    # channel shipped from the v01c export onward, was compiled into every binary, and
    # was verified by nothing. Two of four channels had oracles; this is the third.
    # (neighbours is still the fourth -- CSR/variable-length, a different oracle shape,
    # filed separately rather than bodged in here.)
    if vft_arr is not None:
        _wj(a.out / "vfacets.names.json", {
            # v2: polarity_known declared + reserved_mask_b1 published.
            "version": 2,
            "dictionary_fingerprint": fingerprint,
            "count": count,
            "spec": "semantic_compression/docs/compression/spec-vfacets-db.md",
            # polarity_known DECLARED 2026-09-04, on ELO-Browser's finding.
            #
            # The field was added to the record on 2026-08-28 and never added HERE, so
            # the shipped contract described five channels while the asset carried six.
            # The browser's reserved-bits guard caught it exactly as written -- 236,645
            # records setting a bit the contract said must be zero -- and that lane was
            # right to leave the test RED rather than widen its mask. Widening would
            # have silenced the only instrument that noticed, and the browser would
            # have gone on decoding five channels out of six while reporting success.
            #
            # WHAT IT MEANS: polarity was DERIVED FROM AN EPA VALUE. Set = measured
            # (NEUTRAL included); clear = no affect data existed. Without it, the
            # 131,454 surfaces measured as genuinely neutral are byte-identical to the
            # 201,350 with no EPA at all -- the absent-vs-zero rule, which POLARITY
            # cannot express on its own because NEUTRAL is 0b00.
            #
            # 236,645 records carry it, which is EXACTLY the epa channel's entry count.
            # That equality is the check: the flag is set iff an EPA record exists.
            "shifts": {"agency": AGENCY_SHIFT, "direction": DIRECTION_SHIFT,
                       "temporal": TEMPORAL_SHIFT, "domain": DOMAIN_SHIFT,
                       "polarity": POLARITY_SHIFT,
                       "polarity_known": POLARITY_KNOWN_SHIFT},
            "masks":  {"agency": AGENCY_MASK, "direction": DIRECTION_MASK,
                       "temporal": TEMPORAL_MASK, "domain": DOMAIN_MASK,
                       "polarity": POLARITY_MASK,
                       "polarity_known": POLARITY_KNOWN_MASK},
            # Byte 1 bit 0 is the ONLY genuinely reserved bit left, and it is measured
            # clear on all 437,995 records. A reader's reserved-bits guard should assert
            # 0b01, not 0b11.
            "reserved_mask_b1": 0b00000001,
            "agency_names":    {str(v): k for k, v in AGENCY.items()},
            "direction_names": {str(v): k for k, v in DIRECTION.items()},
            "temporal_names":  {str(v): k for k, v in TEMPORAL.items()},
            "domain_names":    {str(v): k for k, v in DOMAIN.items()},
            "polarity_names":  {str(v): k for k, v in POLARITY.items()},
            "polarity_known_names": {"0": "UNKNOWN", "1": "MEASURED"},
        })
        _wj(a.oracle_out / "vfacets.json", {
            # vfacets is the channel r3 actually rebuilt, so this is the oracle whose
            # missing revision stamp cost the browser a suite it could not trust.
            **_identity,
            "cases": [dict({"surface": s, "raw": vft_sample[s]},
                           **_vft_decode(*vft_sample[s]))
                      for s in sample if s in vft_sample],
        })

    # SEAL LAST, over WHAT THIS STAGE WROTE -- not over the directory.
    #
    # Two wrong versions of this preceded the right one, and the second was mine:
    #
    #   v1  a hand-kept list, written two-thirds of the way through the script. It
    #       omitted `vfacets.names.json` because that file did not exist yet, so the
    #       channel's field contract shipped with no sha while its data shipped with one.
    #
    #   v2  moved to the end and enumerated `a.out`. That fixed the omission and created
    #       a worse failure: stages 15/16 write `wordclass.bin` and `wordclass.names.json`
    #       into this same directory AFTER stage 7 runs, so the manifest recorded shas
    #       for files a later stage then rewrote. G9 caught it on the first v04r2 build
    #       -- "assets.meta.json claims wordclass.bin=8dadf64e…, file disagrees".
    #
    # The directory is the right source for the BUNDLE manifest, which is assembled once
    # at publish when every stage has finished. It is the wrong source for a STAGE
    # manifest, which is written while other stages still have work to do. Claiming a
    # file you did not write is how a manifest goes stale without anyone editing it.
    #
    # `_written` is populated by `_wb`/`_wj` at the moment of writing, so this cannot
    # drift from what actually happened, and it matches `completeness.scope` above --
    # which v2 contradicted while asserting.
    _assets_meta["files"] = dict(sorted(_written.items()))
    _wj(a.out / "assets.meta.json", _assets_meta)
    print(f"  sealed  assets.meta.json          {len(_assets_meta['files'])} files "
          f"written by THIS stage: {', '.join(sorted(_written))}")

    print("\nNext: cargo test (epa.rs + facets.rs read the .bin via include_bytes!).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
