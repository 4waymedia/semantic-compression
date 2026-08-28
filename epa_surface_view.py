"""
epa_surface_view.py -- surface-keyed EPA view over a build's own id-keyed `epa` sub-DB.

WHY THIS EXISTS (measured 2026-08-27). Every dictionary asset -- epa, facets, vfacets,
neighbours, lexicals -- keys by ID. That is the house convention and it is correct.
`vfacet_builder._load_epa_lookup` is the outlier: it was written against
`Memory/data/epa_substrate.lmdb`, an EXTERNAL substrate keyed `b'en|surface'` (human EPA
ratings exist on terms, before ids do), and it treats any non-`en|` key as a surface.

So pointing --epa-db at a build's own dictionary does not fail. It loads all 236,645
entries, reports no error, and matches ~8,188 of them -- every one an id string that
happens to equal some OTHER word's surface. Measured cost on elo-browser-v04: polarity
non-NEUTRAL collapses 29,392 -> 3,332, each carrying the wrong word's affect.

  epa['XP']    -> (3.47, 2.21, 1.05)   # 'happy'    (id XP)      HIT
  epa['happy'] -> miss
  epa['car']   -> HIT, and it is NOT car's EPA.

THE PREFERRED FIX LANDED 2026-08-27: `vfacet_builder` now sniffs the key scheme
(`_epa_keys_are_surfaces`) and reads an id-keyed EPA source directly, with the surface
substrate as blend fallback -- the wrong-affect join described above can no longer
happen (non-en| keys route to the id path instead of being read as surfaces).
`build_assets.py` stage 13 passes the build's own LMDB. THIS SCRIPT IS THEREFORE
SUPERSEDED for the builder path; it remains only as a standalone surface-keyed view for
consumers that genuinely need one. It is a VIEW, not a substrate: regenerate it, never
edit it.

    python epa_surface_view.py --dict db/builds/<build>/dictionary.lmdb \
                               --out  db/builds/<build>/epa_surface.lmdb
"""
from __future__ import annotations

import argparse
from pathlib import Path

import lmdb


def build_view(dict_path: Path, out_path: Path) -> dict:
    src = lmdb.open(str(dict_path), readonly=True, lock=False, max_dbs=10, subdir=True)
    epa = src.open_db(b"epa", create=False)
    rev = src.open_db(b"reverse", create=False)
    out = lmdb.open(str(out_path), map_size=256 * 1024**2, max_dbs=4, subdir=True)
    odb = out.open_db(b"epa")

    written = orphan = 0
    try:
        with src.begin() as t, out.begin(write=True) as w:
            for k, v in t.cursor(db=epa):
                surface = t.get(k, db=rev)
                if surface is None:
                    orphan += 1
                    continue
                w.put(b"en|" + surface, v, db=odb)
                written += 1
    finally:
        out.close()
        src.close()
    return {"written": written, "orphan_ids": orphan}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dict", type=Path, required=True,
                    help="build dictionary.lmdb (required: defaulting to one is how the "
                         "wrong dictionary gets read)")
    ap.add_argument("--out", type=Path, required=True,
                    help="destination epa_surface.lmdb (a VIEW -- safe to delete and rebuild)")
    a = ap.parse_args()

    res = build_view(a.dict, a.out)
    print(f"epa_surface view: {res['written']:,} surface-keyed entries "
          f"({res['orphan_ids']:,} ids had no reverse)")
    if res["orphan_ids"]:
        print("  WARNING: orphan ids mean this build's `epa` and `reverse` disagree. "
              "Stop and look -- do not feed this view to the builder.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
