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

# THE registry -- what an asset is, and which ones ship. Imported rather than restated so
# `bundle_channels` here and in publish_dictionary cannot drift apart.
from asset_registry import bundle_channels           # noqa: E402

SC = Path(__file__).resolve().parent            # semantic_compression/
ROOT = SC.parent                                # R-D-concepts/
BUILDS = SC / "db" / "builds"
DIST = ROOT / "dist" / "dictionary"
INDEX_PATH = DIST / "INDEX.json"
STANDARD_PATH = DIST / "STANDARD.json"

SCHEMA_INDEX = "elo-dictionary-index/1"
SCHEMA_STANDARD = "elo-dictionary-standard/2"   # /2 adds the derived block (§3)

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



def _package_revision(build_dir: Path) -> int:
    """The build's asset-set revision, read from its manifest.

    Read, never restated. The build's `manifest.json` is where `--add-asset` and
    `--bump-revision` record it; copying the number into a second place by hand is the
    defect this repo has spent a fortnight removing."""
    mp = build_dir / "manifest.json"
    if not mp.exists():
        return 1
    try:
        return int(json.loads(mp.read_text(encoding="utf-8")).get("package_revision", 1))
    except Exception:
        return 1


def _derived(lmdb_path: Path, build_dir: Path) -> dict:
    """Fields DERIVED from the artifact, computed once at PROMOTION time (§3).

    A consumer should not have to open a 20 MB LMDB to learn how many entries a channel
    has, or which sidecars a package carries. Everything here is read FROM the artifact
    and frozen into the pointer, so `STANDARD.json` answers those questions on its own
    -- which is what lets the derivation leave the read path entirely.

    `builds_root` is recorded for the same reason: resolving a bare build NAME
    (`ELO_DICT=elo-browser-v04`) otherwise needs a hardcoded
    `semantic_compression/db/builds` inside the resolver -- a derived location baked
    into code instead of stated in data.

    Every value is measured. Nothing here is typed by hand."""
    import hashlib
    out: dict = {"channel_entries": {}, "sidecars": {}}
    try:
        env = lmdb.open(str(lmdb_path), readonly=True, lock=False, max_dbs=24)
    except lmdb.Error as e:
        out["derived_error"] = str(e)[:120]
        return out
    try:
        main = env.open_db()
        with env.begin() as t:
            names = sorted(bytes(k).decode("utf-8", "replace") for k, _ in t.cursor(db=main))
        handles = {n: env.open_db(n.encode(), create=False) for n in names}
        with env.begin() as t:
            for n in names:
                out["channel_entries"][n] = t.stat(db=handles[n])["entries"]
    except Exception as e:
        out["derived_error"] = str(e)[:120]
    finally:
        env.close()

    for f in sorted(build_dir.glob("*")):
        if f.is_file() and f.suffix in (".json", ".db", ".gz", ".csv"):
            try:
                out["sidecars"][f.name] = {
                    "bytes": f.stat().st_size,
                    "sha256_16": hashlib.sha256(f.read_bytes()).hexdigest()[:16],
                }
            except OSError:
                continue
    try:
        out["lmdb_bytes"] = sum(x.stat().st_size for x in lmdb_path.glob("*") if x.is_file())
    except OSError:
        pass
    out["builds_root"] = str(BUILDS.relative_to(ROOT)).replace("\\", "/")
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


def _published_bundle(bundle_id: str) -> dict:
    """Read the PUBLISHED bundle's own manifest, for the fields only it knows.

    2026-09-16, reported by integration: STANDARD.json carried no `bundle_fingerprint`,
    while the re-pin broadcast published one in the very block consumers are told to pin
    from -- and the broadcast also says STANDARD.json wins on disagreement. So the one
    field identifying the asset set a consumer was told to re-pin to was the one field
    they could not check against the document that wins. The precedence rule had a hole
    exactly the width of the thing being pinned.

    Read rather than recomputed: the bundle fingerprint is whatever `publish_dictionary`
    sealed into BUNDLE.json. Recomputing it here would create a second place holding one
    fact -- the defect this lane has now found five times."""
    p = DIST / bundle_id / "BUNDLE.json"
    if not p.exists():
        return {"bundle_fingerprint": None, "bundle_published_utc": None,
                "bundle_path": None,
                "bundle_note": (f"no published bundle at dist/dictionary/{bundle_id}/ -- "
                                f"the build is promoted but not published, so consumers "
                                f"have nothing to read. Run publish_dictionary.")}
    try:
        b = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        return {"bundle_fingerprint": None, "bundle_published_utc": None,
                "bundle_path": None,
                "bundle_note": f"BUNDLE.json at {p} is unreadable: {type(e).__name__}"}
    return {
        "bundle_fingerprint": b.get("bundle_fingerprint"),
        "bundle_published_utc": b.get("published_utc"),
        "bundle_path": str(p.parent.relative_to(ROOT)).replace("\\", "/"),
        "bundle_note": ("`bundle_fingerprint` identifies the ASSET SET and moves with every "
                        "content revision. `dictionary_fingerprint` identifies the ID SPACE "
                        "and does not. Pin `bundle_id`; verify with both."),
    }


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
    _rev = _package_revision(d)
    _bid = build if _rev <= 1 else f"{build}r{_rev}"
    _pub = _published_bundle(_bid)
    doc = {
        "schema": SCHEMA_STANDARD,
        "build": build,
        "dictionary_fingerprint": ident["fingerprint"],
        # THE ASSET-SET IDENTITY (2026-09-10). Without it the standard cannot express
        # the difference between two packages of the same build:
        # `dictionary_fingerprint` is the ID SPACE and is IDENTICAL across revisions by
        # design -- that is what keeps stored `.elo` files readable -- so a consumer
        # resolving through STANDARD.json had no way to tell v04 r1 (facets wrong on
        # 14,394 surfaces, no wordclass) from v04 r2. `bundle_id` is the string to pin.
        "package_revision": _rev,
        "bundle_id": _bid,
        # The published bundle's own identity, read from BUNDLE.json. See _published_bundle.
        **_pub,
        # TWO CHANNEL SETS, NAMED SEPARATELY (2026-09-14).
        #
        # `channels` above enumerates the LMDB's sub-dbs, which is correct for `path` --
        # it describes what that file carries. But it silently EXCLUDES every asset that
        # has no sub-db: `morph` (keyed on surface pairs) and `neighbours` (a CSR file).
        # So STANDARD.json listed seven channels while BUNDLE.json listed six DIFFERENT
        # ones, and a consumer reading the standard would conclude the dictionary has no
        # morph -- on the very build that publishes it for the first time.
        #
        # Neither list was wrong; they answer different questions and shared a name. Both
        # are now derived, both are named for what they describe.
        "bundle_channels": list(bundle_channels()),
        "channels_note": ("`channels` = sub-dbs in the LMDB at `path`. "
                          "`bundle_channels` = what the PUBLISHED bundle ships, which "
                          "includes assets with no sub-db (morph, neighbours). A "
                          "consumer reads the bundle."),
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
        # §3 -- derived at promotion, not at read time. See _derived().
        **_derived(lm, d),
        # 2026-09-16: integration read `sidecars` as "consumers can fetch and verify these
        # without asking". They are declared and sha-bound, but they live in the BUILD
        # directory, which is gitignored and not distributed -- a consumer holding only the
        # published bundle has none of them. Declared != shipped, and the document said
        # nothing either way.
        "sidecars_note": ("sidecars are files in the BUILD directory "
                          "(`builds_root`/`build`), which is NOT distributed. They are "
                          "declared and sha-bound so a holder of the build can verify "
                          "them; a consumer who has only the published bundle at "
                          "`bundle_path` does not have them. `coverage_census.json` in "
                          "particular is a measurement sidecar, not a bundle payload."),
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
