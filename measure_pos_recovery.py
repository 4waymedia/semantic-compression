#!/usr/bin/env python3
"""
measure_pos_recovery.py -- how much of the wordclass all-zero hole a POS source recovers.

Measurement-first (the vfacets lesson): before adding a POS evidence layer to
wordclass_builder, measure what it would actually recover. This reads a build's
`wordclass` sub-DB, collects the single-word ALL-ZERO surfaces (dominant=UNKNOWN,
no class), tags them with spaCy, and reports the recovery % = the true new ceiling.

Run on a machine with spaCy + en_core_web_sm installed (the build machine):
    uv run python semantic_compression/measure_pos_recovery.py \
        semantic_compression/db/builds/elo-browser-v04/dictionary.lmdb
Optional: --sample 2000  (default: all).  Not needed at build time; a dev measurement.
"""
from __future__ import annotations
import argparse
import random
import sys
from collections import Counter
from pathlib import Path

import lmdb

USABLE = {"NOUN", "VERB", "ADJ", "ADV", "PROPN", "NUM"}


def all_zero_singles(lmdb_path: Path) -> list[str]:
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=16, lock=False)
    fwd = env.open_db(b"forward", create=False)
    wc = env.open_db(b"wordclass", create=False)
    id2s: dict[bytes, str] = {}
    with env.begin(db=fwd) as t:
        for s, i in t.cursor():
            id2s[bytes(i)] = s.decode("utf-8", "replace")
    out = []
    with env.begin(db=wc) as t:
        for i, rec in t.cursor():
            if not any(rec):                       # all-zero == UNKNOWN, no class
                s = id2s.get(bytes(i))
                if s and " " not in s and s.replace("-", "").replace("'", "").isalpha():
                    out.append(s)
    env.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lmdb", type=Path)
    ap.add_argument("--sample", type=int, default=0, help="0 = all")
    a = ap.parse_args()

    words = all_zero_singles(a.lmdb)
    if a.sample and a.sample < len(words):
        random.seed(1)
        words = random.sample(words, a.sample)
    if not words:
        print("no all-zero single-word entries found (wrong build? no wordclass sub-DB?)")
        return 2

    try:
        import spacy
        nlp = spacy.load("en_core_web_sm", disable=["ner", "parser", "lemmatizer"])
    except Exception as e:
        print(f"spaCy unavailable ({e}). Install spacy + en_core_web_sm and re-run.", file=sys.stderr)
        return 3

    pos = Counter()
    for w in words:
        d = nlp(w)
        pos[d[0].pos_ if len(d) else "EMPTY"] += 1
    usable = sum(v for k, v in pos.items() if k in USABLE)
    n = len(words)
    print(f"all-zero single-word alpha surfaces measured: {n}")
    print("spaCy POS distribution:", dict(pos.most_common()))
    print(f"RECOVERABLE (NOUN/VERB/ADJ/ADV/PROPN/NUM): {usable}/{n} = {usable*100.0/n:.1f}%")
    print("-> that % of the 162,762 hole is what a spaCy-POS layer could close;")
    print("   add it to today's ~38% single-word coverage for the projected new ceiling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
