"""
vfacet_census.py -- channel coverage for a shipped browser bundle. Scope in, scope out.

WHY THIS EXISTS (2026-08-27). `assets.meta.json` reports `vfacets_entries: 437995`, which
was true for months while TWO of the five sub-channels held nothing at all: agency and
direction were 100% UNKNOWN because the enrichment substrate join had never been run
against the build. "Present" was the whole check. This makes "present and empty"
visible -- from the artifact that actually ships, not from the build log.

Reads only the bundle (vfacets.bin + vfacets.names.json + epa.bin). No lmdb, no build
tree, so it runs anywhere the bundle does. Decode tables come from vfacets.names.json --
never retyped here, per the same rule vfacets.rs follows.

DENOMINATORS. The dictionary is an id<->term relation and terms are not only words:
phrases, symbols, HTML, CSS, JavaScript, PHP. EPA and polarity are meaningless for a
Tailwind class. If `data/nonlexical_terms_<build>.txt` is passed via --nonlexical, a
second set of percentages is reported over the lexical remainder, so a coverage number
is never quietly divided by a denominator that could not have been covered.
(Measured on elo-browser-v04: the adjustment is ~0.1pt -- the gap is real, not an
artifact. The check is here so that stays checkable rather than remembered.)

    python vfacet_census.py <bundle-dir> [--nonlexical data/nonlexical_terms_<build>.txt]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import struct
from pathlib import Path

HEADER = struct.Struct("<8sII")
MAGIC_VFT = b"ELOVFT\x01\x00"
CHANNELS = ("agency", "direction", "temporal", "domain", "polarity")
BYTE0 = {"agency", "direction", "temporal"}


def _read_header(raw: bytes, magic: bytes) -> tuple[int, str, bytes]:
    m, count, fp_len = HEADER.unpack_from(raw, 0)
    if m != magic:
        raise SystemExit(f"bad magic {m!r} (expected {magic!r})")
    return count, raw[16:16 + fp_len].decode(), raw[16 + fp_len:]


def census(bundle: Path, nonlexical: Path | None = None) -> dict:
    names = json.loads((bundle / "vfacets.names.json").read_text(encoding="utf-8"))
    count, fp, body = _read_header((bundle / "vfacets.bin").read_bytes(), MAGIC_VFT)
    if len(body) != 2 * count:
        raise SystemExit(f"vfacets.bin stride {len(body) / count} != 2")
    if fp != names["dictionary_fingerprint"]:
        raise SystemExit("vfacets.bin and vfacets.names.json disagree on the fingerprint")

    nonlex: set[str] = set()
    if nonlexical and nonlexical.exists():
        nonlex = {ln.strip() for ln in nonlexical.read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")}

    lex_n: set[int] = set()
    if nonlex:
        bj = next(bundle.glob("*.browser.json"))
        lex_n = {e["n"] for e in json.loads(bj.read_text(encoding="utf-8"))["entries"]
                 if e["surface"] not in nonlex}

    hist = {c: collections.Counter() for c in CHANNELS}
    hist_lex = {c: collections.Counter() for c in CHANNELS}
    allzero = 0
    for n in range(count):
        b0, b1 = body[2 * n], body[2 * n + 1]
        if b0 == 0 and b1 == 0:
            allzero += 1
        for c in CHANNELS:
            code = ((b0 if c in BYTE0 else b1) & names["masks"][c]) >> names["shifts"][c]
            hist[c][code] += 1
            if n in lex_n:
                hist_lex[c][code] += 1

    epa_path = bundle / "epa.bin"
    rated = None
    if epa_path.exists():
        ecount, _, ebody = _read_header(epa_path.read_bytes(), b"ELOEPA\x01\x00")
        vals = struct.unpack(f"<{3 * ecount}f", ebody)
        rated = sum(1 for n in range(ecount) if not math.isnan(vals[3 * n]))

    return {"count": count, "fp": fp, "allzero": allzero, "rated": rated,
            "hist": hist, "hist_lex": hist_lex, "lex_total": len(lex_n), "names": names}


def report(r: dict) -> None:
    n, names = r["count"], r["names"]
    print(f"build fp {r['fp'][:16]}   entries {n:,}")
    print(f"  no vfacet at all   {r['allzero']:,} ({100 * r['allzero'] / n:.1f}%)")
    if r["rated"] is not None:
        print(f"  EPA rated (non-NaN) {r['rated']:,} ({100 * r['rated'] / n:.1f}%)")
    if r["lex_total"]:
        print(f"  lexical remainder  {r['lex_total']:,} "
              f"(non-lexical-only terms excluded from the second column)")
    print()
    for c in CHANNELS:
        unset = r["hist"][c][0]
        line = f"  {c:<10} populated {n - unset:>7,} ({100 * (n - unset) / n:5.1f}%)"
        if r["lex_total"]:
            lu, lt = r["hist_lex"][c][0], r["lex_total"]
            line += f"   lexical {lt - lu:>7,} ({100 * (lt - lu) / lt:5.1f}%)"
        print(line)
        named = {names[f"{c}_names"].get(str(k), k): v
                 for k, v in sorted(r["hist"][c].items())}
        print(f"             {named}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("bundle", type=Path, help="dictionary/<build>/ bundle directory")
    ap.add_argument("--nonlexical", type=Path, default=None,
                    help="data/nonlexical_terms_<build>.txt -- adds lexical-only percentages")
    a = ap.parse_args()
    report(census(a.bundle, a.nonlexical))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
