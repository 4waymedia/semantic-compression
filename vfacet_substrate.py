"""
vfacet_substrate.py -- persistent, surface-keyed cache of ENRICHED vfacet fields.

THE DEBT THIS RETIRES (measured 2026-08-10): `agency` and `direction` are patched by
vfacet_llm.py, which is deliberately NOT a build stage (a stage needing an LLM cannot
reproduce offline). So every rebuild reset both fields to UNKNOWN and owed a full
re-enrichment -- O(vocab) LLM calls per rebuild, forever. Same pattern EPA solved with
epa_substrate.lmdb: make enrichment a SUBSTRATE, not a pass.

    write path   vfacet_llm.py classifies surface -> (agency, direction)
                 and WRITES THROUGH here (as well as into the build's vfacets).
    read path    vfacet_builder.py (stage 13) JOINS this substrate at build time,
                 deterministically: known surface -> cached verdict; unknown -> UNKNOWN.

So enrichment survives rebuilds; only NEW surfaces ever need the LLM. The join is
reproducible offline given the substrate file, whose version participates in the
build's vfacets_stats.json.

Layout (LMDB, subdir):
    b'enrich'    surface(utf-8) -> struct '<BB' (agency, direction)   2 bytes
    b'meta'      substrate_version (content hash), updated_at, count, format=1
    (named sub-db, NOT the unnamed main db: python-lmdb raises MDB_INCOMPATIBLE
    when arbitrary keys share the main db with named sub-db entries)

    python vfacet_substrate.py --stats
    python vfacet_substrate.py --import-tsv enriched.tsv   # surface<TAB>agency<TAB>direction
"""
from __future__ import annotations

import hashlib
import struct
import time
from pathlib import Path

import lmdb

SC = Path(__file__).resolve().parent
DEFAULT_SUBSTRATE = SC.parent / "Memory" / "data" / "vfacet_substrate.lmdb"
REC = struct.Struct("<BB")            # agency, direction
UNKNOWN = 0                           # both fields: 0 == UNKNOWN


def _env(path: Path, readonly: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    return lmdb.open(str(path), map_size=256 * 1024 * 1024, max_dbs=2,
                     readonly=readonly, lock=not readonly,
                     create=not readonly)


def load_all(path: Path = DEFAULT_SUBSTRATE) -> dict[str, tuple[int, int]]:
    """surface -> (agency, direction). Empty dict when the substrate doesn't exist
    yet -- absence degrades to 'no enrichment', never to an error."""
    if not Path(path).exists():
        return {}
    env = _env(Path(path), readonly=True)
    out: dict[str, tuple[int, int]] = {}
    try:
        main = env.open_db(b"enrich", create=False)
        with env.begin() as txn:
            for k, v in txn.cursor(db=main):
                if len(v) == REC.size:
                    a, d = REC.unpack(v)
                    out[k.decode("utf-8", "replace")] = (a, d)
    except lmdb.Error:
        return {}
    finally:
        env.close()
    return out


def substrate_version(path: Path = DEFAULT_SUBSTRATE) -> str | None:
    if not Path(path).exists():
        return None
    env = _env(Path(path), readonly=True)
    try:
        meta = env.open_db(b"meta", create=False)
        with env.begin() as txn:
            v = txn.get(b"substrate_version", db=meta)
        return v.decode() if v else None
    except Exception:
        return None
    finally:
        env.close()


def put_many(items: dict[str, tuple[int, int]],
             path: Path = DEFAULT_SUBSTRATE) -> dict:
    """Write-through: upsert surface -> (agency, direction). Only non-UNKNOWN
    verdicts are stored (an UNKNOWN cache entry is noise, not knowledge). The
    version is recomputed over the full sorted content, so two substrates with the
    same entries agree regardless of write order."""
    stored = skipped = 0
    env = _env(Path(path), readonly=False)
    try:
        main = env.open_db(b"enrich")
        meta = env.open_db(b"meta")
        with env.begin(write=True) as txn:
            for surface, (a, d) in items.items():
                if a == UNKNOWN and d == UNKNOWN:
                    skipped += 1
                    continue
                txn.put(surface.encode("utf-8"), REC.pack(a, d), db=main)
                stored += 1
        # version = hash over sorted (surface, a, d)
        h = hashlib.sha256()
        with env.begin() as txn:
            n = 0
            for k, v in txn.cursor(db=main):
                h.update(k); h.update(v); n += 1
        with env.begin(write=True) as txn:
            txn.put(b"substrate_version", h.hexdigest()[:16].encode(), db=meta)
            txn.put(b"updated_at", time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()).encode(), db=meta)
            txn.put(b"count", str(n).encode(), db=meta)
            txn.put(b"format", b"1", db=meta)
    finally:
        env.close()
    return {"stored": stored, "skipped_unknown": skipped}


def main() -> int:
    import argparse, json
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", type=Path, default=DEFAULT_SUBSTRATE)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--import-tsv", type=Path, default=None,
                    help="surface<TAB>agency<TAB>direction (ints per vfacet_builder encoding)")
    a = ap.parse_args()
    if a.import_tsv:
        items = {}
        for line in a.import_tsv.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            s, ag, di = line.split("\t")[:3]
            items[s] = (int(ag), int(di))
        print(json.dumps(put_many(items, a.db), indent=2))
        return 0
    data = load_all(a.db)
    print(f"vfacet_substrate {a.db}")
    print(f"  entries {len(data):,}   version {substrate_version(a.db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
