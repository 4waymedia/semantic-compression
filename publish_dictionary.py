"""
publish_dictionary.py -- STAGE 12. Turn a built dictionary into a *published bundle*.

Build and publish are separate verbs. `build_assets.py` (stages 1-11) derives the
channels; this stage assembles ONLY the shippable files into one self-describing
bundle, VERIFIES them against each other and against the source build, and -- only if
every gate passes -- writes `BUNDLE.json` and stamps the bundle `published`. It refuses
on any failure. It never ships a partial bundle.

Spec: docs/compression/spec-publish-dictionary.md (spec 1, 2026-08-07).
Companion: DICTIONARY-BUILD-RUNBOOK.md (stages 1-11), spec-asset-pipeline.md.

WHY THIS EXISTS (measured on elo-browser-v01c, 2026-08-07)
    - Five different fingerprints identify one build; only the 4-file browser bundle is
      internally consistent. "Are these files the same build?" had no single answer.
    - The build manifest said `epa.present=false` while epa.bin (208,556 entries)
      shipped. A consumer that retypes a producer's identity can contradict it.
    - `manifest.deliverables` listed 1.5 GB of denotative *inputs* and none of the
      three .bin channels that actually ship.
    This stage makes the bundle -- and its single fingerprint -- the object of record.

THE PUBLISHABLE OBJECT (spec sec 2) -- exactly these, nothing else:
    <build>.browser.json   vocab {surface,id,n}     forward table
    facets.bin             role    -- parallel over n
    epa.bin                affect  -- parallel over n
    neighbours.bin         meaning -- CSR over n
    facets.names.json      code -> name legend
    BUNDLE.json            the manifest of record   (written here)
Explicitly NOT shipped: dictionary.denotative.{index,vecs.f32,surfaces.json} (the
machine that MAKES neighbours.bin), meta.db, dictionary.lmdb, token-ids.csv.gz,
every *_stats.json. G4 asserts they are absent from the bundle.

USAGE (PowerShell, from semantic_compression/):
    python publish_dictionary.py db/builds/elo-browser-v01c --dry-run
    python publish_dictionary.py db/builds/elo-browser-v01c
    python publish_dictionary.py --verify dist/dictionary/elo-browser-v01c
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path

# --- wire formats (identical headers to the three exporters) ----------------------
HEADER = struct.Struct("<8sII")          # magic, count, fp_len   (16 B) + 64 fp = 80 B
EPA_REC = struct.Struct("<fff")          # 12 B  affect triple
FCT_REC = struct.Struct("<BHB")          # 4 B   bucket, cue_mask, flags
NBR_OFF = struct.Struct("<I")            # CSR offset u32
NBR_REC = struct.Struct("<IB")           # neighbour_n u32 + sim u8 = 5 B
VFT_REC = struct.Struct("<BB")           # 2 B  vfacet record; b"\xff\xff" = absent
VFT_ABSENT = b"\xff\xff"
from asset_registry import (ASSETS, BY_NAME, NOT_PAYLOAD,   # noqa: E402
                            bundle_channels, wire_magic)

# DERIVED, not declared. These were three hand-kept lists here; `wordclass` was in none
# of them, which is how a locked 437,995-record channel passed every publish gate by not
# being mentioned. See asset_registry.py.
MAGIC = wire_magic()
BUCKET_ABSENT = 0xFF
HDR_LEN = HEADER.size + 64               # 80

SCHEMA = "elo-dictionary-bundle/1"

# Every asset the registry marks `ships`. CONDITIONAL ON THE BUILD, uniformly: an asset
# is required in the bundle iff the source build's LMDB carries it. That rule used to be
# a special case written for `vfacets` alone; generalising it is what makes a new asset
# publish correctly without editing this file.
#
# A build that HAS a channel must ship it ("complete by construction"); a build that
# never had one publishes a smaller bundle, honestly, and BUNDLE.json says which.
BUNDLE_CHANNELS = bundle_channels()
# Anything matching these must NOT be in a bundle dir (spec sec 2 deny-list).
DENY = ("dictionary.lmdb", "meta.db", "token-ids.csv.gz",
        "dictionary.denotative.index", "dictionary.denotative.vecs.f32",
        "dictionary.denotative.surfaces.json", "dictionary.denotative.json",
        "dictionary.denotative.progress.json")
DENY_SUFFIX = ("_stats.json",)

# Files that are ABOUT the bundle rather than part of its payload. Everything else in
# a bundle directory is payload and MUST appear in BUNDLE.json's `files` map.
#
# This set is the only hand-maintained list left, and it is deliberately the smallest
# one: naming what is NOT payload fails safe, because forgetting an entry here makes a
# file get declared, while forgetting an entry in a payload list makes a file ship
# unbound. That is exactly how `neighbours.bin` -- 22 MB, the largest single asset --
# and `vfacets.names.json` shipped with no sha in elo-browser-v04: three separate
# hand-maintained "shipped files" lists (export_browser_assets.py, export_neighbours.py,
# and this module) and none of them complete. G9 now refuses on any payload file that
# is not declared, so the next channel cannot be forgotten.
#
# MOVED TO asset_registry 2026-09-21. It was defined here alone, and dictionary_standard's
# published read-back needs the same answer -- so it would have had to import a
# publish-time script or restate the tuple. A restated list is how nine asset lists
# happened; this one is now declared once, with the assets it is about. Imported above.
#
# BUNDLE.json IS THE MANIFEST OF RECORD. Settled 2026-09-11 (Paul), on evidence:
#
#     dist/dictionary/elo-browser-v01c/     BUNDLE.json          no assets.meta.json
#     dist/dictionary/elo-browser-v04/      BUNDLE.json          no assets.meta.json
#     dist/dictionary/elo-browser-v04r2/    BUNDLE.json  AND     assets.meta.json
#
# `elo-reasoning`'s gate reads the bundle identity out of `assets.meta.json` -- a file
# present in ONE PUBLISHED BUNDLE OF THREE, and present in that one only because r2
# picked it up when this module became directory-driven. A consumer pinned to it works
# against r2 and fails against every earlier publication, which is worse than failing
# everywhere: it looks like the gate works.
#
# The stage manifests are the build's DIARY -- what stage 7 and stage 8 each wrote. They
# are useful in the build directory and they are not part of the shipped artifact, so
# they stop being copied. A bundle carries payload plus the one manifest that describes
# all of it.
#
# Consumers read BUNDLE.json: `build`, `package_revision`, `bundle_id`,
# `source_build_fingerprint`, `bundle_fingerprint`, `files`, `channels`, `codec`.


# --- small io helpers -------------------------------------------------------------
def _read_self_described(path: Path, role: str) -> tuple:
    """Identity for an asset that carries it INSIDE the file rather than in a header.

    `morph_map.json` is `{"meta": {...}, "decided": {"a|b": bool}}`. Its meta holds the
    build and bundle fingerprint it was baked against -- which is how
    `elo_reasoning.morphology.lemma.verify_pin` already detects a stale map, so publish
    reads the same field rather than inventing a second notion of the map's identity.

    Returns (entry_count, fingerprint). The count is PAIRS DECIDED, not a vocab count --
    the registry marks this asset `n_parallel=False` so G1 does not compare the two."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                                     # noqa: BLE001
        raise SystemExit(f"{path.name}: unreadable as JSON ({exc}). {role} is declared "
                         f"self-describing in asset_registry; if its container changed, "
                         f"the registry entry is what must change with it.")
    meta = doc.get("meta") or {}
    decided = doc.get("decided")
    if decided is None:
        raise SystemExit(f"{path.name}: no `decided` block -- this is not a morph map.")
    # `bundle_fingerprint` is what lemma.py reads as the pin; fall back to the plain
    # `fingerprint` key rather than silently reporting none.
    fp = meta.get("bundle_fingerprint") or meta.get("fingerprint") or ""
    return len(decided), fp


def _contract_version(bundle_dir: Path, asset) -> "int | None":
    """The channel's format version, READ from its own contract file.

    Returns None when the channel ships no contract (`epa`, `neighbours`) -- which is
    itself the answer to "can a consumer gate on this channel's geometry", and the answer
    is no. Recorded as null rather than omitted, so the gap is visible in the manifest
    instead of being absent from it."""
    if not asset.contract_file:
        return None
    p = bundle_dir / asset.contract_file
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("version")
    except Exception:                                            # noqa: BLE001
        return None


def payload_files(bundle_dir: Path) -> dict:
    """Every file in the bundle directory that is PAYLOAD, by name.

    Directory-driven on purpose. A manifest assembled from a list someone maintains
    records what the author remembered; a manifest assembled from the directory records
    what actually ships, and the difference is the 22 MB `neighbours.bin` that had no
    sha anywhere in elo-browser-v04."""
    return {p.name: p for p in sorted(bundle_dir.iterdir())
            if p.is_file() and p.name not in NOT_PAYLOAD}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_lmdb_fingerprint(build_dir: Path) -> str | None:
    """The build LMDB's OWN self-declared dictionary_fingerprint (meta b'...').
    A bundle that does not carry this fingerprint was not exported from this build
    dir -- exactly the elo-browser-v01c divergence (bundle bdec07bf vs LMDB f4c8879e,
    the LMDB rebuilt after the bundle shipped). Dependency-light: lmdb only."""
    try:
        import lmdb  # noqa: PLC0415
    except Exception:
        return None
    p = build_dir / "dictionary.lmdb"
    if not p.exists():
        return None
    env = lmdb.open(str(p), readonly=True, lock=False, max_dbs=16, subdir=True)
    try:
        meta = env.open_db(b"meta", create=False)
        with env.begin() as txn:
            v = txn.get(b"dictionary_fingerprint", db=meta)
        return v.decode() if v else None
    except Exception:
        return None
    finally:
        env.close()


def _has_subdb_publish(lmdb_path: Path, name: bytes) -> bool:
    """Does the build LMDB carry this sub-db? (drives conditional channels)."""
    try:
        import lmdb  # noqa: PLC0415
    except Exception:
        return False
    if not lmdb_path.exists():
        return False
    env = lmdb.open(str(lmdb_path), readonly=True, lock=False, max_dbs=16)
    try:
        env.open_db(name, create=False)
        return True
    except Exception:
        return False
    finally:
        env.close()


def _read_header(path: Path, role: str) -> tuple[int, str]:
    """Return (count, fingerprint) from a channel header; raise on a wrong magic."""
    blob = path.read_bytes()[:HDR_LEN]
    if len(blob) < HDR_LEN:
        raise ValueError(f"{path.name}: shorter than an {HDR_LEN}-byte header")
    magic, count, fp_len = HEADER.unpack_from(blob, 0)
    if magic != MAGIC[role]:
        raise ValueError(f"{path.name}: magic {magic!r} != {MAGIC[role]!r}")
    fp = blob[HEADER.size:HEADER.size + fp_len].decode("ascii", "replace")
    return count, fp


class Gate:
    """One check. Records id, pass/fail, and a human detail line."""
    __slots__ = ("id", "name", "ok", "detail")

    def __init__(self, gid: str, name: str, ok: bool, detail: str):
        self.id, self.name, self.ok, self.detail = gid, name, ok, detail

    def as_dict(self) -> dict:
        return {"gate": self.id, "name": self.name,
                "result": "PASS" if self.ok else "FAIL", "detail": self.detail}


# --- channel readers (whole-file, for coverage + spot read) -----------------------
def _epa_coverage(path: Path, count: int) -> tuple[int, list[int]]:
    """Return (present_count, ns_with_epa) reading the '<fff' payload; NaN == absent."""
    raw = path.read_bytes()
    present = 0
    have: list[int] = []
    for n in range(count):
        off = HDR_LEN + n * EPA_REC.size
        e, p, a = EPA_REC.unpack_from(raw, off)
        if not (math.isnan(e) and math.isnan(p) and math.isnan(a)):
            present += 1
            if len(have) < 4096:
                have.append(n)
    return present, have


def _facets_coverage(path: Path, count: int) -> tuple[int, list[int]]:
    raw = path.read_bytes()
    present = 0
    have: list[int] = []
    for n in range(count):
        bucket = raw[HDR_LEN + n * FCT_REC.size]
        if bucket != BUCKET_ABSENT:
            present += 1
            if len(have) < 4096:
                have.append(n)
    return present, have


def _nbr_offsets(path: Path, count: int) -> list[int]:
    raw = path.read_bytes()
    base = HDR_LEN
    return [NBR_OFF.unpack_from(raw, base + i * NBR_OFF.size)[0] for i in range(count + 1)]


def _nbr_of(path: Path, n: int, count: int) -> list[tuple[int, int]]:
    raw = path.read_bytes()
    offs = _nbr_offsets(path, count)
    rec0 = HDR_LEN + (count + 1) * NBR_OFF.size
    out = []
    for r in range(offs[n], offs[n + 1]):
        nn, sim = NBR_REC.unpack_from(raw, rec0 + r * NBR_REC.size)
        out.append((nn, sim))
    return out


# --- the gates --------------------------------------------------------------------
def run_gates(bundle: dict, build_dir: Path, run_heavy: bool) -> list[Gate]:
    """bundle: {role -> Path} plus 'vocab' and parsed vocab meta. Returns gate list."""
    gates: list[Gate] = []
    vocab = bundle["_vocab"]
    vocab_entries = bundle["_vocab_entries"]
    src_fp = bundle["_source_fp"]           # chosen = facets header fp
    counts = bundle["_counts"]              # role -> header count
    fps = bundle["_fps"]                    # role -> header fp

    chans = bundle.get("_channels", list(BUNDLE_CHANNELS))
    # G1 one vocabulary, one n -- FOR THE CHANNELS THAT HAVE AN n.
    #
    # `morph_map` is keyed on PAIRS of surfaces, not on the vocab index, so it has no
    # per-n count and comparing its size to `vocab_entries` compares a pair count to a
    # vocabulary. The registry says which assets are n-parallel rather than this gate
    # assuming all of them are -- an assumption that held only while every channel
    # happened to be a dense array.
    n_chans = [r for r in chans if BY_NAME[r].n_parallel]
    sparse = [r for r in chans if not BY_NAME[r].n_parallel]
    bad = [f"{r}={counts[r]}" for r in n_chans
           if counts.get(r) is not None and counts[r] != vocab_entries]
    gates.append(Gate("G1", "one vocabulary, one n", not bad,
                      f"{len(n_chans)} n-parallel channels count == "
                      f"vocab_entries={vocab_entries}"
                      + (f"; sparse (no n): {', '.join(sparse)}" if sparse else "")
                      if not bad
                      else f"count mismatch: {', '.join(bad)} (vocab={vocab_entries})"))

    # G2 channel fingerprints agree with EACH OTHER *and* with the source build LMDB.
    #     The second half catches a bundle exported from a since-replaced build state
    #     (elo-browser-v01c: every channel carries bdec07bf, but the build LMDB now
    #     self-declares f4c8879e -- the bundle cannot be reproduced from its build dir).
    build_fp = bundle.get("_build_lmdb_fp")
    # FRAMED channels only. Their header fingerprint IS the dictionary fingerprint, so
    # they must all agree. `morph_map`'s pin is a BUNDLE fingerprint -- a different
    # identity, deliberately, because the map keys on SURFACES and therefore survives a
    # rebuild that moves every id. Comparing the two would fail a correct artifact and
    # teach people to ignore G2.
    framed = [r for r in chans if BY_NAME[r].framed]
    disagree = {r: fps[r] for r in framed if fps[r] != src_fp}
    lmdb_ok = (build_fp is None) or (build_fp == src_fp)
    g2ok = (not disagree) and lmdb_ok
    # Surface the unframed pins rather than dropping them: not gated here, still visible.
    _pins = {r: (fps.get(r) or "(unpinned)")[:16] for r in chans
             if not BY_NAME[r].framed}
    if disagree:
        detail = f"channels disagree: {', '.join(f'{r}={v[:16]}' for r, v in disagree.items())}"
    elif not lmdb_ok:
        detail = (f"channels agree ({src_fp[:16]}) but the build LMDB self-declares "
                  f"{build_fp[:16]} -- bundle not exported from this build dir")
    else:
        detail = f"{len(framed)} framed channels + build LMDB carry {src_fp[:16]}"
        if _pins:
            detail += ("; surface-keyed (own pin): "
                       + ", ".join(f"{r}={v}" for r, v in _pins.items()))
    gates.append(Gate("G2", "channel + build fingerprints agree", g2ok, detail))

    # G3 manifest agrees with reality (present flag vs the file that shipped)
    man = bundle.get("_manifest") or {}
    arts = (man.get("artifacts") or {})
    g3bad = []
    for r in BUNDLE_CHANNELS:
        role_key = "facets" if r == "facets" else ("epa" if r == "epa" else None)
        if role_key and role_key in arts:
            present = bool(arts[role_key].get("present"))
            if not present:          # the file is in the bundle, so present must be True
                g3bad.append(f"manifest.artifacts.{role_key}.present=false but {r}.bin shipped")
    gates.append(Gate("G3", "manifest agrees with reality", not g3bad,
                      "manifest present-flags match shipped channels" if not g3bad
                      else "; ".join(g3bad)))

    # G4 no build-time artifact in the bundle
    strays = [p.name for p in bundle["_dir"].iterdir()
              if p.name in DENY or any(p.name.endswith(s) for s in DENY_SUFFIX)]
    gates.append(Gate("G4", "no build-time artifact in bundle", not strays,
                      "deny-list absent" if not strays else f"present: {', '.join(strays)}"))

    # G9 every payload file is declared, and every declaration is a real file.
    #
    # The gate that would have caught elo-browser-v04. `neighbours.bin` (22 MB) and
    # `vfacets.names.json` shipped with no sha in any manifest, because completeness was
    # enforced by three separate hand-written lists and enforced by none of them. This
    # asks the directory instead: two-way, so a declared-but-missing file fails too.
    #
    # It also cross-checks the per-stage manifests that stages 7 and 8 leave behind.
    # They are provenance, not the record -- but a stage manifest claiming a sha that
    # disagrees with the file is a fact worth refusing on.
    payload = payload_files(bundle["_dir"])
    declared = set(bundle.get("_declared_files") or ())
    undeclared = sorted(set(payload) - declared) if declared else []
    phantom = sorted(declared - set(payload)) if declared else []
    stage_bad = []
    for man_name in ("assets.meta.json", "neighbours.meta.json"):
        mp = bundle["_dir"] / man_name
        if not mp.exists():
            continue
        try:
            stage = json.loads(mp.read_text(encoding="utf-8"))
        except Exception as exc:
            stage_bad.append(f"{man_name} unreadable: {exc}")
            continue
        for fname, claimed in (stage.get("files") or {}).items():
            if fname in payload and not _sha256(payload[fname]).startswith(str(claimed)):
                stage_bad.append(f"{man_name} claims {fname}={claimed}, file disagrees")
    g9bad = ([f"UNDECLARED payload: {', '.join(undeclared)}"] if undeclared else []) \
        + ([f"declared but absent: {', '.join(phantom)}"] if phantom else []) + stage_bad
    gates.append(Gate("G9", "every payload file declared", not g9bad,
                      f"{len(payload)} payload files, all declared and sha-bound"
                      if not g9bad else "; ".join(g9bad)))

    # G7 coverage recorded (computed from the files, never inherited)
    epa_present, epa_ns = _epa_coverage(bundle["epa"], vocab_entries)
    fct_present, fct_ns = _facets_coverage(bundle["facets"], vocab_entries)
    offs = _nbr_offsets(bundle["neighbours"], vocab_entries)
    nbr_present = sum(1 for n in range(vocab_entries) if offs[n + 1] > offs[n])
    bundle["_coverage"] = {
        "facets": {"entries": fct_present, "coverage": round(fct_present / vocab_entries, 4)},
        "epa": {"entries": epa_present, "coverage": round(epa_present / vocab_entries, 4)},
        "neighbours": {"entries": nbr_present, "coverage": round(nbr_present / vocab_entries, 4)},
    }
    # Fixed-width channels measure the same way: a record equal to the ABSENT pattern is
    # absent, anything else is present. Written per-channel before, which is why adding
    # one meant editing here too and forgetting to meant it silently had no coverage.
    _ABSENT_PATTERN = {"vfacets": VFT_ABSENT, "wordclass": b"\x00\x00\x00"}
    for name in chans:
        if name in bundle["_coverage"] or name not in _ABSENT_PATTERN:
            continue
        a = BY_NAME[name]
        raw = bundle[name].read_bytes()
        w, pat = a.record_width, _ABSENT_PATTERN[name]
        present = sum(1 for n in range(vocab_entries)
                      if raw[HDR_LEN + n * w: HDR_LEN + (n + 1) * w] != pat)
        bundle["_coverage"][name] = {
            "entries": present, "coverage": round(present / vocab_entries, 4)}
    # SPARSE assets get an entry count and NO coverage ratio. There is no denominator:
    # `morph_map` holds decided PAIRS, and 48,000 pairs out of what? Dividing by the
    # vocabulary would produce a percentage that looks like coverage and means nothing --
    # the same "a number with no referent" complaint this lane made about mode share not
    # saying 99.9% OF WHAT. Report the count, refuse to invent the ratio.
    for name in chans:
        if name in bundle["_coverage"] or BY_NAME[name].n_parallel:
            continue
        bundle["_coverage"][name] = {"entries": counts.get(name, 0), "coverage": None}
    gates.append(Gate("G7", "coverage recorded", True, ", ".join(
        (f"{n} {c['entries']}/{vocab_entries} ({100*c['coverage']:.1f}%)"
         if c["coverage"] is not None
         else f"{n} {c['entries']:,} entries (sparse, no denominator)")
        for n, c in bundle["_coverage"].items())))

    # G10 -- every asset the BUILD carries is shipped, or the registry says why not.
    #
    # G3 compares the manifest's present-flags to the shipped channels, but only for
    # channels in BUNDLE_CHANNELS -- so `wordclass` passed publish for two weeks by not
    # being in that tuple. An asset is not absent when it is unmentioned; it is invisible,
    # and the two are indistinguishable from inside a hand-kept list. This asks the
    # LMDB instead.
    lmdb_p = build_dir / "dictionary.lmdb"
    g10bad = []
    for a in ASSETS:
        if not a.in_lmdb or a.unbuilt_reason or a.name in ("forward", "reverse"):
            continue
        if not _has_subdb_publish(lmdb_p, a.subdb):
            continue
        if a.name not in chans:
            g10bad.append(f"{a.name}: the build carries it and the bundle does not ship it"
                          + ("" if a.ships else " (registry says ships=False -- set it, "
                                                "or record why this asset stays local)"))
    gates.append(Gate("G10", "every built asset ships", not g10bad,
                      f"{len(chans)} channels: {', '.join(chans)}"
                      if not g10bad else "; ".join(g10bad)))

    # G11 -- CONTENT MOVED => REVISION MOVED.
    #
    # `package_revision` started life meaning "the asset SET", which does not describe a
    # rebuild that changes asset CONTENT -- and the facets/utility fix changes 14,394
    # facet records without adding anything. A consumer caching facet verdicts under
    # elo-browser-v04 would be silently wrong. So the definition is now **the assets,
    # INCLUDING their content**.
    #
    # A definition alone is not enough. An integer someone must remember to bump is the
    # same failure mode as the nine hand-kept asset lists this module spent the day
    # replacing, so the counter is CHECKED rather than trusted: compare this bundle's
    # per-channel shas against every already-published revision of the same build. If
    # any channel's bytes differ and the revision did not move, refuse.
    g11 = ""
    try:
        # The publication root comes from the BUNDLE, not from a module global. The
        # first cut reached for `ROOT`, which is a local inside main() -- so G11 threw
        # NameError and reported "could not compare", i.e. a gate that could not run
        # looked exactly like a gate that failed. Passing the path in is what makes the
        # check honest about which of those two it is.
        _out = (Path(bundle["_out_root"]) if bundle.get("_out_root")
                else Path(__file__).resolve().parent.parent / "dist" / "dictionary")
        _base = bundle.get("_build_name", "")
        _rev = int(bundle.get("_revision", 1))
        prior = []
        if _out.exists():
            for d in sorted(_out.iterdir()):
                if not d.is_dir() or not (d / "BUNDLE.json").exists():
                    continue
                pd = json.loads((d / "BUNDLE.json").read_text(encoding="utf-8"))
                if pd.get("build") == _base:
                    prior.append((int(pd.get("package_revision", 1)), d.name, pd))
        clashes = []
        for prev_rev, dname, pd in prior:
            if prev_rev != _rev:
                continue
            for cname, crec in (pd.get("channels") or {}).items():
                now = bundle["_coverage"].get(cname)
                if now is None:
                    continue
                cur_sha = _sha256(bundle[cname]) if cname in bundle else None
                if cur_sha and crec.get("sha256") and cur_sha != crec["sha256"]:
                    clashes.append(f"{cname} differs from {dname} at the same revision "
                                   f"{_rev}")
        if clashes:
            gates.append(Gate("G11", "content moved => revision moved", False,
                              "; ".join(clashes) + ". Run build_assets.py --bump-revision"))
        else:
            g11 = (f"revision {_rev}; {len(prior)} prior publication(s) of {_base}"
                   if prior else f"revision {_rev}; first publication of {_base}")
            gates.append(Gate("G11", "content moved => revision moved", True, g11))
    except Exception as exc:                                    # noqa: BLE001
        # ERRORED, not FAILED -- and it still refuses. A check that could not run tells
        # you nothing about the bundle, so publishing on it would be publishing on an
        # unanswered question. Say which of the two it is, because "G11 FAIL" sent me
        # looking at the bundle when the fault was a NameError in the gate.
        gates.append(Gate("G11", "content moved => revision moved", False,
                          f"GATE ERRORED (this is a bug in the gate, not a verdict on "
                          f"the bundle): {type(exc).__name__}: {exc}"))

    # G8 spot read -- resolve a real surface through all three channels at one n
    #     Pick an n that has all three (so the proof is end-to-end), else fail loudly.
    triple = set(epa_ns) & set(fct_ns) & {n for n in range(vocab_entries) if offs[n + 1] > offs[n]}
    if triple:
        n = min(triple)
        surface = next((e["surface"] for e in vocab["entries"] if e["n"] == n), f"n={n}")
        e, p, a = EPA_REC.unpack_from(bundle["epa"].read_bytes(), HDR_LEN + n * EPA_REC.size)
        bucket = bundle["facets"].read_bytes()[HDR_LEN + n * FCT_REC.size]
        nbrs = _nbr_of(bundle["neighbours"], n, vocab_entries)
        ok = (bucket != BUCKET_ABSENT and not math.isnan(e) and 0 < len(nbrs) <= 16)
        gates.append(Gate("G8", "spot read (end-to-end)", ok,
                          f"n={n} '{surface}': facets bucket={bucket}, epa=({e:.2f},{p:.2f},{a:.2f}), "
                          f"{len(nbrs)} neighbours (k<=16)"))
    else:
        gates.append(Gate("G8", "spot read (end-to-end)", False,
                          "no single n carries facets+epa+neighbours -- channels may be misaligned"))

    # G5 round-trip + G6 facets -- against the SOURCE lmdb (the bundle ships no lmdb).
    # Each verifier wants its OWN cwd + path: verify_lossless imports the
    # `semantic_compression` PACKAGE and resolves sample paths from the repo root;
    # verify_facets imports flat modules (config/facets) from semantic_compression/.
    # Put both dirs on PYTHONPATH so either import style resolves regardless.
    lmdb_path = build_dir / "dictionary.lmdb"
    SC_DIR = Path(__file__).resolve().parent
    ROOT_DIR = SC_DIR.parent
    if run_heavy:
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(
            p for p in (str(ROOT_DIR), str(SC_DIR), os.environ.get("PYTHONPATH", "")) if p)}
        for gid, name, script, cwd in (
                ("G5", "round-trip (verify_lossless)", "verify_lossless.py", ROOT_DIR),
                ("G6", "facets (verify_facets)", "verify_facets.py", SC_DIR)):
            r = subprocess.run([sys.executable, str(SC_DIR / script), "--db", str(lmdb_path)],
                               capture_output=True, text=True, cwd=str(cwd), env=env)
            # On failure, the ERROR is the detail -- prefer stderr, then the last
            # stdout line. (A failing verify_facets printed "[OK] T4 ..." as its
            # last stdout line while the assertion sat in stderr; the gate showed
            # an OK line next to FAIL, which helped nobody.)
            if r.returncode == 0:
                tail = (r.stdout or r.stderr).strip().splitlines()
            else:
                tail = (r.stderr or r.stdout).strip().splitlines()
            gates.append(Gate(gid, name, r.returncode == 0,
                              (tail[-1] if tail else f"exit {r.returncode}")))
    else:
        for gid, name in (("G5", "round-trip (verify_lossless)"), ("G6", "facets (verify_facets)")):
            gates.append(Gate(gid, name, True, "SKIPPED (--no-heavy); run before real publish"))
    return gates


# --- assembly ---------------------------------------------------------------------
def load_bundle(bundle_dir: Path, build_dir: Path, build_name: str) -> dict:
    vocab_path = bundle_dir / f"{build_name}.browser.json"
    if not vocab_path.exists():
        raise SystemExit(f"bundle incomplete: missing {vocab_path}")

    # ONE RULE FOR EVERY ASSET (2026-09-10): the build carries it => the bundle ships it.
    #
    # This was three hardcoded paths plus a bespoke `vfacets` branch. `wordclass` was in
    # neither, so a build carrying 437,995 wordclass records published a bundle without
    # it and no gate objected -- the asset was not absent, it was unmentioned, and those
    # look identical from inside a hand-kept list.
    lmdb_path = build_dir / "dictionary.lmdb"
    files, channels, missing = {}, [], []
    for a in ASSETS:
        if not (a.ships and a.bin_file):
            continue
        p = bundle_dir / a.bin_file
        # An asset with no sub-db (neighbours) is judged by its file alone; one with a
        # sub-db is REQUIRED in the bundle exactly when the build has it.
        build_has = True if a.subdb is None else _has_subdb_publish(lmdb_path, a.subdb)
        if build_has and not p.exists():
            missing.append(f"  {a.name}: the build carries it, {p.name} is missing")
            continue
        if p.exists():
            files[a.name] = p
            channels.append(a.name)
            # A channel ships WITH its geometry or it does not ship. `vfacets.bin` was
            # published without `vfacets.names.json` for a month: the data was hashed,
            # the contract that decodes it stayed in the browser tree.
            if a.contract_file and not (bundle_dir / a.contract_file).exists():
                missing.append(f"  {a.name}: {p.name} ships but its decode contract "
                               f"{a.contract_file} is not in the bundle")
    if missing:
        raise SystemExit("bundle incomplete:\n" + "\n".join(missing)
                         + "\n\nEvery asset in asset_registry.ASSETS with ships=True must "
                           "be exported before publish. Re-run the exporter stages.")
    vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
    # FRAMED assets carry the 80-byte magic+count+fingerprint header. A SELF-DESCRIBING
    # one (morph_map.json) carries its identity inside the file, so asking it for a
    # header would read its first 8 bytes as a magic and fail -- or worse, not fail.
    counts, fps = {}, {}
    for role, p in files.items():
        a = BY_NAME[role]
        if a.framed:
            counts[role], fps[role] = _read_header(p, role)
        else:
            counts[role], fps[role] = _read_self_described(p, role)
    man_path = build_dir / "manifest.json"
    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
    return {
        **files, "vocab": vocab_path, "_dir": bundle_dir, "_vocab": vocab,
        "_vocab_entries": int(vocab["content_size"]),
        "_counts": counts, "_fps": fps,
        "_channels": channels,                 # 3 required + vfacets when present
        # What BUNDLE.json will declare: the directory, not a list. G9 checks both
        # directions against it so the assembly and the gate cannot drift apart.
        "_declared_files": set(payload_files(bundle_dir)),
        # For G11: which build this is a revision OF, and which revision.
        "_build_name": build_name,
        "_revision": int(manifest.get("package_revision", 1)),
        "_source_fp": fps["facets"],           # canonical; G2 checks the others match
        "_build_lmdb_fp": _build_lmdb_fingerprint(build_dir),
        "_manifest": manifest,
    }


def build_bundle_json(bundle: dict, build_name: str, display: str, published: bool) -> dict:
    src_fp = bundle["_source_fp"]
    cov = bundle["_coverage"]
    nbr_count, _ = _read_header(bundle["neighbours"], "neighbours")
    # Per-file sha over every payload file in the directory -- NOT over a list kept
    # here. The previous list omitted `vfacets.names.json`, which is the file that
    # declares `polarity_known`: the field contract shipped unbound while the data it
    # describes shipped bound. G9 makes that combination impossible.
    shipped = payload_files(bundle["_dir"])
    shas = {name: _sha256(p) for name, p in sorted(shipped.items())}
    fp_material = "".join(f"{name}\t{sha}\n" for name, sha in sorted(shas.items()))
    bundle_fp = hashlib.sha256(fp_material.encode("utf-8")).hexdigest()
    vocab = bundle["_vocab"]
    man = bundle.get("_manifest") or {}
    smeta = man.get("spec_meta") or {}
    return {
        "schema": SCHEMA,
        "build": build_name,
        "display": display,
        "status": "published" if published else "candidate",
        "published_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "bundle_fingerprint": bundle_fp,
        "source_build_fingerprint": src_fp,
        "vocab": {"entries": bundle["_vocab_entries"], "cut": vocab.get("cut"),
                  "version": vocab.get("vocab_version")},
        # THE ASSET-SET IDENTITY, distinct from the id space (Paul, 2026-09-10).
        #
        #   source_build_fingerprint  the ID SPACE. An `.elo` binds to THIS, and it does
        #                             not move when an asset is added.
        #   package_revision          the ASSET SET. Increments every time a build is
        #                             widened, so two bundles both named elo-browser-v04
        #                             are distinguishable and a consumer can require
        #                             "v04 at revision >= 2" when it needs wordclass.
        #
        # Without the second, "rebuilt with the new asset" and "the original" are the
        # same string, and the only thing separating them is a date nobody reads.
        "package_revision": int(man.get("package_revision", 1)),
        # bundle_id IN THE MANIFEST OF RECORD. It lived only in STANDARD.json, so the
        # 2026-09-12 broadcast told consumers to pin `bundle_id` and, two sentences later,
        # that everything they need is in BUNDLE.json -- and both could not be true.
        # ELO-Browser read the artifact rather than the prose and found it. A pin a
        # consumer cannot find in the file you told them to read is not a pin.
        "bundle_id": (build_name if int(man.get("package_revision", 1)) <= 1
                      else f"{build_name}r{int(man.get('package_revision', 1))}"),
        "revision_log": man.get("revision_log", []),
        # WHICH FINGERPRINT TO PIN (2026-09-16, asked by the reasoning lane).
        #
        # This file spells one identity three ways -- `source_build_fingerprint`,
        # `provenance.manifest_dictionary_fp`, and `dictionary_fingerprint` over in
        # STANDARD.json -- and publishes a second, different identity beside them
        # (`bundle_fingerprint`). All three spellings of the first agree today; that is
        # luck holding, not a contract, and the reasoning lane's pin check returned
        # `unverifiable` rather than guess between them.
        #
        # A consumer guessing wrong FAILS OPEN: verify against `bundle_fingerprint` and
        # you get a green pin for an asset set built on a different dictionary, because
        # the packaging identity moves on every content revision while the id space does
        # not. That is the worst available failure -- a check that passes wrongly.
        #
        # So the file now names its own canonical field rather than leaving it to prose
        # in a handoff that not every consumer reads.
        "identity": {
            "pin": "bundle_id",
            "id_space_field": "source_build_fingerprint",
            "asset_set_field": "bundle_fingerprint",
            "echoes": ["provenance.manifest_dictionary_fp"],
            "note": ("`source_build_fingerprint` IS the dictionary identity -- what an "
                     ".elo binds to, identical across revisions by design. Verify a pin "
                     "against it. `provenance.manifest_dictionary_fp` is an echo of the "
                     "same value and MUST NOT be pinned against independently. "
                     "`bundle_fingerprint` identifies the ASSET SET and moves on every "
                     "content revision -- pinning against it reports drift when nothing "
                     "moved, and pinning the id space against it fails open."),
        },
        "files": {name: {"sha256": sha, "bytes": shipped[name].stat().st_size}
                  for name, sha in sorted(shas.items())},
        # DERIVED from the registry + measured coverage, not four hand-written blocks.
        # Each channel carries its ABSENT rule into the bundle, so a consumer never has
        # to guess whether a zero is a measurement -- the single most repeated bug in
        # this system, in every channel that has one.
        # GATE vs DIAGNOSTIC, per elo-sdm's 2026-09-10 refinement -- the sharpest thing
        # anyone said about this bundle:
        #
        #   "Binding a file is the DIAGNOSTIC half. A sha tells a consumer the file is
        #    the one that shipped; it does not tell them the channel still MEANS what
        #    their code expects."
        #
        # That is precisely the facets defect: `facets.bin` was intact, sha-bound, and
        # WRONG on 14,394 surfaces. No byte check could have caught it.
        #
        #   gate=       refuse on a change. Geometry -- record width, format version,
        #               absent rule. A consumer decoding with last month's geometry gets
        #               garbage SHAPED LIKE DATA, which is the failure that cannot be
        #               noticed downstream.
        #   diagnostic= explain a red, never cause one. Coverage and entry counts move
        #               on every legitimate re-derivation; gating them would red every
        #               consumer for a change they do not replay.
        #
        # `format_version` is READ FROM THE CHANNEL'S OWN CONTRACT FILE, never restated
        # here -- a version number typed in a second place is how `wordclass` came to be
        # documented as "reserved, ships with v05, 2 bytes" while shipping at 3 bytes.
        "channels": {
            name: {
                "file": BY_NAME[name].bin_file,
                "sha256": shas[BY_NAME[name].bin_file],
                "contract": BY_NAME[name].contract_file,
                "gate": {
                    "record_width": BY_NAME[name].record_width,
                    "format_version": _contract_version(bundle["_dir"], BY_NAME[name]),
                    "absent": BY_NAME[name].absent,
                },
                "diagnostic": {
                    "entries": cov[name]["entries"],
                    # null for a sparse asset -- there is no denominator, and a
                    # percentage invented for one reads as coverage and is not.
                    "coverage": cov[name]["coverage"],
                    "n_parallel": BY_NAME[name].n_parallel,
                },
            }
            for name in bundle.get("_channels", ()) if name in cov
        },
        # THE FOURTH IDENTITY. `bundle_id` says which asset set; `source_build_
        # fingerprint` says which id space; `codec.policy_version` says which ENCODER
        # produced, and which DECODER will correctly read, the bytes. A consumer caching
        # encoded output or expected decode values must pin this one -- ELO-Browser's
        # recase fixture pins build + fingerprint + a facets sha, and NONE of the three
        # can move when the encoder's behaviour changes.
        "codec": man.get("codec"),
        "provenance": {
            "corpus_fingerprint": man.get("corpus_fingerprint"),
            "bound_model": smeta.get("llm_model") or man.get("bound_model"),
            "built_utc": man.get("created_utc"),
            "manifest_dictionary_fp": ((man.get("artifacts") or {}).get("dictionary") or {}).get("fingerprint"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("build", type=Path, nargs="?",
                    help="build package dir under db/builds/ (has dictionary.lmdb + manifest.json)")
    ap.add_argument("--verify", type=Path, default=None,
                    help="re-check an already-published bundle dir against its BUNDLE.json")
    ap.add_argument("--bundle-src", type=Path, default=None,
                    help="dir holding the exported channels "
                         "(default: <build>/bundle/, the cascade's staging dir)")
    ap.add_argument("--out-root", type=Path, default=None,
                    help="published bundles root (default: <repo>/dist/dictionary; NOT under "
                         "packages/, which is the uv workspace of Python packages)")
    ap.add_argument("--display", default=None, help="UI name; default from ACTIVE_BUILD or build name")
    ap.add_argument("--dry-run", action="store_true", help="assemble + gate; write nothing")
    ap.add_argument("--no-heavy", action="store_true",
                    help="skip G5/G6 subprocess gates (fast demo; NOT for a real publish)")
    ap.add_argument("--retag", type=Path, default=None,
                    help="clone an already-published bundle under a NEW build name (no rebuild); "
                         "same dictionary content. Needs --as. E.g. --retag elo-browser-v01c --as elo-browser-v02")
    ap.add_argument("--as", dest="as_name", default=None, help="new build name for --retag")
    a = ap.parse_args()

    SC = Path(__file__).resolve().parent
    ROOT = SC.parent

    if a.retag:
        if not a.as_name:
            ap.error("--retag requires --as <new-build-name>")
        src = a.retag
        if not (src / "BUNDLE.json").exists():
            cand = ROOT / "dist" / "dictionary" / src.name
            if (cand / "BUNDLE.json").exists():
                src = cand
        out_root = (a.out_root.resolve() if a.out_root else ROOT / "dist" / "dictionary")
        return retag_bundle(src.resolve(), a.as_name, out_root, a.dry_run)

    if a.verify:
        # Accept a full path OR a bare build name. publish writes to ROOT/dist/dictionary
        # (script-relative), so a relative path typed from semantic_compression/ would
        # otherwise resolve to the wrong place. Fall back to the default published root.
        vp = a.verify
        if not (vp / "BUNDLE.json").exists():
            cand = ROOT / "dist" / "dictionary" / vp.name
            if (cand / "BUNDLE.json").exists():
                vp = cand
        return _verify_published(vp.resolve())

    if not a.build:
        ap.error("a build package dir is required (or use --verify <bundle-dir>)")
    build_dir = a.build.resolve()
    if not build_dir.exists() and a.build.parent == Path("."):
        build_dir = (SC / "db" / "builds" / a.build.name).resolve()
    if not (build_dir / "dictionary.lmdb").exists():
        raise SystemExit(f"no dictionary.lmdb in {build_dir}")
    name = build_dir.name

    # PUBLISH READS THE BUILD'S OWN STAGING DIR (2026-09-11). This defaulted to
    # `ELO-Browser/.../dictionary/<name>`, so every publication -- v04r2 included --
    # assembled a dictionary bundle out of a consumer lane's repository, and said so on
    # its own first line: `bundle src: ELO-Browser/...`. The exporters moved here on
    # 09-10; the write path did not, which is why v04r2's `assets.meta.json` still
    # stamps `generated_by: ELO-Browser/tools/...`.
    #
    # Falls back to the old location when a build predates the move, so v01c and v04
    # remain republishable rather than being stranded by a layout change.
    bundle_src = a.bundle_src.resolve() if a.bundle_src else (build_dir / "bundle")
    if not a.bundle_src and not bundle_src.exists():
        legacy = ROOT / "ELO-Browser" / "elo-browser" / "src-tauri" / "dictionary" / name
        if legacy.exists():
            print(f"  note: no {bundle_src}; falling back to the pre-2026-09-11 "
                  f"location {legacy}")
            bundle_src = legacy
    if not bundle_src.exists():
        raise SystemExit(f"no exported bundle at {bundle_src}\n"
                         f"  run build_assets.py {build_dir} first (stages 6-8 export the channels)")
    # Published DATA bundles land OUTSIDE packages/. `packages/elo-dictionary/` is now
    # the Python PACKAGE (the codec/tokenizer, renamed from compression-dictionary,
    # 2026-08-07), and the uv workspace globs `packages/*` as members -- a data dir
    # there would be misread as a package. The bundle is a distribution artifact, so
    # it goes to dist/dictionary/<build>/. (spec-publish-dictionary sec 4.2.)
    out_root = (a.out_root.resolve() if a.out_root else ROOT / "dist" / "dictionary")
    display = a.display or name

    # THE BUNDLE ID CARRIES THE REVISION (2026-09-10).
    #
    # `elo-browser-v04` is the ID SPACE and must not be renamed -- renaming it is what
    # would renumber fixtures and orphan stored ids, for a problem that is not in the id
    # space. But two bundles built from that same id space with DIFFERENT asset content
    # cannot both be called `elo-browser-v04`, or a consumer holding cached facet
    # verdicts from revision 1 has no way to learn they are stale.
    #
    # So: build name is the id space, bundle id is `<build>r<revision>`, and consumers
    # pin the BUNDLE ID. ELO-Browser's FIXTURE_BUILD tripwire compares that string, so
    # it fires on a content revision exactly as it would on a new build -- which is the
    # behaviour it was written for and would NOT have got from a bare revision integer
    # tucked inside the json.
    _man_path = build_dir / "manifest.json"
    _man = json.loads(_man_path.read_text(encoding="utf-8")) if _man_path.exists() else {}
    revision = int(_man.get("package_revision", 1))
    bundle_id = name if revision <= 1 else f"{name}r{revision}"

    print(f"publish {name}   package_revision {revision}   bundle_id {bundle_id}")
    print(f"  source build : {build_dir}")
    print(f"  bundle src   : {bundle_src}")
    print(f"  dest         : {out_root / bundle_id}" + ("   [DRY RUN]" if a.dry_run else ""))

    bundle = load_bundle(bundle_src, build_dir, name)
    # G11 compares this bundle against prior publications of the same build, so it needs
    # to know where publications live -- including when --out-root moved them.
    bundle["_out_root"] = str(out_root)
    print(f"  vocab n=0..{bundle['_vocab_entries']-1}   source_fp={bundle['_source_fp'][:16]}\n")

    gates = run_gates(bundle, build_dir, run_heavy=not a.no_heavy)
    print(f"{'gate':<5}{'result':<7}check")
    print("-" * 78)
    for g in gates:
        print(f"{g.id:<5}{('PASS' if g.ok else 'FAIL'):<7}{g.name} -- {g.detail}")
    failed = [g for g in gates if not g.ok]
    print("-" * 78)

    doc = build_bundle_json(bundle, name, display, published=not failed)
    doc["gates"] = [g.as_dict() for g in gates]
    print(f"bundle_fingerprint {doc['bundle_fingerprint'][:16]}   "
          f"source_build_fingerprint {doc['source_build_fingerprint'][:16]}")

    if failed:
        # REPEAT THE REASON HERE. The gate table above already carries it, but the
        # refusal is the line people copy, and a bare "G11 failed" is not actionable --
        # it sends the reader back to a scrollback they may not have. A refusal should
        # be self-contained.
        print(f"\nREFUSED: {len(failed)} gate(s) failed -- "
              f"{', '.join(g.id for g in failed)}. "
              f"A partial or inconsistent bundle is never published.")
        for g in failed:
            print(f"\n  {g.id}  {g.name}\n      {g.detail}")
            if g.id == "G11":
                print(f"      -> the bundle's bytes differ from a publication at this "
                      f"same revision.\n"
                      f"         Bump it:  python build_assets.py {build_dir} "
                      f"--bump-revision \"<what changed>\"\n"
                      f"         Current package_revision: {revision}  "
                      f"(would publish as {bundle_id})")
            if g.id == "G10":
                print("      -> an asset the build carries is not in the bundle. Run "
                      "its exporter stage, or record in asset_registry why it stays "
                      "local.")
        return 2
    if a.dry_run:
        print("\n--dry-run: all gates pass; nothing written. Re-run without --dry-run to publish.")
        return 0

    dest = out_root / bundle_id
    if dest.exists():
        raise SystemExit(
            f"REFUSED: {dest} already exists. A published bundle is immutable.\n"
            f"  If the ASSET CONTENT changed, bump the revision:\n"
            f"    python build_assets.py {build_dir} --bump-revision \"<what changed>\"\n"
            f"  If the ID SPACE changed, that is a new dictionary and needs a new build "
            f"name (spec sec 4.2).")
    dest.mkdir(parents=True)
    import shutil
    # THE FOURTH HAND-MAINTAINED LIST, and the one that did the damage. `to_copy` named
    # six files and omitted `vfacets.names.json`, so elo-browser-v04 published
    # `vfacets.bin` with NO DECODE CONTRACT in the bundle: shifts, masks, value names and
    # `polarity_known` all stayed behind in the browser tree. `facets.bin` shipped with
    # its names file; `vfacets.bin` did not, and nothing compared the two.
    #
    # Copy the payload, from the directory. `files` in BUNDLE.json is derived the same
    # way, from the same function, so the manifest and the copy cannot disagree -- they
    # were two lists before, and they did.
    for src in payload_files(bundle_src).values():
        shutil.copyfile(src, dest / src.name)
    (dest / "BUNDLE.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\npublished -> {dest}\n  {len(doc['files'])+1} files, bundle_fingerprint {doc['bundle_fingerprint'][:16]}")
    return 0


def retag_bundle(src_dir: Path, new_name: str, out_root: Path, dry: bool) -> int:
    """Clone a published bundle under a NEW build name, no rebuild. Same dictionary
    content -- `source_build_fingerprint` is carried over unchanged (that IS the
    dictionary's identity), so v02 provably encodes the same dictionary as v01c; only
    the label and the (renamed) vocab file change, so `bundle_fingerprint` is new.
    Refuses if the source doesn't verify or the destination already exists."""
    import shutil
    bj = src_dir / "BUNDLE.json"
    if not bj.exists():
        raise SystemExit(f"source has no BUNDLE.json: {src_dir}")
    src = json.loads(bj.read_text(encoding="utf-8"))
    src_name = src["build"]
    print(f"re-tag {src_name} -> {new_name}   (same dictionary; "
          f"source_build_fingerprint {src['source_build_fingerprint'][:16]})")

    # 1. the source must be intact before we clone it.
    if _verify_published(src_dir) != 0:
        raise SystemExit("source bundle failed --verify; refusing to re-tag a broken bundle")

    dest = out_root / new_name
    if dest.exists():
        raise SystemExit(f"REFUSED: {dest} already exists. A published bundle is immutable.")
    src_vocab = src_dir / f"{src_name}.browser.json"
    if not src_vocab.exists():
        raise SystemExit(f"source vocab missing: {src_vocab}")
    if dry:
        print(f"--dry-run: would write {dest} (5 files + BUNDLE.json); nothing written.")
        return 0

    dest.mkdir(parents=True)
    # channels are fingerprint-keyed and name-agnostic -> copy verbatim.
    retag_files = ["epa.bin", "facets.bin", "neighbours.bin", "facets.names.json"]
    if (src_dir / "vfacets.bin").exists():
        retag_files.append("vfacets.bin")
    for fn in retag_files:
        shutil.copyfile(src_dir / fn, dest / fn)
    # vocab carries the label -> rename the file and re-stamp vocab_version.
    vocab = json.loads(src_vocab.read_text(encoding="utf-8"))
    vocab["vocab_version"] = new_name
    (dest / f"{new_name}.browser.json").write_text(
        json.dumps(vocab, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    shipped = {f"{new_name}.browser.json": dest / f"{new_name}.browser.json",
               **{fn: dest / fn for fn in retag_files}}
    shas = {n: _sha256(p) for n, p in sorted(shipped.items())}
    bundle_fp = hashlib.sha256(
        "".join(f"{n}\t{s}\n" for n, s in sorted(shas.items())).encode("utf-8")).hexdigest()

    doc = dict(src)
    doc.update({
        "build": new_name,
        "status": "published",
        "published_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "bundle_fingerprint": bundle_fp,       # new: the files changed (vocab relabelled)
        # source_build_fingerprint: UNCHANGED -- proves identical dictionary content
        "files": {n: {"sha256": s, "bytes": shipped[n].stat().st_size} for n, s in sorted(shas.items())},
    })
    doc["vocab"] = {**src.get("vocab", {}), "version": new_name}
    for ch, meta in (doc.get("channels") or {}).items():
        f = meta.get("file")
        if f in shas:
            meta["sha256"] = shas[f]
    doc.setdefault("provenance", {})
    doc["provenance"]["retagged_from"] = src_name
    doc["provenance"]["retag_note"] = ("identical dictionary content; only the build label + "
                                       "vocab_version changed. Same source_build_fingerprint.")
    doc["gates"] = [{"gate": "RETAG", "name": "clone of a verified bundle", "result": "PASS",
                     "detail": f"source {src_name} passed --verify; channels copied byte-for-byte"}]
    (dest / "BUNDLE.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nre-tagged -> {dest}\n  6 files, bundle_fingerprint {bundle_fp[:16]}  "
          f"source_build_fingerprint {doc['source_build_fingerprint'][:16]} (unchanged)")
    return 0


def _verify_published(bundle_dir: Path) -> int:
    """Recompute every sha + the bundle fingerprint and compare to BUNDLE.json."""
    bj = bundle_dir / "BUNDLE.json"
    if not bj.exists():
        raise SystemExit(f"no BUNDLE.json in {bundle_dir}")
    doc = json.loads(bj.read_text(encoding="utf-8"))
    print(f"verify {doc['build']}  (recorded bundle_fingerprint {doc['bundle_fingerprint'][:16]})")
    ok = True
    shas = {}
    for name, rec in sorted(doc["files"].items()):
        p = bundle_dir / name
        if not p.exists():
            print(f"  MISSING {name}"); ok = False; continue
        got = _sha256(p)
        shas[name] = got
        match = got == rec["sha256"]
        ok &= match
        print(f"  {'ok  ' if match else 'FAIL'} {name}  {got[:16]}")

    # THE OTHER DIRECTION. Iterating `doc["files"]` alone only proves that what was
    # declared is intact -- it says nothing about a file sitting in the bundle that
    # nothing declares, which is precisely how `neighbours.bin` (22 MB) and
    # `vfacets.names.json` shipped unbound in elo-browser-v04 while every check passed.
    # A one-way integrity check cannot detect an omission; it can only detect damage.
    undeclared = sorted(set(payload_files(bundle_dir)) - set(doc["files"]))
    if undeclared:
        ok = False
        for name in undeclared:
            print(f"  FAIL {name}  UNDECLARED -- present in the bundle, absent from "
                  f"BUNDLE.json")
    material = "".join(f"{n}\t{s}\n" for n, s in sorted(shas.items()))
    recomputed = hashlib.sha256(material.encode()).hexdigest()
    fp_ok = recomputed == doc["bundle_fingerprint"]
    ok &= fp_ok
    print(f"  bundle_fingerprint {'matches' if fp_ok else 'MISMATCH ' + recomputed[:16]}")

    # CROSS-COPY CHECK (2026-08-27, Paul's question): internal verification alone let
    # two artifacts both named <build> -- the frozen publication and the live browser
    # bundle -- diverge silently (measured: v04 vfacets 0e146a62 published vs e18d52f0
    # live, both "OK"). Staged re-derivation is PERMITTED; being invisible is not.
    # Compare against the live bundle when present and SAY SO -- a warning, not a
    # failure, because under status=staged the divergence is legal.
    live = (Path(__file__).resolve().parent.parent / "ELO-Browser" / "elo-browser"
            / "src-tauri" / "dictionary" / doc["build"])
    if live.exists():
        drift = []
        for name in doc["files"]:
            lp = live / name
            if lp.exists() and _sha256(lp) != doc["files"][name]["sha256"]:
                drift.append(name)
        extra = sorted(set(payload_files(live)) - set(doc["files"]))
        if extra:
            print(f"  ⚠ the live bundle carries {len(extra)} file(s) this publication "
                  f"does not describe: {', '.join(extra)}")
        if drift:
            print(f"  ⚠ STAGED DRIFT: live bundle differs from this publication in "
                  f"{len(drift)} file(s): {', '.join(drift)}")
            print(f"    (legal while status=staged -- derived channels advanced since "
                  f"published_utc {doc.get('published_utc')}. Republish under a new "
                  f"name, or record this publication as superseded.)")
        else:
            print("  live bundle matches this publication (no staged drift)")
    print("OK" if ok else "FAILED")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
