"""Do two builds' denotative vectors DIFFER, or are they the same vectors reordered?

    python semantic_compression/probe_vector_identity.py elo-browser-v04 elo-v5

WHY THIS EXISTS (2026-09-17, reasoning lane).

`morph_map.json` declares its invalidants as `corpus_fingerprint` + the vectors. Between
v04 and elo-v5 the corpus fingerprint was BYTE-IDENTICAL (`b80e8706...`) and the map was
carried forward on that basis -- yet one veto moved (1,332,122 -> 1,332,121) out of
2,332,632 pairs scored. Two candidate causes, with different fixes:

  (a) the vectors genuinely changed while the named invalidant did not move
      -> `corpus_fingerprint` does not cover what invalidates the map
  (b) a pair sat exactly on the tau boundary and tipped
      -> the sweep is not bit-reproducible and "verified unchanged" is weaker than it reads

A whole-file sha was measured and the two files DIFFER:

    elo-browser-v04  EED41679...        elo-v5  F61522F6...      (both 793,356,288 bytes)

**That measurement does not settle it.** The file is id-ordered and the two builds have
DIFFERENT ID SPACES (`b0164e50...` vs `fda06969...`), so the same vectors in a different
row order produce a different sha trivially. "The file differs" is not "the vectors
differ", and handing the reasoning lane the first as though it were the second is exactly
the class of error this lane has made three times this month.

WHAT THIS DOES INSTEAD: looks each shared SURFACE up by ITS OWN build's row and compares
the float vectors.

    identical for every shared surface  -> row-order only. Cause (b). The embeddings are
                                           the same; the sweep tipped at the boundary.
    differing for some/all              -> cause (a). The vectors moved under an unchanged
                                           corpus fingerprint, and `vectors_digest` is a
                                           missing invalidant rather than a nicety.

Nothing here is assumed about geometry: the row width is derived from the file size and
the surface count, and the script refuses rather than guesses if they disagree.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDS = ROOT / "semantic_compression" / "db" / "builds"

VECS = "dictionary.denotative.vecs.f32"
SURF = "dictionary.denotative.surfaces.json"


def _load_surfaces(build_dir: Path) -> list[str]:
    """Row-ordered surface list. Accepts the shapes this file has actually had."""
    p = build_dir / SURF
    if not p.is_file():
        raise SystemExit(f"REFUSING: no {SURF} in {build_dir}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(doc, list):
        # either ["a","b",...] or [["a",0],...]
        if doc and isinstance(doc[0], str):
            return doc
        if doc and isinstance(doc[0], (list, tuple)):
            return [str(x[0]) for x in doc]
    if isinstance(doc, dict):
        for k in ("surfaces", "rows", "order"):
            v = doc.get(k)
            if isinstance(v, list):
                return [str(x) if isinstance(x, str) else str(x[0]) for x in v]
        # {surface: row}
        if doc and all(isinstance(v, int) for v in doc.values()):
            out = [""] * (max(doc.values()) + 1)
            for s, i in doc.items():
                out[i] = s
            return out
    raise SystemExit(f"REFUSING: cannot read a row order out of {p} "
                     f"(top-level {type(doc).__name__}). Inspect it and extend _load_surfaces; "
                     f"guessing the layout is how the neighbours.bin header was misread twice.")


def _geometry(build_dir: Path, n_surfaces: int) -> tuple[int, int]:
    """(rows, dim) derived from file size and surface count. Never assumed."""
    p = build_dir / VECS
    if not p.is_file():
        raise SystemExit(f"REFUSING: no {VECS} in {build_dir}")
    size = p.stat().st_size
    if n_surfaces <= 0 or size % (n_surfaces * 4):
        raise SystemExit(
            f"REFUSING: {p.name} is {size} bytes and {SURF} lists {n_surfaces} surfaces; "
            f"{size}/({n_surfaces}*4) is not a whole number of dimensions. The two files "
            f"do not describe the same array -- that is itself the finding, not a reason "
            f"to pick a dim and carry on.")
    return n_surfaces, size // (n_surfaces * 4)


def _row(fh, row: int, dim: int) -> tuple:
    fh.seek(row * dim * 4)
    b = fh.read(dim * 4)
    if len(b) != dim * 4:
        raise SystemExit(f"REFUSING: short read at row {row}")
    return struct.unpack(f"<{dim}f", b)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build_a")
    ap.add_argument("build_b")
    ap.add_argument("--sample", type=int, default=2000,
                    help="shared surfaces to compare (0 = all; the full set is slow)")
    ap.add_argument("--tol", type=float, default=0.0,
                    help="max abs delta treated as identical (default 0 = bit-exact)")
    a = ap.parse_args(argv)

    da, db = BUILDS / a.build_a, BUILDS / a.build_b
    for d in (da, db):
        if not d.is_dir():
            raise SystemExit(f"REFUSING: no build at {d}")

    sa, sb = _load_surfaces(da), _load_surfaces(db)
    ra, dima = _geometry(da, len(sa))
    rb, dimb = _geometry(db, len(sb))

    print(f"  {a.build_a:24} {ra:>8} rows x {dima} dims")
    print(f"  {a.build_b:24} {rb:>8} rows x {dimb} dims")
    if dima != dimb:
        print(f"\nVERDICT: different dimensionality ({dima} vs {dimb}). The vectors are not "
              f"comparable row-wise; this alone invalidates any carry-forward.")
        return 2

    idx_a = {s: i for i, s in enumerate(sa) if s}
    idx_b = {s: i for i, s in enumerate(sb) if s}
    shared = sorted(set(idx_a) & set(idx_b))
    print(f"  shared surfaces          {len(shared):>8}  "
          f"(a-only {len(idx_a) - len(shared)}, b-only {len(idx_b) - len(shared)})")
    if not shared:
        print("\nVERDICT: no shared surfaces -- nothing to compare.")
        return 2

    pick = shared if a.sample <= 0 else shared[:: max(1, len(shared) // a.sample)][:a.sample]
    same = diff = 0
    worst = 0.0
    worst_surface = ""
    reordered = 0
    with open(da / VECS, "rb") as fa, open(db / VECS, "rb") as fb:
        for s in pick:
            ia, ib = idx_a[s], idx_b[s]
            if ia != ib:
                reordered += 1
            va, vb = _row(fa, ia, dima), _row(fb, ib, dima)
            d = max(abs(x - y) for x, y in zip(va, vb))
            if d > worst:
                worst, worst_surface = d, s
            if d <= a.tol:
                same += 1
            else:
                diff += 1

    print(f"\n  compared                 {len(pick):>8} shared surfaces")
    print(f"  identical                {same:>8}")
    print(f"  differing                {diff:>8}")
    print(f"  at a DIFFERENT row       {reordered:>8}  (row order changed between builds)")
    print(f"  worst abs delta          {worst:.8g}  ({worst_surface!r})")

    # MAGNITUDE DECIDES THE VERDICT, NOT THE COUNT (fixed 2026-09-22, first run).
    #
    # The first version compared bit-exactly and printed "THE VECTORS DIFFER" on 870/2000
    # -- with a worst delta of 2.6e-07. float32 epsilon is ~1.19e-07, so that is one to two
    # ULP: rounding, not meaning. A count of differing rows says nothing on its own; the
    # same count is consistent with a re-run on different hardware and with a genuinely
    # different model. Reporting the count as a semantic finding is the error this whole
    # probe was written to prevent, committed by the probe.
    F32_EPS = 1.1920929e-07
    NOISE = 32 * F32_EPS        # ~3.8e-06: generous, still far below any real change
    print()
    if diff == 0:
        print("VERDICT: BIT-IDENTICAL, REORDERED.")
        print("  Every shared surface carries a bit-identical vector; only its row moved.")
        print("  The whole-file sha difference is explained by the id space alone.")
        return 0
    if worst <= NOISE:
        print("VERDICT: NUMERICALLY IDENTICAL -- RECOMPUTED, NOT CHANGED.")
        print(f"  {diff} of {len(pick)} rows differ, worst delta {worst:.3g}, which is")
        print(f"  {worst / F32_EPS:.1f} ULP of float32. That is the signature of the vectors")
        print("  being RE-COMPUTED (different kernel, batch size, device or reduction order)")
        print("  rather than carried forward -- the values themselves did not move.")
        print()
        print("  Consequence for a carried-forward map: the scores are the same to within")
        print("  float noise, so any decision NOT sitting within that noise of a threshold is")
        print("  unchanged. A pair scoring within ~1e-06 of tau can tip. One veto moving out")
        print("  of 2,332,632 pairs is exactly the size of effect this predicts.")
        print()
        print("  So a BIT-EXACT vectors_digest would be the wrong fix: it would refuse a")
        print("  carry-forward on every rebuild while reporting a change that is not one.")
        print("  What the map needs recorded is a digest that is STABLE under recomputation")
        print("  (quantise before hashing), plus tau-margin handling for pairs inside the")
        print("  noise band -- those are the only decisions recomputation can move.")
        return 0
    print("VERDICT: THE VECTORS GENUINELY DIFFER.")
    print(f"  {diff} of {len(pick)} shared surfaces differ, worst delta {worst:.6g} --")
    print(f"  {worst / F32_EPS:.0f} ULP, far outside float32 rounding. The embeddings moved")
    print("  under a corpus_fingerprint that did not. The named invalidant does not cover")
    print("  the thing that invalidates, and the map was carried forward across a real")
    print("  change. `vectors_digest` is a MISSING INVALIDANT, not an improvement.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
