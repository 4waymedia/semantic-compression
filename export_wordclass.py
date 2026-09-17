"""Export the b'wordclass' channel to the bundle: wordclass.bin + wordclass.names.json.

WHY THIS EXISTS. `wordclass` was built into every dictionary from 2026-08-28, locked at
format v3 on 08-29, stamped in `meta`, measured by the census, adopted by the Verbalizer
-- and it had **no exporter**. 437,995 records, and the only way to read them was to open
the build's LMDB directly, which is exactly the coupling the bundle exists to remove.

It passed every publish gate by not being mentioned: `BUNDLE_CHANNELS`, `MAGIC`,
`artifact_identity.KINDS` and this directory's exporter were four separate hand-kept
lists, and the asset was in none of them. `asset_registry.py` is now the one list; this
script is the first asset added through it.

WIRE FORMAT -- identical in shape to epa/facets/vfacets, so a reader that handles one
handles this:

    magic        8 bytes   b"ELOWCL\\x01\\x00"
    count        u32 LE    vocab entries (n = 0 .. count-1)
    fp_len       u32 LE    64
    fingerprint  64 bytes  the dictionary fingerprint, ASCII hex
                           (that is an 80-byte header. `fp_len` is NOT the header
                           length -- this lane has misread that field twice.)
    records      count x 3 bytes, dense, indexed by n

ABSENT is b"\\x00\\x00\\x00": dominant UNKNOWN, empty class mask, all tri-states UNKNOWN.
Unlike vfacets there is no separate sentinel, because UNKNOWN is already representable in
every field -- that is the point of the v2 tri-state widening. A surface with no record
in the LMDB and a surface measured UNKNOWN are therefore indistinguishable in the .bin,
which is honest here: the builder writes a record for every entry, so the first case does
not occur. `wordclass_entries` in the meta names the count either way.

    python ELO-Browser/tools/export_wordclass.py --build <pkg> --out <bundle dir>
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import struct
import sys
from pathlib import Path

# Location-independent: works whether this file sits in semantic_compression/ (where it
# belongs, since 2026-09-10) or in a consumer's tools/ (where the exporters used to
# live). The `parents[2]` form silently resolved to the wrong root after the move.
_HERE = Path(__file__).resolve().parent
SC_DIR = _HERE if (_HERE / "asset_registry.py").exists() else \
    _HERE.parents[1] / "semantic_compression"
ROOT = SC_DIR.parent
sys.path.insert(0, str(SC_DIR))

import lmdb                                                        # noqa: E402
from asset_registry import BY_NAME                                 # noqa: E402
from wordclass_builder import (                                    # noqa: E402
    AMBIVALENT, CLASS, CLASS_MASK, CLASS_SHIFT, CONF, CONF_MASK, CONF_SHIFT,
    COUNTABILITY, MASK_BIT, NUMBER, PROPER_MASK, PROPER_SHIFT, REQDET_MASK,
    REQDET_SHIFT, TRI, WORDCLASS_FORMAT_VERSION, unpack_wordclass)

#: Derived, never typed -- see the note in export_browser_assets.py.
_GENERATED_BY = str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/")

ASSET = BY_NAME["wordclass"]
HEADER = struct.Struct("<8sII")
REC_WIDTH = ASSET.record_width          # 3 -- from the registry, not retyped
ABSENT = b"\x00\x00\x00"
COUNT_SHIFT, COUNT_MASK = 6, 0b11000000
NUMBER_SHIFT, NUMBER_MASK = 4, 0b00110000


def _header(count: int, fingerprint: str) -> bytes:
    fp = fingerprint.encode("ascii")
    if len(fp) != 64:
        raise SystemExit(f"fingerprint must be 64 hex chars, got {len(fp)}")
    return HEADER.pack(ASSET.wire_magic, count, 64) + fp


def build(pkg: Path, out: Path) -> int:
    vocab_path = next(pkg.glob("*.browser.json"), None)
    if vocab_path is None:
        raise SystemExit(f"no <build>.browser.json in {pkg} -- run stage 6 first")
    vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
    count = int(vocab["content_size"])
    fingerprint = vocab["dictionary_fingerprint"]

    env = lmdb.open(str(pkg / "dictionary.lmdb"), readonly=True, max_dbs=32, lock=False)
    try:
        fwd = env.open_db(b"forward", create=False)
        wc = env.open_db(b"wordclass", create=False)
    except lmdb.NotFoundError:
        raise SystemExit("this build has no b'wordclass' sub-db -- run stage 15 "
                         "(wordclass_builder.py) before exporting")

    arr = bytearray(ABSENT * count)
    written = n_any = n_classed = 0
    hist: dict[str, int] = {}
    # The vocab json is the ONE definition of n. Reading ids from it rather than from a
    # cursor order is what keeps every channel parallel to the same index -- G1.
    with env.begin() as t:
        for e in vocab["entries"]:
            n = e["n"]
            if n >= count:
                continue
            idb = t.get(e["surface"].encode("utf-8"), db=fwd)
            if idb is None:
                continue
            rec = t.get(bytes(idb), db=wc)
            if rec is None or len(rec) != REC_WIDTH:
                continue
            arr[n * REC_WIDTH:(n + 1) * REC_WIDTH] = rec
            written += 1
            if bytes(rec) != ABSENT:
                n_any += 1
            d = unpack_wordclass(bytes(rec))["dominant"]
            if d != "UNKNOWN":
                n_classed += 1
            hist[d] = hist.get(d, 0) + 1
    env.close()

    out.mkdir(parents=True, exist_ok=True)
    blob = _header(count, fingerprint) + bytes(arr)
    (out / ASSET.bin_file).write_bytes(blob)
    h_bin = hashlib.sha256(blob).hexdigest()[:16]
    print(f"  wrote {ASSET.bin_file:<24} {len(blob)/1e6:6.2f} MB  "
          f"{written:,}/{count:,} records  sha {h_bin}")

    # THE DECODE CONTRACT. `facets.bin` shipped with one and `vfacets.bin` did not, for
    # a month -- the data was hashed and the geometry to read it stayed behind. A channel
    # ships with its contract or it does not ship; publish now refuses on exactly this.
    names = {
        "version": WORDCLASS_FORMAT_VERSION,
        "dictionary_fingerprint": fingerprint,
        "count": count,
        "record_width": REC_WIDTH,
        "entries": written,
        "spec": "semantic_compression/docs/compression/spec-wordclass-db.md",
        "absent": "b'\\x00\\x00\\x00' -- dominant UNKNOWN, empty mask, tri-states UNKNOWN",
        # THREE NUMBERS, THREE QUESTIONS. Each measured, none inferred from another.
        #
        # MY BUG, caught by ELO-Browser 2026-09-12: `coverage_any_measurement` was set to
        # `written`, which counts records that EXIST -- and the builder writes one for
        # every entry, so it was always the vocabulary size. A field named
        # "any_measurement" reporting 437,995 while the publish gate measured 276,399 is
        # exactly the "default counted as a measurement" pattern this lane publishes as an
        # open concern about `temporal` and `direction`, in my own contract file.
        "records_written": written,            # a record exists (== count, by construction)
        "coverage_any_measurement": n_any,     # record != ABSENT: some field is set
        "coverage_classed": n_classed,         # dominant != UNKNOWN: narrower still
        # A FOURTH NUMBER (2026-09-17) -- offered by ELO-Browser, and it is the bug above
        # one field to the RIGHT. `coverage_classed` counts dominant != UNKNOWN. But OTHER
        # is 174,628, which is EXACTLY the `phrase` segment count in coverage_census.json:
        # OTHER is not a measured class, it is the phrase segment wearing a class name. So
        # 66.8% of `coverage_classed` is definitional, and a consumer sizing a WORD-level
        # gate by it overestimates by ~3x -- the same shape as r1's `coverage_any_measurement`
        # overstating by 1.6x. A set counted as measured that is partly by construction.
        "coverage_classed_words": n_classed - hist.get("OTHER", 0),
        "coverage_note": ("FOUR different sets, largest to smallest: records_written (a "
                          "row exists for every entry -- this is NOT coverage), "
                          "coverage_any_measurement (the row is not all-zero), "
                          "coverage_classed (dominant != UNKNOWN), coverage_classed_words "
                          "(also excludes OTHER). A record can carry a measured FEATURE "
                          "with no CLASS, which is the gap between the middle two. OTHER "
                          "is the PHRASE segment by construction, not a measured class, "
                          "which is the gap between the last two -- size word-level gates "
                          "with coverage_classed_words. Do not read the first as coverage."),
        "layout": {
            "byte0": "dominant class | confidence | ambivalent",
            "byte1": "class mask (composable; >1 bit set is what AMBIVALENT reports)",
            "byte2": "countability | inherent_number | proper | requires_determiner",
        },
        "shifts": {"dominant": CLASS_SHIFT, "confidence": CONF_SHIFT,
                   "countability": COUNT_SHIFT, "inherent_number": NUMBER_SHIFT,
                   "proper": PROPER_SHIFT, "requires_determiner": REQDET_SHIFT},
        "masks": {"dominant": CLASS_MASK, "confidence": CONF_MASK,
                  "ambivalent": AMBIVALENT, "countability": COUNT_MASK,
                  "inherent_number": NUMBER_MASK, "proper": PROPER_MASK,
                  "requires_determiner": REQDET_MASK},
        "class_names": {str(v): k for k, v in CLASS.items()},
        "confidence_names": {str(v): k for k, v in CONF.items()},
        "class_mask_bits": {str(v): k for k, v in MASK_BIT.items()},
        "countability_names": {str(v): k for k, v in COUNTABILITY.items()},
        "inherent_number_names": {str(v): k for k, v in NUMBER.items()},
        # THE TRAP, PUBLISHED. `proper == 1` means definitely NOT a name, and 1 is
        # truthy, so `if wc.proper` fires on the opposite of what it reads like. A
        # consumer inverted their own gate on exactly this. Any reader generated from
        # this file should emit predicates, not raw fields.
        "tristate_names": {str(v): k for k, v in TRI.items()},
        "tristate_warning": ("0=UNKNOWN 1=NO 2=YES. 1 is TRUTHY and means NO. "
                             "Compare to 2; never test truthiness."),
        "dominant_histogram": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
        "generated_by": _GENERATED_BY,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    txt = json.dumps(names, ensure_ascii=False, separators=(",", ":"))
    (out / ASSET.contract_file).write_bytes(txt.encode("utf-8"))
    print(f"  wrote {ASSET.contract_file:<24} "
          f"{hashlib.sha256(txt.encode()).hexdigest()[:16]}  "
          f"(format v{WORDCLASS_FORMAT_VERSION})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", type=Path, required=True,
                    help="build package dir (has dictionary.lmdb + <name>.browser.json)")
    ap.add_argument("--out", type=Path, required=True, help="bundle output dir")
    a = ap.parse_args()
    return build(a.build, a.out)


if __name__ == "__main__":
    raise SystemExit(main())
