"""
word_frequency_counter.py -- Step 4 of System 1

Scans all transcript JSON files via corpus_scanner (ASR-deduped, clean text).
Counts every surface form word frequency across the full corpus.
Outputs word_frequencies.txt sorted by frequency descending.

Run this first to see:
  - Total unique surface forms in corpus
  - Actual top words (validate against hardcoded Tier 0 WORD_IDS)
  - Natural tier boundary points

Usage:
    python -m semantic_compression.word_frequency_counter
    python -m semantic_compression.word_frequency_counter --limit 100
    python -m semantic_compression.word_frequency_counter --raw

    --limit N     process only N transcript files (for dev/testing)
    --raw         count from raw JSON chunk text instead of deduped scanner
                  (faster, but inflates frequencies from ASR repetition)

Output:
    semantic_compression/data/word_frequencies.txt
    format: <count>TAB<word>   one per line, sorted descending
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, '.')

from semantic_compression.config import (
    FORMAT_VERSION, TRANSCRIPT_DIR, WORD_IDS, DB_PATH,
)
from semantic_compression.corpus_scanner import scan_transcripts

OUTPUT_PATH = Path('semantic_compression/data/word_frequencies.txt')
TOP_N_DISPLAY = 60   # how many top words to print in summary


# ---------------------------------------------------------------------------
# Counting modes
# ---------------------------------------------------------------------------

def count_via_scanner(transcript_dir: str, db_path: str, limit: int | None) -> Counter:
    """
    Count surface forms from corpus_scanner output.
    Text is ASR-deduped and lowercased — clean counts.
    """
    counts: Counter = Counter()
    for _vid, _cid, text, _meta in scan_transcripts(
        transcript_dir=transcript_dir,
        db_path=db_path,
        skip_processed=False,
        limit=limit,
        show_progress=True,
    ):
        counts.update(text.split())
    return counts


def count_via_raw_json(transcript_dir: str, limit: int | None) -> Counter:
    """
    Count surface forms directly from raw JSON chunk text.
    Faster but includes ASR rolling-window duplicates — frequencies inflated.
    Use only for quick smoke tests.
    """
    counts: Counter = Counter()
    files = sorted(Path(transcript_dir).rglob('*.json'))
    if limit:
        files = files[:limit]

    for path in tqdm(files, desc='Counting (raw)', unit='file'):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for chunk in data.get('chunks', []):
                words = chunk.get('text', '').lower().split()
                counts.update(words)
        except Exception:
            continue
    return counts


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_frequencies(counts: Counter, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f'# word_frequencies.txt  format_version={FORMAT_VERSION}\n')
        f.write(f'# count\tword\n')
        for word, count in counts.most_common():
            f.write(f'{count}\t{word}\n')


def print_summary(counts: Counter, mode: str) -> None:
    total_tokens  = sum(counts.values())
    unique_forms  = len(counts)
    top           = counts.most_common(TOP_N_DISPLAY)

    print()
    print(f'=== Word Frequency Summary (mode={mode}) ===')
    print(f'  Total tokens (post-dedup): {total_tokens:>12,}')
    print(f'  Unique surface forms:      {unique_forms:>12,}')
    print()

    # Coverage stats at tier boundaries
    for label, cutoff in [('Top 26 (Tier 0)', 26), ('Top 1000 (Tier 1)', 1000),
                           ('Top 4096 (Tier 1 full)', 4096), ('Top 10000 (Tier 2)', 10000)]:
        tier_total = sum(c for _, c in counts.most_common(cutoff))
        pct = 100 * tier_total / total_tokens if total_tokens else 0
        print(f'  {label:<28}  covers {pct:5.1f}% of tokens')

    print()
    print(f'  Top {TOP_N_DISPLAY} words:')
    print(f'  {"RANK":>5}  {"COUNT":>10}  {"WORD":<20}  {"IN TIER 0?"}')
    print(f'  {"-"*55}')
    tier0_words = set(WORD_IDS.values())
    for rank, (word, count) in enumerate(top, 1):
        flag = '  <-- Tier 0' if word in tier0_words else ''
        pct  = 100 * count / total_tokens if total_tokens else 0
        print(f'  {rank:>5}  {count:>10,}  {word:<20}  {pct:4.2f}%{flag}')

    # Check: any Tier 0 words missing from top 60?
    top_words = {w for w, _ in top}
    missing   = tier0_words - top_words
    if missing:
        print()
        print(f'  NOTE: {len(missing)} Tier 0 words not in top {TOP_N_DISPLAY}:')
        for w in sorted(missing):
            rank_val = next(
                (i + 1 for i, (ww, _) in enumerate(counts.most_common()) if ww == w),
                None,
            )
            print(f'    {w!r}  (rank ~{rank_val})')

    print()
    print(f'  Output: {OUTPUT_PATH}')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description='Count word frequencies across transcript corpus')
    parser.add_argument('--limit', type=int, default=None,
                        help='Max transcript files to process (default: all)')
    parser.add_argument('--raw', action='store_true',
                        help='Use raw JSON text instead of deduped scanner (faster, less accurate)')
    args = parser.parse_args()

    mode = 'raw' if args.raw else 'scanner'
    print(f'Word Frequency Counter  |  mode={mode}  |  limit={args.limit or "all"}')
    print(f'Transcript dir: {TRANSCRIPT_DIR}')
    print()

    if args.raw:
        counts = count_via_raw_json(TRANSCRIPT_DIR, args.limit)
    else:
        counts = count_via_scanner(TRANSCRIPT_DIR, DB_PATH, args.limit)

    write_frequencies(counts, OUTPUT_PATH)
    print_summary(counts, mode)


if __name__ == '__main__':
    main()
