"""
dictionary_info.py -- THE one door for "what does this dictionary have?"

Every consumer used to hand-roll `env.open_db(b'...')` probes to discover which
channels a build carries -- each differently, and one of them (verbalizer core.py,
pre-2026-08-10) minted an empty sub-db instead of answering "absent". Meanwhile the
build manifest's `artifacts` registry, which does know, is read by nobody at runtime.

This module closes gap G-B/G-C (2026-08-10): a consumer opens a dictionary through
`dictionary_info()` and receives, from THE ARTIFACT ITSELF (never the manifest,
never a hand-typed constant):

    identity   -- fingerprint / release / status / format versions, read from the
                  LMDB's own `meta` sub-db (the same values stamp_meta writes)
    assets     -- for every known channel: present? entry count? key scheme?
    sidecars   -- sibling files (meta.db, *_stats.json, profile-cuts.json ...)
                  with the bound fingerprint each records, so a stale sidecar
                  (bound to a different build) is visible instead of trusted
    stats      -- parsed vfacets_stats/epa_stats extras (e.g. llm_enriched)

Design rules (the repository's recurring lesson, applied):
  * READ, never restate. Presence = the sub-db exists in the LMDB. Counts = the
    LMDB's own stat. A sidecar is reported with ITS recorded fingerprint next to
    the dictionary's, and `bound: True/False` computed -- not assumed.
  * Absence is a named state. `present: False` is an answer, not an error.
  * lmdb only. No heavy deps; safe to import anywhere the codec is.

Usage:
    from compression_dictionary.dictionary_info import dictionary_info
    info = dictionary_info("db/builds/elo-browser-v01c/dictionary.lmdb")
    if info["assets"]["vfacets"]["present"]: ...

    python dictionary_info.py db/builds/elo-browser-v01c/dictionary.lmdb   # CLI report
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import lmdb

# The channels a dictionary MAY carry, and how each is keyed. Extend here when a
# new sub-db ships (e.g. b'role'); consumers then see it with no code of their own.
KNOWN_SUBDBS: dict[bytes, str] = {
    b"forward":  "surface->id",
    b"reverse":  "id->surface",
    b"facets":   "base64_id",       # 4-byte record: bucket | cue_mask u16 | flags
    b"epa":      "base64_id",       # 12-byte record: <fff E,P,A
    b"vfacets":  "base64_id",       # 2-byte record: agency/dir/temporal | domain/polarity
    b"meta":     "identity keys",   # the dictionary's own identity store
    # 2026-08-28: renamed from b'role' (collided with the reasoning lane's semantic
    # roles agent/patient) and scoped for BOTH consumers per the grammatical-sentence
    # gap analysis: byte 0 = class/confidence/ambivalent, byte 1 = the lexical features
    # generation needs (countability, inherent number, proper, requires_determiner).
    # Contract + rationale: handoffs/2026-08-28-dictionary-lane-wordclass-accepted.md
    b"wordclass": "base64_id",      # reserved -- planned, ships with v05 (2 bytes)
}

# Sidecar files that may sit next to the LMDB in a build package, and the JSON
# field each uses to record which dictionary it was derived from.
SIDECARS: dict[str, str | None] = {
    "meta.db": None,                                # SQLite; fp lives in meta_stats.json
    "meta_stats.json": "dictionary_fingerprint",
    "meta_layer2_stats.json": "dictionary_fingerprint",
    "epa_stats.json": "dictionary_fingerprint",
    "facets_stats.json": "dictionary_fingerprint",
    "vfacets_stats.json": "dictionary_fingerprint",
    "dict_stats.json": None,
    "profile-cuts.json": None,
    "manifest.json": None,                          # build record; identity lives in artifacts
    "assets_pipeline.json": None,
    # stage 14; fingerprint-bound so a census from another build is visible
    "coverage_census.json": "dictionary_fingerprint",
}

# Int-encoded meta keys (4-byte LE). `wordclass_format_version` and
# `vfacets_format_version` added 2026-08-30 on 04-Verbalizer's ask: both channels
# DECLARED a format version in their stats JSON and stamped it nowhere, so every
# consumer had to restate the constant -- and one restated `2` while the geometry was
# `3`. A version that cannot be read from the artifact is not a version, it is a rumour.
_INT_META = {b"dictionary_version", b"dictionary_format_version",
             b"facets_format_version", b"wordclass_format_version",
             b"vfacets_format_version", b"normalization_version", b"record_width"}


def _decode_meta(k: bytes, v: bytes):
    if k in _INT_META:
        try:
            return struct.unpack("<I", v)[0] if len(v) == 4 else v.decode("utf-8", "replace")
        except Exception:
            return v.hex()
    return v.decode("utf-8", "replace")


def dictionary_info(lmdb_path: str | Path) -> dict:
    """Inventory a dictionary artifact: identity + available assets + sidecars."""
    p = Path(lmdb_path)
    if not p.exists():
        raise FileNotFoundError(f"no dictionary at {p}")

    env = lmdb.open(str(p), readonly=True, lock=False, max_dbs=len(KNOWN_SUBDBS) + 4)
    try:
        # 1. which sub-dbs actually exist (list the unnamed main db's keys --
        #    reading the artifact, not probing blind).
        present_names: set[bytes] = set()
        main = env.open_db()
        with env.begin() as txn:
            for k, _ in txn.cursor(db=main):
                present_names.add(bytes(k))

        # 2. identity, from the artifact's own meta sub-db.
        identity: dict = {}
        if b"meta" in present_names:
            mdb = env.open_db(b"meta", create=False)
            with env.begin() as txn:
                for k, v in txn.cursor(db=mdb):
                    identity[k.decode()] = _decode_meta(bytes(k), v)
        fingerprint = identity.get("dictionary_fingerprint")

        # 3. per-channel presence + entry counts.
        assets: dict[str, dict] = {}
        for name, scheme in KNOWN_SUBDBS.items():
            key = name.decode()
            if name in present_names:
                db = env.open_db(name, create=False)
                with env.begin() as txn:
                    n = txn.stat(db=db)["entries"]
                assets[key] = {"present": True, "entries": n, "key_scheme": scheme}
            else:
                assets[key] = {"present": False, "entries": 0, "key_scheme": scheme}
        unknown = sorted(n.decode("utf-8", "replace")
                         for n in present_names if n not in KNOWN_SUBDBS)
    finally:
        env.close()

    # 4. sidecars next to the artifact, each with its recorded binding checked
    #    against the dictionary's own fingerprint (a stale sidecar says so).
    sidecars: dict[str, dict] = {}
    stats_extra: dict[str, dict] = {}
    for fname, fp_field in SIDECARS.items():
        f = p.parent / fname
        if not f.exists():
            continue
        rec: dict = {"present": True, "bytes": f.stat().st_size}
        if fp_field and f.suffix == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                side_fp = data.get(fp_field)
                rec["fingerprint"] = side_fp
                rec["bound"] = (bool(side_fp) and bool(fingerprint)
                                and side_fp == fingerprint)
                if fname == "vfacets_stats.json":
                    stats_extra["vfacets"] = {
                        "llm_enriched": data.get("llm_enriched"),
                        "deterministic_fields": data.get("deterministic_fields"),
                    }
                if fname == "epa_stats.json":
                    stats_extra["epa"] = {
                        "global_epa_version": data.get("global_epa_version"),
                        "pct_content_covered": data.get("pct_content_covered"),
                    }
            except Exception as e:
                rec["error"] = str(e)[:80]
        sidecars[fname] = rec

    return {
        "path": str(p),
        "identity": identity,
        "fingerprint": fingerprint,
        "assets": assets,
        "unknown_subdbs": unknown,      # channels this module doesn't know -- surfaced, not hidden
        "sidecars": sidecars,
        "stats": stats_extra,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Report a dictionary artifact's identity + assets")
    ap.add_argument("lmdb", type=Path)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()
    info = dictionary_info(a.lmdb)
    if a.json:
        print(json.dumps(info, indent=2))
        return 0
    fp = info["fingerprint"] or "?"
    ident = info["identity"]
    print(f"dictionary {info['path']}")
    print(f"  fingerprint {fp[:16]}   release={ident.get('dictionary_release')}"
          f"   status={ident.get('dictionary_status')}")
    print(f"  {'asset':<10}{'present':<9}{'entries':>10}  key")
    for k, a_ in info["assets"].items():
        print(f"  {k:<10}{str(a_['present']):<9}{a_['entries']:>10,}  {a_['key_scheme']}")
    if info["unknown_subdbs"]:
        print(f"  UNKNOWN sub-dbs: {info['unknown_subdbs']}")
    for f, r in info["sidecars"].items():
        b = "" if "bound" not in r else ("  bound" if r["bound"] else "  ⚠ NOT BOUND to this build")
        print(f"  sidecar {f:<26}{b}")
    for ch, s in info["stats"].items():
        print(f"  stats.{ch}: {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
