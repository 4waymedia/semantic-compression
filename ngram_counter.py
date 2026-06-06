"""
ngram_counter.py -- Step 11 of v0.3

Mines phrase-atom candidates from the corpus.

Process:
  1. Stream corpus chunks via corpus_scanner (ASR-deduped text).
  2. Skip the 3 held-out measurement transcripts.
  3. Tokenize via universal tokenizer, keep only word-class tokens.
  4. Count contiguous word n-grams (length 2..9) within each chunk.
     N-grams never cross chunk boundaries.
  5. Periodically prune low-count entries to bound memory.
  6. For every n-gram, compute estimated compression savings if promoted
     to a single dictionary ID in v0.2 binary format.
  7. Report top phrases by:
        (a) raw frequency
        (b) per-occurrence savings (favours long phrases)
        (c) total estimated savings (frequency * per-occurrence)
        --> the "total savings" view drives v0.3 promotion order.

Usage:
    python -m semantic_compression.ngram_counter
    python -m semantic_compression.ngram_counter --limit 100   # 100 files
    python -m semantic_compression.ngram_counter --min 200     # only freq >= 200
    python -m semantic_compression.ngram_counter --nmax 5      # 2-5 grams only

Output:
    data/ngram_frequencies.txt        all surviving n-grams,
                                      sorted by total estimated savings desc
    data/ngram_top_summary.txt        top 100/250/500/1000 phrases
                                      with savings breakdown
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, '.')

from semantic_compression.config import DB_PATH, FORMAT_VERSION, TRANSCRIPT_DIR
from semantic_compression.corpus_scanner import scan_transcripts
from semantic_compression.tokenizer import CLASS_WORD, classify, tokenize


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NGRAM_MIN_DEFAULT = 2
NGRAM_MAX_DEFAULT = 9
MIN_FREQ_DEFAULT  = 100      # below this floor we discard at end

# Held-out measurement files -- excluded from mining so the benchmark
# numbers cannot be accused of memorisation.
HELD_OUT_VIDEO_IDS = {
    'KhyQCU6oqE8',    # times_now
    'KOhbGjmidgs',    # jocko_podcast
    'bRwFb8JmznE',    # julian_dorey
}

# Periodic pruning to bound memory.
PRUNE_INTERVAL    = 50_000       # chunks
PRUNE_FLOOR_FUNC  = lambda chunks_seen: max(2, chunks_seen // 50_000)

OUTPUT_DIR        = Path('semantic_compression/data')
OUT_ALL           = OUTPUT_DIR / 'ngram_frequencies.txt'
OUT_SUMMARY       = OUTPUT_DIR / 'ngram_top_summary.txt'


# ---------------------------------------------------------------------------
# Byte-cost model -- v0.2 binary stream
# ---------------------------------------------------------------------------
#
# In the v0.2 binary format, each in-vocab token takes:
#   Tier 0  (1 char ID)   1 byte
#   Tier 1  (2 char IDs)  2 bytes
#   Tier 2  (3 char IDs)  3 bytes
#   Tier 3  (4 char IDs)  4 bytes
#
# Implicit-whitespace transform removes single-space tokens between words,
# so we only count the word tokens themselves.
#
# Effective per-word costs in the current corpus:
#   ~31% of word occurrences are Tier 0 (26 words) -> 1 byte
#   ~50% are Tier 1                                  -> 2 bytes
#   ~17% are Tier 2                                  -> 3 bytes
#   ~ 1% are Tier 3                                  -> 4 bytes
#   ~ 1% are OOV (~5 bytes including marker overhead)
#
# Weighted average: 0.31*1 + 0.50*2 + 0.17*3 + 0.01*4 + 0.01*5  = 1.91 bytes/word
# Rounded conservatively to 1.8 to avoid overstating savings.
# ---------------------------------------------------------------------------

WORD_AVG_BYTES = 1.8


def phrase_byte_cost(rank: int) -> int:
    """Cost in v0.2 binary bytes for a phrase placed at the given dictionary rank."""
    # Tier 0 (1-char): 64 slots -- skip, used for system/words/structural
    # Tier 1 (2-char): up to 1,280 dictionary entries
    # Tier 2 (3-char): up to 81,920
    # Tier 3 (4-char): unbounded
    if rank < 1280:
        return 2
    if rank < 1280 + 81920:
        return 3
    return 4


# ---------------------------------------------------------------------------
# Mining
# ---------------------------------------------------------------------------

def iter_word_tokens(text: str) -> list[str]:
    """Return only word-class tokens from text. Lowercased for canonical key."""
    return [t.lower() for t in tokenize(text) if classify(t) == CLASS_WORD]


def mine_ngrams(
    transcript_dir: str = TRANSCRIPT_DIR,
    db_path: str = DB_PATH,
    file_limit: int | None = None,
    n_min: int = NGRAM_MIN_DEFAULT,
    n_max: int = NGRAM_MAX_DEFAULT,
    excluded_video_ids: set[str] = HELD_OUT_VIDEO_IDS,
) -> dict[int, Counter]:
    """
    Stream corpus, count word-class n-grams of every length in [n_min, n_max].

    Returns:
        {n: Counter({ngram_string: count, ...})}
    """
    counters: dict[int, Counter] = {n: Counter() for n in range(n_min, n_max + 1)}
    chunks_seen = 0
    excluded_chunks = 0

    for video_id, _chunk_id, text, _meta in scan_transcripts(
        transcript_dir=transcript_dir,
        db_path=db_path,
        skip_processed=False,
        limit=file_limit,
        show_progress=True,
    ):
        if video_id in excluded_video_ids:
            excluded_chunks += 1
            continue

        chunks_seen += 1
        words = iter_word_tokens(text)
        wlen = len(words)
        for n in range(n_min, n_max + 1):
            if wlen < n:
                continue
            c = counters[n]
            for i in range(wlen - n + 1):
                ngram = ' '.join(words[i:i + n])
                c[ngram] += 1

        if chunks_seen % PRUNE_INTERVAL == 0:
            floor = PRUNE_FLOOR_FUNC(chunks_seen)
            for n in counters:
                counters[n] = Counter({k: v for k, v in counters[n].items() if v > floor})

    print(f"Mining done. chunks_used={chunks_seen:,}  chunks_excluded(held-out)={excluded_chunks:,}")
    return counters


# ---------------------------------------------------------------------------
# Savings ranking
# ---------------------------------------------------------------------------

def score_candidates(
    counters: dict[int, Counter],
    min_freq: int = MIN_FREQ_DEFAULT,
) -> list[dict]:
    """
    Compute estimated total compression savings for each candidate phrase.

    Returns list of records:
      {
        'phrase':        str,
        'n':             int,    # number of word tokens
        'freq':          int,    # occurrences in corpus
        'word_cost':     float,  # current cost = n * WORD_AVG_BYTES
        'phrase_cost':   int,    # cost if promoted (assumes Tier 2)
        'savings_each':  float,  # bytes saved per occurrence
        'savings_total': float,  # cumulative bytes saved across corpus
      }
    """
    # Default phrase cost: assume Tier 2 (3 bytes) for ranking; later we re-rank
    # and refine the cost using actual tier placement.
    default_cost = 3   # Tier 2 byte cost in v0.2 binary

    records: list[dict] = []
    for n, c in counters.items():
        word_cost = n * WORD_AVG_BYTES
        savings_each = word_cost - default_cost
        if savings_each <= 0:
            continue   # phrase wouldn't save anything; n=2 with 1.8b/word gives -0.4 < 0 -- skip

        for phrase, freq in c.items():
            if freq < min_freq:
                continue
            records.append({
                'phrase':        phrase,
                'n':             n,
                'freq':          freq,
                'word_cost':     word_cost,
                'phrase_cost':   default_cost,
                'savings_each':  savings_each,
                'savings_total': freq * savings_each,
            })

    # Sort by total savings descending
    records.sort(key=lambda r: r['savings_total'], reverse=True)
    return records


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_all_ngrams(records: list[dict], path: Path = OUT_ALL) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# ngram_frequencies.txt  format_version={FORMAT_VERSION}\n")
        f.write("# rank<TAB>n<TAB>freq<TAB>savings_each<TAB>savings_total<TAB>phrase\n")
        for rank, r in enumerate(records, 1):
            f.write(
                f"{rank}\t{r['n']}\t{r['freq']}\t"
                f"{r['savings_each']:.2f}\t{int(r['savings_total'])}\t{r['phrase']}\n"
            )


def write_summary(records: list[dict], path: Path = OUT_SUMMARY) -> None:
    """Write the top 100 / 250 / 500 / 1000 summary report."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def cum_savings(n: int) -> int:
        return int(sum(r['savings_total'] for r in records[:n]))

    with open(path, 'w', encoding='utf-8') as f:
        f.write("# ngram_top_summary.txt\n")
        f.write(f"# format_version={FORMAT_VERSION}\n")
        f.write(f"# candidates above MIN_FREQ floor: {len(records):,}\n\n")

        for top_n in (100, 250, 500, 1000):
            slice_ = records[:top_n]
            if not slice_:
                continue
            total = cum_savings(top_n)
            f.write(f"=== TOP {top_n} ===\n")
            f.write(f"Cumulative estimated savings: {total:,} bytes\n\n")
            f.write(f"{'RANK':>5}  {'N':>2}  {'FREQ':>10}  {'SAVE/OCC':>9}  "
                    f"{'TOTAL SAVE':>13}  PHRASE\n")
            for rank, r in enumerate(slice_, 1):
                f.write(
                    f"{rank:>5}  {r['n']:>2}  {r['freq']:>10,}  "
                    f"{r['savings_each']:>9.2f}  {int(r['savings_total']):>13,}  "
                    f"{r['phrase']}\n"
                )
            f.write("\n")


def print_console_summary(records: list[dict]) -> None:
    if not records:
        print("\nNo candidates above the minimum frequency floor.")
        return

    print()
    print(f"=== PHRASE MINING -- TOTAL CANDIDATES: {len(records):,} ===")
    print()

    cumulative_savings_at = {n: int(sum(r['savings_total'] for r in records[:n]))
                             for n in (100, 250, 500, 1000)}

    print("Cumulative estimated bytes saved by promoting top N:")
    for n, s in cumulative_savings_at.items():
        if len(records) < n:
            continue
        print(f"  top {n:>5,}:  {s:>15,} bytes  ({s/1024/1024:.2f} MB)")

    print()
    print(f"Top 30 phrases by total estimated savings:")
    print(f"  {'RANK':>4}  {'N':>2}  {'FREQ':>10}  {'BYTES SAVED':>13}  PHRASE")
    print(f"  {'-'*60}")
    for rank, r in enumerate(records[:30], 1):
        print(f"  {rank:>4}  {r['n']:>2}  {r['freq']:>10,}  "
              f"{int(r['savings_total']):>13,}  {r['phrase']!r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Mine phrase candidates + compression savings")
    p.add_argument('--limit', type=int, default=None,
                   help="cap file count for dev runs (default: all 14,806)")
    p.add_argument('--min',   type=int, default=MIN_FREQ_DEFAULT,
                   help=f"minimum corpus frequency to retain a candidate (default {MIN_FREQ_DEFAULT})")
    p.add_argument('--nmin',  type=int, default=NGRAM_MIN_DEFAULT,
                   help=f"shortest n-gram length (default {NGRAM_MIN_DEFAULT})")
    p.add_argument('--nmax',  type=int, default=NGRAM_MAX_DEFAULT,
                   help=f"longest n-gram length (default {NGRAM_MAX_DEFAULT})")
    args = p.parse_args()

    print(f"Phrase Mining  |  n={args.nmin}..{args.nmax}  |  "
          f"min_freq={args.min}  |  files={args.limit or 'all'}")
    print(f"Held-out (excluded): {sorted(HELD_OUT_VIDEO_IDS)}")
    print()

    counters = mine_ngrams(
        file_limit=args.limit,
        n_min=args.nmin,
        n_max=args.nmax,
    )

    print()
    print("Unique n-grams (post-prune) by length:")
    for n in sorted(counters.keys()):
        print(f"  {n}-gram:  {len(counters[n]):>10,}")

    records = score_candidates(counters, min_freq=args.min)

    write_all_ngrams(records)
    write_summary(records)

    print_console_summary(records)

    print()
    print(f"Wrote: {OUT_ALL}")
    print(f"Wrote: {OUT_SUMMARY}")


if __name__ == '__main__':
    main()
