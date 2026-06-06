"""
word_frequency_counter.py -- Step 8 rebuild of System 1

Scans transcript JSON files through corpus_scanner or raw JSON mode.
Counts tokenizer-level surface tokens for the canonical dictionary build.

System 1 v1 rules:
  - Do NOT lowercase before tokenization.
  - Do NOT collapse whitespace.
  - Do NOT use text.split() in the canonical pipeline.
  - Preserve whitespace tokens.
  - Preserve punctuation tokens.
  - Lowercase only the dictionary frequency key after tokenize().
  - Write token keys escaped so whitespace tokens survive the frequency file.

Usage:
    python -m semantic_compression.word_frequency_counter
    python -m semantic_compression.word_frequency_counter --limit 100
    python -m semantic_compression.word_frequency_counter --raw

Output:
    semantic_compression/data/word_frequencies.txt
    format: <count>TAB<escaped_token>
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

from tqdm import tqdm

sys.path.insert(0, ".")

from semantic_compression.config import (
    FORMAT_VERSION,
    TRANSCRIPT_DIR,
    WORD_IDS,
    DB_PATH,
)

from semantic_compression.corpus_scanner import scan_transcripts
from semantic_compression.tokenizer import tokenize

OUTPUT_PATH = Path("semantic_compression/data/word_frequencies.txt")
TOP_N_DISPLAY = 60


# ---------------------------------------------------------------------------
# Token key encoding
# ---------------------------------------------------------------------------

def normalize_dictionary_key(token: str) -> str:
    """
    Normalize only the dictionary frequency key.

    The tokenizer receives raw text and preserves case/whitespace.
    The dictionary key is lowercased so caps_codec can handle case separately.
    """
    return token.lower()


def encode_frequency_token(token: str) -> str:
    """
    Escape token for safe storage in a tab-separated text file.

    Needed because tokens may be:
      - " "
      - "\\n"
      - "\\t"
      - punctuation
      - unicode text
    """
    return token.encode("unicode_escape").decode("ascii")


def decode_frequency_token(raw: str) -> str:
    """
    Companion helper for dictionary_builder.py.
    Keep this here too so tests can validate round-trip behavior.
    """
    return raw.encode("ascii").decode("unicode_escape")


# ---------------------------------------------------------------------------
# Text sources
# ---------------------------------------------------------------------------

def iter_scanner_texts(
    transcript_dir: str,
    db_path: str,
    limit: int | None,
) -> Iterable[str]:
    """
    Yield ASR-deduped transcript text from corpus_scanner.

    corpus_scanner must preserve whitespace and case.
    It may decode HTML entities.
    It must not lowercase or collapse whitespace.
    """
    for item in scan_transcripts(
        transcript_dir=transcript_dir,
        db_path=db_path,
        skip_processed=False,
        limit=limit,
        show_progress=True,
    ):
        # Backward-compatible with existing scanner tuple output:
        # (video_id, chunk_id, text, metadata)
        if isinstance(item, tuple):
            yield item[2]
        else:
            yield item


def iter_raw_json_texts(
    transcript_dir: str,
    limit: int | None,
) -> Iterable[str]:
    """
    Yield raw JSON chunk text.

    Raw mode is useful for smoke tests, but may include ASR repetition.
    It still must not lowercase or whitespace-split.
    """
    files = sorted(Path(transcript_dir).rglob("*.json"))
    if limit:
        files = files[:limit]

    for path in tqdm(files, desc="Counting (raw)", unit="file"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for chunk in data.get("chunks", []):
                yield chunk.get("text", "")

        except Exception:
            continue


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

def count_texts(texts: Iterable[str]) -> Counter:
    """
    Count tokenizer-level surface tokens.

    This intentionally counts:
      - words
      - whitespace
      - punctuation
      - symbols
      - structural tokens

    No filtering by isalpha().
    No text.split().
    """
    counts: Counter = Counter()

    for text in texts:
        for token in tokenize(text):
            key = normalize_dictionary_key(token)
            counts[key] += 1

    return counts


def count_via_scanner(
    transcript_dir: str,
    db_path: str,
    limit: int | None,
) -> Counter:
    return count_texts(iter_scanner_texts(transcript_dir, db_path, limit))


def count_via_raw_json(
    transcript_dir: str,
    limit: int | None,
) -> Counter:
    return count_texts(iter_raw_json_texts(transcript_dir, limit))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_frequencies(counts: Counter, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# word_frequencies.txt  format_version={FORMAT_VERSION}\n")
        f.write("# count\\tescaped_token\n")

        for token, count in counts.most_common():
            escaped = encode_frequency_token(token)
            f.write(f"{count}\t{escaped}\n")


def display_token(token: str) -> str:
    """
    Human-readable display for summary output.
    """
    if token == " ":
        return "<SPACE>"
    if token == "\n":
        return "<NEWLINE>"
    if token == "\t":
        return "<TAB>"
    return token


def print_summary(counts: Counter, mode: str) -> None:
    total_tokens = sum(counts.values())
    unique_forms = len(counts)
    top = counts.most_common(TOP_N_DISPLAY)

    print()
    print(f"=== Token Frequency Summary (mode={mode}) ===")
    print(f"  Total tokens:          {total_tokens:>12,}")
    print(f"  Unique token forms:    {unique_forms:>12,}")
    print()

    for label, cutoff in [
        ("Top 26", 26),
        ("Top 1000", 1000),
        ("Top 4096", 4096),
        ("Top 10000", 10000),
    ]:
        tier_total = sum(c for _, c in counts.most_common(cutoff))
        pct = 100 * tier_total / total_tokens if total_tokens else 0
        print(f"  {label:<16} covers {pct:5.1f}% of tokens")

    print()
    print(f"  Top {TOP_N_DISPLAY} tokens:")
    print(f"  {'RANK':>5}  {'COUNT':>10}  {'TOKEN':<24}  {'IN WORD TIER 0?'}")
    print(f"  {'-' * 65}")

    tier0_words = set(WORD_IDS.values())

    for rank, (token, count) in enumerate(top, 1):
        flag = "  <-- Word Tier 0" if token in tier0_words else ""
        pct = 100 * count / total_tokens if total_tokens else 0
        shown = display_token(token)
        print(f"  {rank:>5}  {count:>10,}  {shown!r:<24}  {pct:4.2f}%{flag}")

    top_tokens = {token for token, _ in top}
    missing = tier0_words - top_tokens

    if missing:
        print()
        print(f"  NOTE: {len(missing)} Tier 0 words not in top {TOP_N_DISPLAY}:")
        all_common = counts.most_common()
        for word in sorted(missing):
            rank_val = next(
                (i + 1 for i, (token, _) in enumerate(all_common) if token == word),
                None,
            )
            print(f"    {word!r}  (rank ~{rank_val})")

    print()
    print(f"  Output: {OUTPUT_PATH}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Count tokenizer-level frequencies across transcript corpus"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max transcript files to process",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Use raw JSON text instead of deduped scanner",
    )
    args = parser.parse_args()

    mode = "raw" if args.raw else "scanner"

    print(f"Word Frequency Counter  |  mode={mode}  |  limit={args.limit or 'all'}")
    print(f"Transcript dir: {TRANSCRIPT_DIR}")
    print()

    if args.raw:
        counts = count_via_raw_json(TRANSCRIPT_DIR, args.limit)
    else:
        counts = count_via_scanner(TRANSCRIPT_DIR, DB_PATH, args.limit)

    write_frequencies(counts, OUTPUT_PATH)
    print_summary(counts, mode)


if __name__ == "__main__":
    main()