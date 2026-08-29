"""
dictionary_standard.py -- the INDEX and the STANDARD pointer.

RULING (integration lane, 2026-08-28): *package the RESOLVER, not the dictionary.*
The data stays a build artifact on disk; the ANSWER to "which one, and is it the one
you were built against?" becomes a single importable function with one env var and one
index.

WHY THIS EXISTS. A survey of consumers found four private implementations of one
missing function:

    Verbalizer            ELO_DICT_DB     -> semantic_compression/db/dictionary.lmdb
    ExtractionPipeline    ELO_DICT_LMDB   -> the same legacy file
    ExtractionPipeline/verify   --        -> Memory/data/dictionary.lmdb
    08-MCP                      --        -> base.dictionary.lmdb

Two spellings of one variable, and the ones that resolve at all land on a dictionary
that predates facets, vfacets, wordclass and the id renumbering. Measured: that file
is fingerprint 9a77e623/v1.2.0 with NO wordclass sub-DB, while the current build is
b0164e50/v4.0.0 with one. Both hold 437,995 forward entries -- which is exactly why
four lanes never noticed.

TWO ARTIFACTS, both tiny and both tracked:

  INDEX.json     every build under db/builds/, its fingerprint, release, status,
                 channels and ledger state. The survey found NO index across 11
                 builds; that absence is why "which one is real?" had four answers.
  STANDARD.json  a POINTER -- the promoted build's name + fingerprint. Never a copy.

WHY A POINTER AND NOT A COPY. Copying the artifact per promotion duplicates it and
invites the two-artifact ID-alignment risk this repo has already paid for (the postings
LMDB vs canonical.db built on different dates). A pointer has exactly one possible
inconsistency -- a fingerprint naming no build -- and that is checkable in one line.
Promoting v05 is editing one file, which is also what makes it reviewable in a diff.

IDENTITY IS READ FROM THE ARTIFACT, NEVER RESTATED. Every field here comes from the
LMDB's own meta sub-db or its manifest. A hand-typed fingerprint in a pointer file is
the same defect class as a hand-typed fingerprint in a doc.

    python dictionary_standard.py index                     # regenerate INDEX.json
    python dictionary_standard.py show                      # what is standard now
    python dictionary_standard.py promote elo-browser-v04   # write STANDARD.json
    python dictionary_standard.py promote <b> --allow-staged --force
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import lmdb

SC = Path(__file__).resolve().parent            # semantic_compression/
ROOT = SC.parent                                # R-D-concepts/
BUILDS = SC / "db" / "builds"
DIST = ROOT / "dist" / "dictionary"
INDEX_PATH = DIST / "INDEX.json"
STANDARD_PATH = DIST / "STANDARD.json"

SCHEMA_INDEX = "elo-dictionary-index/1"
SCHEMA_STANDARD = "elo-dictionary-standard/1"

# Stages whose presence in assets_pipeline.json means the package is complete.
# Presence == ran AND succeeded (the ledger records a stage only on success), so a
# missing key is a stage that never completed. Stage 14 (census) is intentionally
# NOT required: it postdates the shipped builds and is advisory, not structural.
REQUIRED_STAGES = {"1", "2", "3", "9", "10", "11"}


class PromotionRefused(RuntimeError):
    """A build was asked to become standard and did not qualify."""


def _identity(lmdb_path: Path) -> dict:
    """Read a build's identity from its OWN meta sub-db. Never from a filename."""
    out: dict = {"channels": [], "fingerprint": None, "release": None, "status": None}
    if not lmdb_path.exists():
        return out
    env = lmdb.open(str(lmdb_path), readonly=True, lock=False, max_dbs=20)
    try:
        main = env.open_db()
        with env.begin() as t:
            names = sorted(bytes(k).decode("utf-8", "replace") for k, _ in t.cursor(db=main))
        out["channels"] = names
        if "meta" in names:
            # handles open OUTSIDE the read txn -- python-lmdb raises otherwise
            mdb = env.open_db(b"meta", create=False)
            with env.begin() as t:
                for key, field in ((b"dictionary_fingerprint", "fingerprint"),
                                   (b"dictionary_release", "release"),
                                   (b"dictionary_status", "status")):
                    v = t.get(key, db=mdb)
                    if v:
                        out[field] = v.decode("utf-8", "replace")
        if "forward" in names:
            fdb = env.open_db(b"forward", create=False)
            with env.begin() as t:
                out["entries"] = t.stat(db=fdb)["entries"]
    finally:
        env.close()
    return out


def _ledger(build_dir: Path) -> dict:
    f = build_dir / "assets_pipeline.json"
    if not f.exists():
        return {"present": False, "green": False, "missing": sorted(REQUIRED_STAGES),
                "reason": "no assets_pipeline.json -- cascade never completed"}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        return {"present": True, "green": False, "missing": [], "reason": f"unreadable: {e}"}
    missing = sorted(REQUIRED_STAGES - set(data))
    return {"present": True, "green": not missing, "missing": missing,
            "stages": sorted(data, key=lambda s: int(s) if s.isdigit() else 99),
            "reason": "" if not missing else f"stages never completed: {', '.join(missing)}"}


def scan() -> list[dict]:
    """Inventory every build package under db/builds/."""
    rows: list[dict] = []
    if not BUILDS.exists():
        return rows
    for d in sorted(p for p in BUILDS.iterdir() if p.is_dir()):
        lm = d / "dictionary.lmdb"
        ident = _identity(lm)
        led = _ledger(d)
        rows.append({
            "build": d.name,
            "path": str(lm.relative_to(ROOT)).replace("\\", "/"),
            "exists": lm.exists(),
            "dictionary_fingerprint": ident["fingerprint"],
            "release": ident["release"],
            "status": ident["status"],
            "entries": ident.get("entries"),
            "channels": ident["channels"],
            "ledger_green": led["green"],
            "ledger_missing": led["missing"],
            "sidecars": sorted(p.name for p in d.glob("*.json")) if d.exists() else [],
        })
    return rows


def write_index() -> dict:
    rows = scan()
    cur = read_standard()
    doc = {
        "schema": SCHEMA_INDEX,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "standard_build": cur.get("build") if cur else None,
        "note": ("ids are build-specific -- bind by SURFACE, verify by fingerprint. "
                 "A build listed here is not thereby endorsed; see STANDARD.json."),
        "builds": rows,
    }
    DIST.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def read_standard() -> dict | None:
    if not STANDARD_PATH.exists():
        return None
    try:
        return json.loads(STANDARD_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def promote(build: str, *, allow_staged: bool = False, force: bool = False,
            by: str = "dictionary lane", notes: str = "") -> dict:
    """Make `build` the standard. REFUSES rather than warning.

    A promotion silently repoints every consumer in the project, so the failure mode
    of a wrong promotion is broad and quiet. Every check below therefore raises."""
    d = BUILDS / build
    lm = d / "dictionary.lmdb"
    if not lm.exists():
        raise PromotionRefused(f"no build package at {lm}")

    ident = _identity(lm)
    if not ident["fingerprint"]:
        raise PromotionRefused(
            f"{build} has no dictionary_fingerprint in its meta sub-db -- it was never "
            f"stamped (stage 10). An unstamped build has no identity to bind to.")

    led = _ledger(d)
    if not led["green"] and not force:
        raise PromotionRefused(
            f"{build} ledger is not green -- {led['reason']}. A build is not done until "
            f"its assets_pipeline.json ledger is green. Re-run the cascade, or --force "
            f"if you are deliberately promoting an incomplete package.")

    if ident["status"] == "staged" and not allow_staged:
        raise PromotionRefused(
            f"{build} is status=staged. Promoting a staged build makes provisional ids "
            f"the project default. Pass --allow-staged if that is intended (it is, while "
            f"the id space is still open), or freeze it first.")

    prev = read_standard()
    doc = {
        "schema": SCHEMA_STANDARD,
        "build": build,
        "dictionary_fingerprint": ident["fingerprint"],
        "release": ident["release"],
        "status": ident["status"],
        "path": str(lm.relative_to(ROOT)).replace("\\", "/"),
        "entries": ident.get("entries"),
        "channels": ident["channels"],
        "promoted": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "promoted_by": by,
        "supersedes": (prev or {}).get("build"),
        "supersedes_fingerprint": (prev or {}).get("dictionary_fingerprint"),
        "ledger_green": led["green"],
        "notes": notes or ("ids are build-specific; bind by surface, verify by "
                           "fingerprint. Persisted ids MUST carry this fingerprint "
                           "beside them and verify tri-state on read."),
    }
    DIST.mkdir(parents=True, exist_ok=True)
    STANDARD_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    write_index()
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="ELO dictionary INDEX + STANDARD pointer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="regenerate INDEX.json")
    sub.add_parser("show", help="print the current standard")
    p = sub.add_parser("promote", help="write STANDARD.json for a build")
    p.add_argument("build")
    p.add_argument("--allow-staged", action="store_true")
    p.add_argument("--force", action="store_true", help="promote despite a non-green ledger")
    p.add_argument("--by", default=os.environ.get("USER") or "dictionary lane")
    p.add_argument("--notes", default="")
    a = ap.parse_args()

    if a.cmd == "index":
        doc = write_index()
        print(f"INDEX.json  {len(doc['builds'])} builds  -> {INDEX_PATH}")
        print(f"{'build':34}{'fingerprint':18}{'release':10}{'status':10}{'ledger':8}channels")
        for b in doc["builds"]:
            fp = (b["dictionary_fingerprint"] or "-")[:16]
            ch = len(b["channels"])
            star = " *" if b["build"] == doc["standard_build"] else "  "
            print(f"{star}{b['build']:32}{fp:18}{str(b['release'] or '-'):10}"
                  f"{str(b['status'] or '-'):10}{'green' if b['ledger_green'] else 'RED':8}{ch}")
        print("\n  * = current standard   (channels = sub-dbs present in the LMDB)")
        return 0

    if a.cmd == "show":
        cur = read_standard()
        if not cur:
            print("no STANDARD.json -- nothing is promoted. "
                  "run: python dictionary_standard.py promote <build>")
            return 1
        print(json.dumps(cur, indent=2))
        return 0

    try:
        doc = promote(a.build, allow_staged=a.allow_staged, force=a.force,
                      by=a.by, notes=a.notes)
    except PromotionRefused as e:
        print(f"REFUSED: {e}")
        return 2
    print(f"standard -> {doc['build']}  fp={doc['dictionary_fingerprint'][:16]}  "
          f"release={doc['release']}  status={doc['status']}")
    if doc["supersedes"]:
        print(f"  supersedes {doc['supersedes']} ({(doc['supersedes_fingerprint'] or '')[:16]})")
    print(f"  channels: {', '.join(doc['channels'])}")
    print(f"  written: {STANDARD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
