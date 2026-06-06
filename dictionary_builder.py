"""
dictionary_builder.py -- Step 5 of System 1

Builds the canonical Base64 dictionary from corpus frequency data.
Writes to LMDB for production use (~100ns lookup, RAM-resident).

UNIFIED MODEL
    All surface forms compete in one frequency ranking.
    Most frequent unit gets shortest ID, regardless of type.
    No distinction between words, contractions, or phrases at this layer.

ID ASSIGNMENT
    Tier 0  (1-char, A-Z):   26 hardcoded universal words from config.WORD_IDS
    Tier 1  (2-char, g-z):   next most frequent  →  up to 1,280 IDs
    Tier 2  (3-char, g-z):   next most frequent  →  up to 81,920 IDs
    Remainder:                OOV at encode time (preserved losslessly)

STORAGE
    LMDB — two named databases in one environment
        forward:  word_bytes  → id_bytes   (encode)
        reverse:  id_bytes    → word_bytes  (decode)
    All keys and values are UTF-8 encoded bytes.
    Integer metadata (frequency, counts) packed as little-endian uint32
    via struct.pack('<I', n) — readable from C.

OUTPUT
    dictionary.lmdb/    LMDB environment (~few MB)
    dict_stats.json     build metadata + coverage stats
"""

import json
import struct
import sys
from collections import Counter
from pathlib import Path

import lmdb
from tqdm import tqdm

sys.path.insert(0, '.')

from semantic_compression.config import (
    BASE64_CHARS, FORMAT_VERSION, STREAM_ENCODING,
    TIER_WORD_FIRST_CHARS, WORD_IDS,
)

FREQ_FILE   = Path('semantic_compression/data/word_frequencies.txt')
LMDB_PATH   = Path('semantic_compression/db/dictionary.lmdb')
STATS_FILE  = Path('semantic_compression/db/dict_stats.json')
MAP_SIZE_GB = 1   # LMDB map ceiling — actual usage is ~few MB


# ---------------------------------------------------------------------------
# ID encoder  (same logic as library_builder, kept self-contained here)
# ---------------------------------------------------------------------------

def _encode_id(tier: int, counter: int) -> str:
    """
    Encode sequential counter to Base64 ID.
    Tier 1 -> 2-char, Tier 2 -> 3-char. First char always from g-z.
    """
    length = tier + 1
    chars = []
    remaining = counter
    for _ in range(length - 1):
        chars.append(BASE64_CHARS[remaining % 64])
        remaining //= 64
    if remaining >= len(TIER_WORD_FIRST_CHARS):
        raise OverflowError(f'Tier {tier} ID space exhausted at counter={counter}')
    chars.append(TIER_WORD_FIRST_CHARS[remaining])
    return ''.join(reversed(chars))


TIER_CAPACITY = {
    1: len(TIER_WORD_FIRST_CHARS) * 64,        # 1,280
    2: len(TIER_WORD_FIRST_CHARS) * 64 ** 2,   # 81,920
}


# ---------------------------------------------------------------------------
# Load frequency file
# ---------------------------------------------------------------------------

def load_frequencies(freq_file: Path) -> Counter:
    """
    Read word_frequencies.txt into a Counter.
    Format: <count>TAB<word>  (header lines start with #, skipped).
    """
    counts: Counter = Counter()
    with open(freq_file, 'r', encoding=STREAM_ENCODING) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            count_str, word = line.split('\t', 1)
            counts[word] = int(count_str)
    return counts


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(
    freq_file: Path  = FREQ_FILE,
    lmdb_path: Path  = LMDB_PATH,
    stats_file: Path = STATS_FILE,
    map_size_gb: int = MAP_SIZE_GB,
) -> dict:
    """
    Build the LMDB dictionary from word_frequencies.txt.

    Returns stats dict with coverage information.
    """
    print(f'Loading frequencies from {freq_file} ...')
    all_counts = load_frequencies(freq_file)
    total_corpus_tokens = sum(all_counts.values())
    print(f'  {len(all_counts):,} unique surface forms  |  {total_corpus_tokens:,} total tokens')

    # Tier 0: hardcoded universal words (word -> single-char ID)
    tier0_map = {word: char for char, word in WORD_IDS.items()}   # e.g. 'the' -> 'T'
    tier0_set = set(tier0_map.keys())

    # Open LMDB
    lmdb_path.mkdir(parents=True, exist_ok=True)
    env = lmdb.open(
        str(lmdb_path),
        map_size=map_size_gb * 1024 ** 3,
        max_dbs=2,
    )
    fwd_db = env.open_db(b'forward')   # word  -> id
    rev_db = env.open_db(b'reverse')   # id    -> word

    tier_counts  = {0: 0, 1: 0, 2: 0}
    tier_tokens  = {0: 0, 1: 0, 2: 0}   # token coverage per tier
    oov_tokens   = 0
    t1_counter   = 0
    t2_counter   = 0

    with env.begin(write=True) as txn:

        # -- Tier 0: seed from config.WORD_IDS --
        for word, char_id in tier0_map.items():
            txn.put(word.encode(STREAM_ENCODING), char_id.encode(STREAM_ENCODING), db=fwd_db)
            txn.put(char_id.encode(STREAM_ENCODING), word.encode(STREAM_ENCODING), db=rev_db)
            tier_counts[0] += 1
            tier_tokens[0] += all_counts.get(word, 0)

        # -- Tier 1 + 2: corpus frequency ranked --
        for word, freq in tqdm(
            all_counts.most_common(), desc='Assigning IDs', unit='word'
        ):
            if word in tier0_set:
                continue   # already assigned

            if t1_counter < TIER_CAPACITY[1]:
                token_id = _encode_id(1, t1_counter)
                t1_counter += 1
                tier_counts[1] += 1
                tier_tokens[1] += freq
            elif t2_counter < TIER_CAPACITY[2]:
                token_id = _encode_id(2, t2_counter)
                t2_counter += 1
                tier_counts[2] += 1
                tier_tokens[2] += freq
            else:
                oov_tokens += freq
                continue   # beyond 3-char space — OOV at runtime

            txn.put(word.encode(STREAM_ENCODING), token_id.encode(STREAM_ENCODING), db=fwd_db)
            txn.put(token_id.encode(STREAM_ENCODING), word.encode(STREAM_ENCODING), db=rev_db)

    env.close()

    # -- Stats --
    assigned_tokens = sum(tier_tokens.values())
    oov_tokens      = total_corpus_tokens - assigned_tokens
    coverage_pct    = 100 * assigned_tokens / total_corpus_tokens if total_corpus_tokens else 0

    stats = {
        'format_version':        FORMAT_VERSION,
        'source_freq_file':      str(freq_file),
        'lmdb_path':             str(lmdb_path),
        'total_corpus_tokens':   total_corpus_tokens,
        'unique_surface_forms':  len(all_counts),
        'total_assigned':        sum(tier_counts.values()),
        'tier0_words':           tier_counts[0],
        'tier1_words':           tier_counts[1],
        'tier2_words':           tier_counts[2],
        'oov_surface_forms':     len(all_counts) - sum(tier_counts.values()),
        'token_coverage_pct':    round(coverage_pct, 4),
        'tier0_token_coverage':  round(100 * tier_tokens[0] / total_corpus_tokens, 4),
        'tier1_token_coverage':  round(100 * tier_tokens[1] / total_corpus_tokens, 4),
        'tier2_token_coverage':  round(100 * tier_tokens[2] / total_corpus_tokens, 4),
        'oov_token_pct':         round(100 * oov_tokens / total_corpus_tokens, 4),
        'tier1_capacity':        TIER_CAPACITY[1],
        'tier2_capacity':        TIER_CAPACITY[2],
    }

    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with open(stats_file, 'w', encoding=STREAM_ENCODING) as f:
        json.dump(stats, f, indent=2)

    _print_stats(stats)
    return stats


def _print_stats(s: dict) -> None:
    print()
    print('=== Dictionary Build Complete ===')
    print(f"  Format version:     {s['format_version']}")
    print(f"  Unique forms:       {s['unique_surface_forms']:>10,}")
    print(f"  Total assigned:     {s['total_assigned']:>10,}")
    print(f"    Tier 0 (1-char):  {s['tier0_words']:>10,}   {s['tier0_token_coverage']:5.1f}% token coverage")
    print(f"    Tier 1 (2-char):  {s['tier1_words']:>10,}   {s['tier1_token_coverage']:5.1f}% token coverage")
    print(f"    Tier 2 (3-char):  {s['tier2_words']:>10,}   {s['tier2_token_coverage']:5.1f}% token coverage")
    print(f"  OOV surface forms:  {s['oov_surface_forms']:>10,}   {s['oov_token_pct']:5.1f}% of tokens (handled losslessly)")
    print(f"  Total token coverage: {s['token_coverage_pct']:.2f}%")
    print(f"  LMDB: {s['lmdb_path']}")
    print(f"  Stats: {STATS_FILE}")


# ---------------------------------------------------------------------------
# Spot-check helper (for verification)
# ---------------------------------------------------------------------------

def spot_check(words: list[str], lmdb_path: Path = LMDB_PATH) -> None:
    """Look up a list of words and print their IDs and back-decoded values."""
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=2)
    fwd_db = env.open_db(b'forward')
    rev_db = env.open_db(b'reverse')
    with env.begin() as txn:
        print(f'\n{"WORD":<25} {"ID":<8} {"DECODED":<25} {"MATCH"}')
        print('-' * 65)
        for word in words:
            raw_id  = txn.get(word.encode(STREAM_ENCODING), db=fwd_db)
            token_id = raw_id.decode(STREAM_ENCODING) if raw_id else None
            if token_id:
                decoded = txn.get(token_id.encode(STREAM_ENCODING), db=rev_db)
                decoded = decoded.decode(STREAM_ENCODING) if decoded else None
                match = 'OK' if decoded == word else 'MISMATCH'
            else:
                decoded, match = None, 'OOV'
            print(f'{word:<25} {str(token_id):<8} {str(decoded):<25} {match}')
    env.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    build()
