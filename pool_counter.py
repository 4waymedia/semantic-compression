"""
pool_counter.py -- Multi-source vocabulary counting (C2).

Consumes SourceRecord streams from source_adapters, tokenizes each record with
the canonical tokenizer, and keeps PER-SOURCE surface counts. Produces:

  - per-(surface, source) counts            (provenance)
  - merged weighted total  = Σ_source weight_source × count_source
  - dispersion(surface)    = # distinct sources containing it   (harness S4)

A single-source run (youtube, weight 1.0) reproduces word_frequency_counter's
word_frequencies.txt byte-for-byte (it reuses the same tokenize + normalize +
writer), so this is a strict superset of today's counter.

Design: docs/compression/spec-corpus-sourcing.md (§5 pooling, §6 dispersion).

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.pool_counter --source youtube:1.0 --limit 50
    python -m semantic_compression.pool_counter --source youtube:1.0 --source wikipedia:0.5
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, ".")

from semantic_compression.source_adapters import ADAPTERS, iter_records
from semantic_compression.tokenizer import tokenize
from semantic_compression.word_frequency_counter import (
    encode_frequency_token, normalize_dictionary_key, write_frequencies,
)

# Canonical builder input (overwrite ONLY with explicit --canonical).
WORD_FREQ_CANONICAL = Path("semantic_compression/data/word_frequencies.txt")
# Safe dev default — never clobbers the canonical input.
WORD_FREQ_OUT = Path("semantic_compression/data/word_frequencies_pooled.txt")
SOURCES_OUT = Path("semantic_compression/data/word_frequencies_sources.tsv")


@dataclass
class SourceSpec:
    """One source in the pool: which adapter, its weight, and adapter kwargs."""
    source_id: str
    weight: float = 1.0
    kwargs: dict = field(default_factory=dict)


class PoolCounts:
    """Per-(surface, source) counts + weighted merge + dispersion."""

    def __init__(self, weights: dict[str, float]):
        self.weights = dict(weights)
        # surface -> {source_id: raw_count}
        self.per_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def add(self, surface: str, source_id: str, n: int = 1) -> None:
        self.per_source[surface][source_id] += n

    def dispersion(self, surface: str) -> int:
        return len(self.per_source.get(surface, ()))

    def merged_count(self, surface: str) -> int:
        """Weighted merged total (int, for the builder)."""
        bysrc = self.per_source.get(surface, {})
        total = sum(self.weights.get(src, 1.0) * c for src, c in bysrc.items())
        return int(round(total))

    def merged(self) -> dict[str, int]:
        """surface -> merged weighted count (drops any that round to 0)."""
        out = {}
        for surface in self.per_source:
            m = self.merged_count(surface)
            if m > 0:
                out[surface] = m
        return out

    # --- writers ------------------------------------------------------------

    def write_word_frequencies(self, path: Path = WORD_FREQ_OUT) -> None:
        """Back-compatible word_frequencies.txt (merged weighted count).

        Reuses word_frequency_counter.write_frequencies → byte-identical format.
        """
        from collections import Counter
        write_frequencies(Counter(self.merged()), path)

    def write_sources(self, path: Path = SOURCES_OUT) -> None:
        """Rich per-source file for the harness: token, total, dispersion, per-source."""
        path.parent.mkdir(parents=True, exist_ok=True)
        sources = sorted(self.weights)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("# token\ttotal\tdispersion\t" + "\t".join(sources) + "\n")
            # sort by merged count desc, then token for determinism
            for surface in sorted(self.per_source,
                                  key=lambda s: (-self.merged_count(s), s)):
                bysrc = self.per_source[surface]
                cols = "\t".join(str(bysrc.get(s, 0)) for s in sources)
                f.write(f"{encode_frequency_token(surface)}\t"
                        f"{self.merged_count(surface)}\t{self.dispersion(surface)}\t{cols}\n")


def count_sources(specs: list[SourceSpec], show: bool = True) -> PoolCounts:
    pool = PoolCounts({s.source_id: s.weight for s in specs})
    for spec in specs:
        n_rec = 0
        for rec in iter_records(spec.source_id, **spec.kwargs):
            for tok in tokenize(rec.text):
                pool.add(normalize_dictionary_key(tok), spec.source_id)
            n_rec += 1
        if show:
            print(f"  [{spec.source_id}] weight={spec.weight}  records={n_rec:,}")
    return pool


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_source(arg: str) -> SourceSpec:
    # "youtube" or "youtube:0.5"
    if ":" in arg:
        sid, w = arg.split(":", 1)
        return SourceSpec(sid, float(w))
    return SourceSpec(arg, 1.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Pool multi-source vocabulary counts")
    p.add_argument("--source", action="append", default=None,
                   help="source_id[:weight]; repeatable (default youtube:1.0)")
    p.add_argument("--limit", type=int, default=None,
                   help="per-source doc limit (passed to each adapter)")
    p.add_argument("--out", default=None,
                   help=f"output path (default {WORD_FREQ_OUT}, a dev file)")
    p.add_argument("--canonical", action="store_true",
                   help=f"write the canonical builder input ({WORD_FREQ_CANONICAL}) "
                        f"— use only for a real full build")
    p.add_argument("--no-sources-file", action="store_true")
    args = p.parse_args()

    specs = [_parse_source(s) for s in (args.source or ["transcripts:1.0"])]
    if args.limit is not None:
        for s in specs:
            s.kwargs["limit"] = args.limit
    for s in specs:
        if s.source_id not in ADAPTERS:
            raise SystemExit(f"unknown source {s.source_id!r}; have {sorted(ADAPTERS)}")

    out_path = (WORD_FREQ_CANONICAL if args.canonical
                else Path(args.out) if args.out else WORD_FREQ_OUT)
    print(f"Pooling {len(specs)} source(s): "
          f"{', '.join(f'{s.source_id}:{s.weight}' for s in specs)}")
    pool = count_sources(specs)
    pool.write_word_frequencies(out_path)
    if not args.no_sources_file:
        pool.write_sources()
    merged = pool.merged()
    print(f"  unique surfaces: {len(merged):,}  ->  {out_path}")
    print(f"  per-source file: {SOURCES_OUT}")


if __name__ == "__main__":
    main()
