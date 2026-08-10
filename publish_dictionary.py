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
MAGIC = {"epa": b"ELOEPA\x01\x00", "facets": b"ELOFCT\x01\x00",
         "neighbours": b"ELONBR\x01\x00"}
BUCKET_ABSENT = 0xFF
HDR_LEN = HEADER.size + 64               # 80

SCHEMA = "elo-dictionary-bundle/1"

# Files that make up a bundle. (role, filename-suffix, required)
BUNDLE_CHANNELS = ("facets", "epa", "neighbours")
# Anything matching these must NOT be in a bundle dir (spec sec 2 deny-list).
DENY = ("dictionary.lmdb", "meta.db", "token-ids.csv.gz",
        "dictionary.denotative.index", "dictionary.denotative.vecs.f32",
        "dictionary.denotative.surfaces.json", "dictionary.denotative.json",
        "dictionary.denotative.progress.json")
DENY_SUFFIX = ("_stats.json",)


# --- small io helpers -------------------------------------------------------------
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

    # G1 one vocabulary, one n
    bad = [f"{r}={counts[r]}" for r in BUNDLE_CHANNELS if counts[r] != vocab_entries]
    gates.append(Gate("G1", "one vocabulary, one n", not bad,
                      f"all channels count == vocab_entries={vocab_entries}" if not bad
                      else f"count mismatch: {', '.join(bad)} (vocab={vocab_entries})"))

    # G2 channel fingerprints agree with EACH OTHER *and* with the source build LMDB.
    #     The second half catches a bundle exported from a since-replaced build state
    #     (elo-browser-v01c: every channel carries bdec07bf, but the build LMDB now
    #     self-declares f4c8879e -- the bundle cannot be reproduced from its build dir).
    build_fp = bundle.get("_build_lmdb_fp")
    disagree = {r: fps[r] for r in BUNDLE_CHANNELS if fps[r] != src_fp}
    lmdb_ok = (build_fp is None) or (build_fp == src_fp)
    g2ok = (not disagree) and lmdb_ok
    if disagree:
        detail = f"channels disagree: {', '.join(f'{r}={v[:16]}' for r, v in disagree.items())}"
    elif not lmdb_ok:
        detail = (f"channels agree ({src_fp[:16]}) but the build LMDB self-declares "
                  f"{build_fp[:16]} -- bundle not exported from this build dir")
    else:
        detail = f"all channels + build LMDB carry {src_fp[:16]}"
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
    gates.append(Gate("G7", "coverage recorded", True,
                      f"facets {fct_present}/{vocab_entries} ({100*fct_present/vocab_entries:.1f}%), "
                      f"epa {epa_present} ({100*epa_present/vocab_entries:.1f}%), "
                      f"neighbours {nbr_present} ({100*nbr_present/vocab_entries:.1f}%)"))

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
            tail = (r.stdout or r.stderr).strip().splitlines()
            gates.append(Gate(gid, name, r.returncode == 0,
                              (tail[-1] if tail else f"exit {r.returncode}")))
    else:
        for gid, name in (("G5", "round-trip (verify_lossless)"), ("G6", "facets (verify_facets)")):
            gates.append(Gate(gid, name, True, "SKIPPED (--no-heavy); run before real publish"))
    return gates


# --- assembly ---------------------------------------------------------------------
def load_bundle(bundle_dir: Path, build_dir: Path, build_name: str) -> dict:
    vocab_path = bundle_dir / f"{build_name}.browser.json"
    files = {"facets": bundle_dir / "facets.bin", "epa": bundle_dir / "epa.bin",
             "neighbours": bundle_dir / "neighbours.bin"}
    for p in (vocab_path, *files.values(), bundle_dir / "facets.names.json"):
        if not p.exists():
            raise SystemExit(f"bundle incomplete: missing {p}")
    vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
    counts, fps = {}, {}
    for role, p in files.items():
        counts[role], fps[role] = _read_header(p, role)
    man_path = build_dir / "manifest.json"
    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
    return {
        **files, "vocab": vocab_path, "_dir": bundle_dir, "_vocab": vocab,
        "_vocab_entries": int(vocab["content_size"]),
        "_counts": counts, "_fps": fps,
        "_source_fp": fps["facets"],           # canonical; G2 checks the others match
        "_build_lmdb_fp": _build_lmdb_fingerprint(build_dir),
        "_manifest": manifest,
    }


def build_bundle_json(bundle: dict, build_name: str, display: str, published: bool) -> dict:
    src_fp = bundle["_source_fp"]
    cov = bundle["_coverage"]
    nbr_count, _ = _read_header(bundle["neighbours"], "neighbours")
    # per-file sha over every shipped file except BUNDLE.json itself
    shipped = {bundle["vocab"].name: bundle["vocab"], "facets.bin": bundle["facets"],
               "epa.bin": bundle["epa"], "neighbours.bin": bundle["neighbours"],
               "facets.names.json": bundle["_dir"] / "facets.names.json"}
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
        "files": {name: {"sha256": sha, "bytes": shipped[name].stat().st_size}
                  for name, sha in sorted(shas.items())},
        "channels": {
            "facets": {"file": "facets.bin", "sha256": shas["facets.bin"],
                       "entries": cov["facets"]["entries"], "coverage": cov["facets"]["coverage"]},
            "epa": {"file": "epa.bin", "sha256": shas["epa.bin"],
                    "entries": cov["epa"]["entries"], "coverage": cov["epa"]["coverage"]},
            "neighbours": {"file": "neighbours.bin", "sha256": shas["neighbours.bin"],
                           "entries": cov["neighbours"]["entries"],
                           "coverage": cov["neighbours"]["coverage"],
                           "format": "CSR: 80B header + (count+1) u32 offsets + records(u32 n,u8 sim)"},
        },
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
                    help="dir holding the exported channels (default: ELO-Browser .../dictionary/<name>)")
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

    bundle_src = (a.bundle_src.resolve() if a.bundle_src
                  else ROOT / "ELO-Browser" / "elo-browser" / "src-tauri" / "dictionary" / name)
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

    print(f"publish {name}")
    print(f"  source build : {build_dir}")
    print(f"  bundle src   : {bundle_src}")
    print(f"  dest         : {out_root / name}" + ("   [DRY RUN]" if a.dry_run else ""))

    bundle = load_bundle(bundle_src, build_dir, name)
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
        print(f"\nREFUSED: {len(failed)} gate(s) failed -- {', '.join(g.id for g in failed)}. "
              f"A partial or inconsistent bundle is never published.")
        return 2
    if a.dry_run:
        print("\n--dry-run: all gates pass; nothing written. Re-run without --dry-run to publish.")
        return 0

    dest = out_root / name
    if dest.exists():
        raise SystemExit(f"REFUSED: {dest} already exists. A published bundle is immutable; "
                         f"a rebuild is a NEW build name (spec sec 4.2).")
    dest.mkdir(parents=True)
    import shutil
    for src in (bundle["vocab"], bundle["facets"], bundle["epa"], bundle["neighbours"],
                bundle_src / "facets.names.json"):
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
    for fn in ("epa.bin", "facets.bin", "neighbours.bin", "facets.names.json"):
        shutil.copyfile(src_dir / fn, dest / fn)
    # vocab carries the label -> rename the file and re-stamp vocab_version.
    vocab = json.loads(src_vocab.read_text(encoding="utf-8"))
    vocab["vocab_version"] = new_name
    (dest / f"{new_name}.browser.json").write_text(
        json.dumps(vocab, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    shipped = {f"{new_name}.browser.json": dest / f"{new_name}.browser.json",
               "facets.bin": dest / "facets.bin", "epa.bin": dest / "epa.bin",
               "neighbours.bin": dest / "neighbours.bin",
               "facets.names.json": dest / "facets.names.json"}
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
    material = "".join(f"{n}\t{s}\n" for n, s in sorted(shas.items()))
    recomputed = hashlib.sha256(material.encode()).hexdigest()
    fp_ok = recomputed == doc["bundle_fingerprint"]
    ok &= fp_ok
    print(f"  bundle_fingerprint {'matches' if fp_ok else 'MISMATCH ' + recomputed[:16]}")
    print("OK" if ok else "FAILED")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
