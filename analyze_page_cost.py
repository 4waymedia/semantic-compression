"""
analyze_page_cost.py -- where do the characters actually go on one document?

A compression ratio is a single number that hides its own cause. This prints the
per-token economics for a real file against a real build, so a bad ratio can be
attributed rather than guessed at.

    python semantic_compression/analyze_page_cost.py <file> --db db/builds/<name>/dictionary.lmdb

THE MODEL. A text stream costs `len(id) + 1` per token: the id, plus one delimiter.
The source costs `len(surface)`. So:

    net = len(surface) - (len(id) + 1)

and a token only pays for itself when the surface is LONGER than its id. A 1-char
token costs 2 to represent 1 — it expands, no matter how common it is or how well
the dictionary covers it.

WHY THIS TOOL EXISTS (measured 2026-07-30). A Google results page compressed at 0.75x
with 98% coverage, which looks contradictory until you separate the two questions:

    coverage  = "did we find an id?"      -> 98%, excellent
    economics = "did the id save a byte?" -> no, for most of the page

Coverage is the metric we had, so it was the metric we reasoned with. It cannot see
this failure, because a short token that resolves perfectly still expands the output.
Machine-generated markup is dominated by short tokens; prose is not. Same dictionary,
opposite results, and the coverage number is high in both cases.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "semantic_compression")

import lmdb  # noqa: E402

from semantic_compression.tokenizer import tokenize  # noqa: E402

DELIM_COST = 1          # one delimiter per token in the text stream
OOV_TEMPLATE = "OOV:A:"  # 6 chars + the character itself


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("file", type=Path)
    ap.add_argument("--db", required=True, help="build package dictionary.lmdb")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    text = a.file.read_text(encoding="utf-8", errors="replace")
    toks = list(tokenize(text))

    env = lmdb.open(str(a.db), readonly=True, max_dbs=4, lock=False)
    fwd = env.open_db(b"forward")

    src = stream = 0
    n_oov = 0
    buckets = Counter()          # net-effect class -> token count
    bucket_chars = Counter()     # net-effect class -> net chars
    by_token = Counter()         # surface -> net chars (negative = costs)
    len_hist = Counter()

    with env.begin() as txn:
        for t in toks:
            tb = t.encode("utf-8")
            tid = txn.get(tb, db=fwd)
            src += len(t)
            len_hist[len(t)] += 1
            if tid is None:
                cost = len(OOV_TEMPLATE) + len(t) + DELIM_COST
                n_oov += 1
                cls = "OOV"
            else:
                cost = len(tid.decode()) + DELIM_COST
                net = len(t) - cost
                cls = "expands" if net < 0 else ("neutral" if net == 0 else "saves")
            stream += cost
            net = len(t) - cost
            buckets[cls] += 1
            bucket_chars[cls] += net
            by_token[t] += net

    env.close()

    ratio = src / stream if stream else float("inf")
    cov = 100 * (len(toks) - n_oov) / len(toks) if toks else 0.0

    print(f"file        {a.file}")
    print(f"dictionary  {a.db}")
    print(f"tokens      {len(toks):,}      coverage {cov:.1f}%   (OOV {n_oov:,})")
    print(f"source      {src:,} chars")
    print(f"stream      {stream:,} chars")
    print(f"RATIO       {ratio:.2f}x        {'EXPANDS' if ratio < 1 else 'compresses'}")
    print()
    print("  class      tokens        net chars      (net<0 = this class costs you)")
    for k in ("saves", "neutral", "expands", "OOV"):
        if buckets[k]:
            print(f"  {k:<9} {buckets[k]:>8,}   {bucket_chars[k]:>+14,}")
    print()

    # THE DIAGNOSIS. Mean token length is the single number that predicts the ratio,
    # because the delimiter is charged per token regardless of how long the token is.
    mean_len = src / len(toks) if toks else 0
    print(f"  mean token length: {mean_len:.2f} chars")
    print("  token-length histogram (len: count, share of all tokens):")
    for L in sorted(len_hist)[:8]:
        share = 100 * len_hist[L] / len(toks)
        flag = "  <- expands at every tier" if L <= 2 else ""
        print(f"    {L:>2}: {len_hist[L]:>8,}  {share:5.1f}%{flag}")
    print()
    print(f"  Worst {a.top} tokens by total characters added:")
    for tok, net in sorted(by_token.items(), key=lambda kv: kv[1])[:a.top]:
        if net >= 0:
            break
        print(f"    {tok!r:<16} {net:>+10,} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
