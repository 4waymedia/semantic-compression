#!/usr/bin/env python
"""gen_vectors_oracle.py -- the CODEC oracle, regenerated per build.

    python tools/gen_vectors_oracle.py --build elo-browser-v04
    python tools/gen_vectors_oracle.py --build elo-browser-v04 --check   # drift, exit 1

WHAT THIS IS. `epa.json`, `facets.json` and `vfacets.json` are regenerated for every
build and describe the CHANNELS. Nothing did the same for the CODEC: the only vectors
oracle in the tree was `elo-browser-v01.vectors.json`, generated once in June, pinned by
three tests in `elo.rs`, and never regenerated. So encode/decode was verified against a
dictionary nobody ships while v04 went live.

WHY PYTHON GENERATES IT. `compressor.py` is the reference implementation; `elo.rs` is a
port. The oracle must come from the reference or it proves nothing -- a port that
generates its own target is a description of what it already does, not a test of it.
This is the same reason the browser lane declined to author
`participant_conformance.json`.

WHAT IT CATCHES, AND WHAT IT DOES NOT. It catches the Rust port drifting from Python --
the cross-runtime risk, and the one that produced `morph_map` and the 13-vs-15 cue bug.
It does NOT catch Python drifting from itself; that is what Python's own byte-exact
round-trip suite over the 13-file set is for (benchmark-v0.3.md).

THE TEXTS ARE SYNTHETIC AND FIXED, on purpose. They exercise the codec's branches --
caps, OOV, phrases, punctuation, whitespace, unicode -- and they are literals in this
file, so the oracle is reviewable, reproducible on any machine, and carries no corpus
dependency.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_root(p: Path) -> Path:
    for q in [p] + list(p.parents):
        if (q / "semantic_compression").is_dir() and (q / "ELO-Browser").is_dir():
            return q
    raise SystemExit("cannot locate repo root")


ROOT = _find_root(Path(__file__).resolve())
# compressor.py imports `semantic_compression.caps_codec`, so the REPO ROOT goes on the
# path -- not the package dir. Putting the dir on the path makes the module importable
# and its own imports fail, which is a confusing way to discover the difference.
sys.path.insert(0, str(ROOT))

# One vector per codec branch. Keep additions APPEND-ONLY: an oracle whose input set
# changes between builds cannot distinguish "the codec changed" from "the test changed".
VECTORS: list[str] = [
    "",                                   # empty -- header only
    "the",                                # single tier-0 word
    "the car",                            # two words, implicit space
    "The Car",                            # caps codec, both words
    "THE CAR",                            # all-caps
    "tHe",                                # irregular caps -- bitmask, not a flag
    "zzqqxx",                             # OOV, lowercase
    "Zzqqxx",                             # OOV + caps
    "it is what it is",                   # repeated tokens
    "don't",                              # interior joiner: ' between letters
    "3.14",                               # interior joiner: . between digits
    "a-b",                                # '-' never joins
    "hello, world.",                      # punctuation tokens
    "  leading and trailing  ",           # whitespace runs
    "line\nbreak",                        # newline
    "tab\there",                          # tab
    "café",                          # non-ascii
    " nbsp",                         # the surface that moved between v01c and v04
    "one two three four five six seven",  # longer run, phrase-scan territory
]


def build_oracle(build_dir: Path) -> dict:
    from semantic_compression.compressor import Compressor  # noqa: E402

    c = Compressor(lmdb_path=build_dir / "dictionary.lmdb").open()
    build_id = getattr(c, "_build_id", "") or build_dir.name
    dict_fp = getattr(c, "_dict_fp", "") or ""

    vectors = []
    for text in VECTORS:
        elo_text = c.encode_text(text, ".txt")
        elo_bin = c.encode_bytes_binary(text.encode("utf-8"), ".txt")
        # ROUND TRIP IS PART OF THE ORACLE, not a separate check. A vector that does not
        # survive its own codec must never be published as a target for the port.
        # decode_bytes_binary returns (SOURCE_EXT, SOURCE_BYTES) -- not (text, bytes).
        # Read the docstring, do not infer the shape: the first element is "txt".
        _ext, raw = c.decode_bytes_binary(elo_bin)
        rt = raw.decode("utf-8") == text
        vectors.append({
            "text": text,
            "elo_text": elo_text,
            "elo_bin_hex": elo_bin.hex(),
            "bin_bytes": len(elo_bin),
            "roundtrip": rt,
        })

    bad = [v["text"] for v in vectors if v["roundtrip"] is False]
    if bad:
        raise SystemExit(f"REFUSING to write: {len(bad)} vector(s) do not round-trip: {bad[:3]}")

    return {
        "_comment": "CODEC oracle for one build. Generated by the Python reference "
                    "(compressor.py); replayed by the Rust port (elo.rs). Regenerate on "
                    "every published build -- see gen_vectors_oracle.py.",
        "build": build_id,
        "dictionary_fingerprint": dict_fp,
        "elo_bin_version": vectors[0]["elo_bin_hex"][6:8] if vectors[0]["elo_bin_hex"] else "",
        "vector_count": len(vectors),
        "all_roundtrip_byte_exact": all(v["roundtrip"] for v in vectors),
        "vectors": vectors,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", required=True,
                    help="build name under semantic_compression/db/builds, or a path")
    ap.add_argument("--out-root", type=Path,
                    default=ROOT / "ELO-Browser" / "poc" / "conformance")
    ap.add_argument("--check", action="store_true",
                    help="compare against the file on disk; exit 1 on drift, write nothing")
    a = ap.parse_args(argv)

    bd = Path(a.build)
    if not bd.is_dir():
        bd = ROOT / "semantic_compression" / "db" / "builds" / a.build
    if not bd.is_dir():
        raise SystemExit(f"no such build: {bd}")

    oracle = build_oracle(bd)
    dst = a.out_root / bd.name / "vectors.json"

    print(f"  build        {oracle['build']}")
    print(f"  fingerprint  {oracle['dictionary_fingerprint'][:32] or '(unstamped)'}")
    print(f"  container    ELO_BIN_VERSION = 0x{oracle['elo_bin_version']}")
    print(f"  vectors      {oracle['vector_count']}  all round-trip: "
          f"{oracle['all_roundtrip_byte_exact']}")

    if a.check:
        if not dst.is_file():
            print(f"  MISSING      {dst}")
            return 1
        cur = json.loads(dst.read_text(encoding="utf-8"))
        same = cur.get("vectors") == oracle["vectors"] and \
            cur.get("dictionary_fingerprint") == oracle["dictionary_fingerprint"]
        print(f"  {'no drift' if same else 'DRIFT -- regenerate'}")
        return 0 if same else 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(oracle, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  wrote        {dst.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
