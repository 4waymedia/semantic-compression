"""
facet_gold_merge.py -- merge AI-proposed facet gold sets by CONSENSUS.

Votes across multiple sources (the AI gold sets + the seed) per surface, per
dimension. Agreement -> high-confidence consensus gold; disagreement -> a human
review queue. Implements the multi-system-consensus pattern (phrase-epa-strategy
Way 3) for gold labeling.

    python facet_gold_merge.py
"""
from __future__ import annotations

import collections
import csv
from pathlib import Path

COLS = ["surface","kind","bucket","utility","logic_cues","abstraction",
        "causality","temporality","scope","confidence","rationale"]
VOTE_DIMS = ["bucket","utility","abstraction","temporality","scope","logic_cues","causality","kind"]
KEY_DIMS  = ["bucket","utility","abstraction"]          # decide confidence/review
SET_DIMS  = {"logic_cues","causality"}

SOURCES = {
    "chatgpt":  "data/facet_gold_bucket/chatgpt_facet_gold_v2.tsv",
    "deepseek": "data/facet_gold_bucket/deepseek_tsv_20260623_53d4d7.tsv.txt",
    "gemini":   "data/facet_gold_bucket/gemini-code-1782172963022.tsv.txt",
    "grok":     "data/facet_gold_bucket/grok_facet_gold_v2.tsv",
    "seed":     "data/facet_gold.tsv",
}


def _norm_set(v: str) -> str:
    if not v or v == "-":
        return "-"
    parts = sorted(p.strip() for p in v.split(",") if p.strip() and p.strip() != "-")
    return ",".join(parts) if parts else "-"


def load(path: str) -> dict:
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.rstrip("\r")
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        f = ln.split("\t")
        if f[0].strip().lower() == "surface":     # header
            continue
        if len(f) < 9:
            continue
        f = (f + [""] * len(COLS))[:len(COLS)]
        rec = dict(zip(COLS, (x.strip() for x in f)))
        if not rec["surface"]:
            continue
        for d in SET_DIMS:
            rec[d] = _norm_set(rec[d])
        out[rec["surface"].lower()] = rec        # last wins within a source
    return out


def main():
    data = {name: load(path) for name, path in SOURCES.items()}
    for n, d in data.items():
        print(f"  {n:<9} {len(d):>4} rows")
    surfaces = sorted(set().union(*[set(d) for d in data.values()]))
    print(f"  union surfaces: {len(surfaces)}\n")

    consensus, review = [], []
    dim_agree = collections.Counter(); dim_total = collections.Counter()
    n_review = 0

    for s in surfaces:
        present = {n: d[s] for n, d in data.items() if s in d}
        nsrc = len(present)
        row = {"surface": present[next(iter(present))]["surface"]}
        # kind
        kinds = collections.Counter(r["kind"] for r in present.values())
        row["kind"] = kinds.most_common(1)[0][0]
        needs_review = nsrc == 1
        disagreements = []
        for dim in VOTE_DIMS:
            if dim == "kind":
                continue
            votes = collections.Counter(present[n].get(dim, "-") or "-" for n in present)
            top, topn = votes.most_common(1)[0]
            row[dim] = top
            dim_total[dim] += 1
            if len(votes) == 1:
                dim_agree[dim] += 1
            else:
                if dim in KEY_DIMS:
                    needs_review = True
                disagreements.append((dim, dict(votes)))
        # confidence from KEY-dim agreement
        key_unanimous = all(len(collections.Counter(present[n].get(d, "-") for n in present)) == 1 for d in KEY_DIMS)
        bucket_votes = collections.Counter(present[n]["bucket"] for n in present)
        bmaj = bucket_votes.most_common(1)[0][1] / nsrc
        row["confidence"] = "high" if (key_unanimous and nsrc >= 2) else ("med" if bmaj > 0.5 and nsrc >= 2 else "low")
        # rationale = agreement summary
        summ = " ".join(f"{d}={'/'.join(f'{k}x{v}' for k,v in collections.Counter(present[n].get(d,'-') for n in present).items())}"
                        for d in KEY_DIMS)
        row["rationale"] = f"src={nsrc} {summ}"
        consensus.append(row)
        if needs_review:
            n_review += 1
            for dim, votes in disagreements:
                if dim in KEY_DIMS or dim in ("logic_cues","causality"):
                    review.append({"surface": row["surface"], "dimension": dim,
                                   "consensus": row[dim], "n_sources": nsrc,
                                   **{n: present[n].get(dim, "") for n in SOURCES if n in present}})
            if nsrc == 1:
                review.append({"surface": row["surface"], "dimension": "SINGLE_SOURCE",
                               "consensus": "", "n_sources": 1,
                               **{n: ("✓" if n in present else "") for n in SOURCES}})

    # write consensus (drop-in gold schema)
    cpath = Path("data/facet_gold_consensus.tsv")
    with open(cpath, "w", encoding="utf-8") as f:
        f.write("# facet gold CONSENSUS — merged from " + ",".join(SOURCES) + " ; review the *_review.tsv before release use\n")
        f.write("\t".join(COLS) + "\n")
        for r in consensus:
            f.write("\t".join(str(r.get(c, "-")) for c in COLS) + "\n")
    # write review queue
    rcols = ["surface","dimension","consensus","n_sources"] + list(SOURCES)
    rpath = Path("data/facet_gold_review.tsv")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write("\t".join(rcols) + "\n")
        for r in review:
            f.write("\t".join(str(r.get(c, "")) for c in rcols) + "\n")

    print("=== per-dimension agreement (unanimous / total surfaces) ===")
    for d in KEY_DIMS + ["logic_cues","causality","temporality","scope"]:
        t = dim_total[d]; a = dim_agree[d]
        print(f"  {d:<12} {a}/{t}  ({100*a/t:.0f}% unanimous)" if t else f"  {d}: -")
    print(f"\nconsensus rows: {len(consensus)}   need review: {n_review}   review lines: {len(review)}")
    print(f"-> {cpath}\n-> {rpath}")


if __name__ == "__main__":
    main()
