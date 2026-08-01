"""
mine_phrases_from_corpus.py -- mine phrase candidates from an ARBITRARY corpus.

`phrase_miner.py` + `ngram_counter.py` read fixed paths under data/ that hold the
327M-token transcript corpus. That is correct for the shipping dictionary and wrong
for any build declaring a different `corpus:` -- and `build_from_spec` passed the
transcript phrase file regardless, so a books build came out 81,434 transcript
phrases against 1,819 book words, with 163,118 book words dropped.

This mines the same shape of artifact (the phrase_candidates.txt format the builder
consumes) from whatever files you point it at, so a corpus-fitted build is actually
fitted on both halves.

    python semantic_compression/mine_phrases_from_corpus.py \
        --glob "Resources/books/*.txt" \
        --exclude alice.txt frankenstein.txt \
        --out semantic_compression/data/phrase_candidates_books.txt

Scoring mirrors phrase_miner.py:
    PMI          = log2( freq * total^(n-1) / prod(word_counts) )
    savings_each = n*1.8 - 3            (v0.2 binary cost model)
    score        = savings_total * (1 + max(0, PMI)/10)
and applies the same maximal-phrase absorption filter: a phrase is dropped when a
longer phrase containing it occurs nearly as often, because promoting the parent
already covers the child.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "semantic_compression")
from semantic_compression.tokenizer import tokenize  # noqa: E402

NGRAM_MIN, NGRAM_MAX = 2, 6
ABSORB = 0.70


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--glob", required=True, help="corpus glob, e.g. 'Resources/books/*.txt'")
    ap.add_argument("--exclude", nargs="*", default=[], help="basenames to hold out")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--min-freq", type=int, default=8)
    ap.add_argument("--top", type=int, default=200_000)
    a = ap.parse_args()

    files = [p for p in sorted(Path().glob(a.glob)) if p.name not in set(a.exclude)]
    if not files:
        sys.exit(f"no files matched {a.glob!r} after exclusions")
    print(f"corpus: {len(files)} files ({sum(p.stat().st_size for p in files)/1e6:.1f} MB)")
    for p in files:
        print(f"    {p.stat().st_size:>10,}  {p.name}")

    # ---- count unigrams and n-grams over WORD tokens only -------------------
    uni: Counter[str] = Counter()
    ng: dict[int, Counter[tuple[str, ...]]] = {n: Counter() for n in range(NGRAM_MIN, NGRAM_MAX + 1)}
    for p in files:
        toks = [t for t in tokenize(p.read_text(encoding="utf-8", errors="replace"))
                if t.strip() and any(c.isalnum() for c in t)]
        uni.update(toks)
        for n in ng:
            for i in range(len(toks) - n + 1):
                ng[n][tuple(toks[i:i + n])] += 1
    total = sum(uni.values())
    print(f"tokens {total:,}   distinct {len(uni):,}")

    # ---- PMI + score --------------------------------------------------------
    recs = []
    for n, c in ng.items():
        each = n * 1.8 - 3.0
        if each <= 0:
            continue
        for words, freq in c.items():
            if freq < a.min_freq:
                continue
            denom = 1.0
            for w in words:
                denom *= uni[w] / total
            if denom <= 0:
                continue
            pmi = math.log2((freq / total) / denom)
            tot = freq * each
            recs.append({"n": n, "freq": freq, "pmi": pmi, "each": each,
                         "total": tot, "score": tot * (1 + max(0.0, pmi) / 10),
                         "phrase": " ".join(words)})
    print(f"candidates before absorption: {len(recs):,}")

    # ---- maximal-phrase absorption -----------------------------------------
    recs.sort(key=lambda r: -r["score"])
    by_phrase = {r["phrase"]: r for r in recs}
    drop = set()
    longer = sorted((r for r in recs if r["n"] > NGRAM_MIN), key=lambda r: -r["n"])
    for parent in longer:
        w = parent["phrase"].split()
        for n in range(NGRAM_MIN, parent["n"]):
            for i in range(len(w) - n + 1):
                child = " ".join(w[i:i + n])
                c = by_phrase.get(child)
                if c and parent["freq"] / c["freq"] >= ABSORB:
                    drop.add(child)
    kept = [r for r in recs if r["phrase"] not in drop][:a.top]
    print(f"absorbed (dropped): {len(drop):,}   kept: {len(kept):,}")

    # ---- emit in the builder's expected format ------------------------------
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("# phrase_candidates.txt  format_version=1\n")
        f.write("# rank\tn\tfreq\tpmi\tsavings_each\tsavings_total\tscore\tphrase\n")
        for i, r in enumerate(kept, 1):
            f.write(f"{i}\t{r['n']}\t{r['freq']}\t{r['pmi']:.2f}\t{r['each']:.2f}\t"
                    f"{int(r['total'])}\t{r['score']:.1f}\t{r['phrase']}\n")
    print(f"wrote {a.out}  ({len(kept):,} phrases)")
    print("top 10:")
    for r in kept[:10]:
        print(f"    {r['freq']:>8,}  pmi {r['pmi']:>6.2f}  {r['phrase']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
