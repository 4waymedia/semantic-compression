"""
dictionary_builder_v03.py -- Step 13 of v0.3

Builds the v0.3 canonical dictionary by merging single-word and phrase
candidates into one unified frequency ranking, then assigning Tier 0-3
Base64 IDs and integer LLM IDs.

Difference from v0.2 dictionary_builder.py:
  - Loads phrase_candidates.txt alongside word_frequencies.txt
  - Phrases compete with words on raw frequency for tier slots
  - Top phrases land in Tier 1 (2-byte IDs) -- maximum compression payoff
  - Emits LLM profile artifacts (token-ids, special-tokens, byte-fallback)
  - Bumps FORMAT_VERSION to 3 in stats

v0.2 reproducibility is preserved via dictionary_builder.py (unchanged)
and the git tag `v0.2` at commit 3f0ac94. To regenerate the v0.2 LMDB,
checkout that tag and run dictionary_builder.

Inputs:
    data/word_frequencies.txt
    data/phrase_candidates.txt

Outputs:
    db/dictionary.lmdb               unified LMDB (forward + reverse)
    db/dict_stats_v03.json           build metadata + profile cuts
    data/token-ids-v1.csv.gz         frozen vocabulary contract
                                     id,base64_id,surface,tier,freq,tiny,
                                     compact,standard,full
    data/special-tokens-v1.json      16 LLM special token definitions
    data/byte-fallback-v1.csv        256-row byte ID table per profile
    data/profile-cuts-v1.json        profile rank boundaries
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path

import lmdb
from tqdm import tqdm

sys.path.insert(0, '.')

from semantic_compression.config import (
    BASE64_CHARS, STREAM_ENCODING, STRUCTURAL_IDS, TIER_WORD_FIRST_CHARS, WORD_IDS,
)

# v0.3 format version (NEW - distinct from v0.2's FORMAT_VERSION=1)
FORMAT_VERSION_V03 = 3

# Paths
DATA_DIR        = Path('semantic_compression/data')
DB_DIR          = Path('semantic_compression/db')

WORD_FREQ_FILE  = DATA_DIR / 'word_frequencies.txt'
PHRASE_FILE     = DATA_DIR / 'phrase_candidates.txt'

LMDB_PATH       = DB_DIR / 'dictionary.lmdb'
STATS_FILE      = DB_DIR / 'dict_stats_v03.json'

TOKEN_IDS_CSV   = DATA_DIR / 'token-ids-v1.csv.gz'
SPECIAL_TOK_JSON = DATA_DIR / 'special-tokens-v1.json'
BYTE_FALLBACK_CSV = DATA_DIR / 'byte-fallback-v1.csv'
PROFILE_CUTS_JSON = DATA_DIR / 'profile-cuts-v1.json'

MAP_SIZE_GB = 1
MIN_FREQ_FOR_DICT = 5   # don't promote hapaxes and near-hapaxes

# Forced first-Tier-1 seeds (cannot appear in OOV body)
FORCED_DICT_TOKENS = ['|']

# Tier capacity from g-z first chars (20 prefixes)
TIER_CAPACITY = {
    1: len(TIER_WORD_FIRST_CHARS) * 64,         # 1,280
    2: len(TIER_WORD_FIRST_CHARS) * 64 ** 2,    # 81,920
    3: len(TIER_WORD_FIRST_CHARS) * 64 ** 3,    # 5,242,880
}

# Profile sizes (powers of 2, content slots only; bytes + special added at runtime)
PROFILE_CONTENT_SIZES = {
    'tiny':     32_496,      # 32,768 - 256 (bytes) - 16 (special)
    'compact':  65_264,      # 65,536 - 256 - 16
    'standard': 130_800,     # 131,072 - 256 - 16
    'full':     261_872,     # 262,144 - 256 - 16
    'reference': None,       # all content
}

BYTE_FALLBACK_SIZE = 256
SPECIAL_TOKEN_COUNT = 16

# Locked LLM special tokens (16 slots reserved at the tail of every profile)
SPECIAL_TOKENS = [
    'PAD', 'BOS', 'EOS', 'UNK', 'SEP', 'MASK', 'CLS',
    'SYS', 'USR', 'AST',
    'TOOL', 'RESULT',
    'PFX', 'SFX', 'MID',
    'RESERVED_15',
]


# ---------------------------------------------------------------------------
# ID encoder
# ---------------------------------------------------------------------------

def _encode_id(tier: int, counter: int) -> str:
    """Encode sequential counter to Base64 ID at given tier (2/3/4 chars)."""
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


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_word_frequencies(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with open(path, 'r', encoding=STREAM_ENCODING) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            count_str, escaped = line.rstrip('\n').split('\t', 1)
            token = escaped.encode('ascii').decode('unicode_escape')
            counts[token] = int(count_str)
    return counts


def load_phrase_candidates(path: Path) -> list[tuple[str, int, int, float]]:
    """
    Returns list of (phrase, n, freq, pmi) tuples sorted by the miner's score.
    """
    records: list[tuple[str, int, int, float]] = []
    with open(path, 'r', encoding=STREAM_ENCODING) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 8:
                continue
            n = int(parts[1])
            freq = int(parts[2])
            pmi = float(parts[3])
            phrase = parts[7]
            records.append((phrase, n, freq, pmi))
    return records


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(
    word_freq_file: Path = WORD_FREQ_FILE,
    phrase_file: Path = PHRASE_FILE,
    lmdb_path: Path = LMDB_PATH,
    stats_file: Path = STATS_FILE,
    map_size_gb: int = MAP_SIZE_GB,
    min_freq: int = MIN_FREQ_FOR_DICT,
) -> dict:
    print('Loading word frequencies...')
    word_freq = load_word_frequencies(word_freq_file)
    total_word_tokens = sum(word_freq.values())
    print(f'  {len(word_freq):,} unique words  |  {total_word_tokens:,} word tokens')

    print('Loading phrase candidates...')
    phrase_records = load_phrase_candidates(phrase_file)
    print(f'  {len(phrase_records):,} phrase candidates')

    # ----------------------------------------------------------------------
    # Tier 0 maps: Words + Structural
    # ----------------------------------------------------------------------
    tier0_map: dict[str, str] = {}
    for char_id, token in WORD_IDS.items():
        tier0_map[token] = char_id
    for char_id, token in STRUCTURAL_IDS.items():
        tier0_map[token] = char_id

    # ----------------------------------------------------------------------
    # Build unified candidate pool: words + phrases (excluding Tier 0)
    # Each entry: (surface, freq, kind, n_words)
    #   kind: 'word' or 'phrase'
    # ----------------------------------------------------------------------
    pool: list[tuple[str, int, str, int]] = []

    # Words (excluding Tier 0 pre-seeded set)
    tier0_set = set(tier0_map.keys())
    for w, f in word_freq.items():
        if w in tier0_set:
            continue
        if f < min_freq:
            continue
        pool.append((w, f, 'word', 1))

    # Phrases (already have freq; filter by min_freq)
    for phrase, n, freq, _pmi in phrase_records:
        if freq < min_freq:
            continue
        # Don't add if collides with an existing word entry (rare)
        if phrase in word_freq:
            continue
        pool.append((phrase, freq, 'phrase', n))

    # Sort unified pool by frequency descending
    pool.sort(key=lambda e: -e[1])

    print(f'  Unified pool: {len(pool):,} entries  '
          f'({sum(1 for _,_,k,_ in pool if k=="word"):,} words + '
          f'{sum(1 for _,_,k,_ in pool if k=="phrase"):,} phrases)')

    # ----------------------------------------------------------------------
    # Open LMDB
    # ----------------------------------------------------------------------
    if lmdb_path.exists():
        # Clean previous build cleanly -- LMDB dir contains data.mdb + lock.mdb
        for p in lmdb_path.iterdir():
            p.unlink()
        lmdb_path.rmdir()
    lmdb_path.mkdir(parents=True, exist_ok=True)

    env = lmdb.open(
        str(lmdb_path),
        map_size=map_size_gb * 1024 ** 3,
        max_dbs=2,
    )
    fwd_db = env.open_db(b'forward')
    rev_db = env.open_db(b'reverse')

    # ----------------------------------------------------------------------
    # Assign IDs:
    #   Tier 0 (string IDs):  pre-seeded from WORD_IDS + STRUCTURAL_IDS
    #   Forced seeds:         '|' -> first Tier 1 slot ('gA')
    #   Tier 1:               next 1,280 - len(FORCED) by frequency
    #   Tier 2:               next 81,920 by frequency
    #   Tier 3:               remaining (capped at TIER_CAPACITY[3])
    #
    # Integer LLM IDs:
    #   Assigned by overall frequency rank (Tier 0 entries inserted at the
    #   top in their frequency order alongside everything else).
    # ----------------------------------------------------------------------

    # Build the full integer-ranked list (Tier 0 entries get inserted at their
    # natural frequency positions among the rest).
    tier0_records: list[tuple[str, int, str, int]] = []
    for char_id, token in tier0_map.items():
        # Pull frequency from word_freq if present (e.g. 'the', 'and')
        # Otherwise use 0 (e.g. '\n' might not appear in word_freq if punctuation-only)
        tier0_records.append((token, word_freq.get(token, 0), 'tier0', 1))

    all_records: list[tuple[str, int, str, int]] = tier0_records + pool
    all_records.sort(key=lambda e: -e[1])

    # ----------------------------------------------------------------------
    # Assign Base64 string IDs (Tier 0 pre-set, Tier 1+ sequential)
    # ----------------------------------------------------------------------
    string_id_of: dict[str, str] = {}
    tier_of: dict[str, int] = {}

    # Tier 0 pre-seeded
    for token, char_id in tier0_map.items():
        string_id_of[token] = char_id
        tier_of[token] = 0

    # Forced seeds (Tier 1 starting slots)
    t1_counter = 0
    forced_assigned: dict[str, str] = {}
    for token in FORCED_DICT_TOKENS:
        if token in tier0_set:
            continue
        token_id = _encode_id(1, t1_counter)
        t1_counter += 1
        string_id_of[token] = token_id
        tier_of[token] = 1
        forced_assigned[token] = token_id

    t2_counter = 0
    t3_counter = 0
    tier_counts = {0: len(tier0_map), 1: len(forced_assigned), 2: 0, 3: 0}
    skipped_overflow = 0

    print('Assigning Tier 1/2/3 string IDs in frequency order...')
    for surface, _freq, kind, _n in tqdm(pool, desc='assign'):
        if surface in string_id_of:
            continue   # already in tier0 (e.g. Tier 0 token also had a frequency)
        if t1_counter < TIER_CAPACITY[1]:
            sid = _encode_id(1, t1_counter)
            t1_counter += 1
            tier_counts[1] += 1
            tier_of[surface] = 1
        elif t2_counter < TIER_CAPACITY[2]:
            sid = _encode_id(2, t2_counter)
            t2_counter += 1
            tier_counts[2] += 1
            tier_of[surface] = 2
        elif t3_counter < TIER_CAPACITY[3]:
            sid = _encode_id(3, t3_counter)
            t3_counter += 1
            tier_counts[3] += 1
            tier_of[surface] = 3
        else:
            skipped_overflow += 1
            continue
        string_id_of[surface] = sid

    # ----------------------------------------------------------------------
    # Write LMDB
    # ----------------------------------------------------------------------
    print('Writing LMDB...')
    with env.begin(write=True) as txn:
        for surface, sid in tqdm(string_id_of.items(), desc='lmdb'):
            txn.put(surface.encode(STREAM_ENCODING), sid.encode(STREAM_ENCODING), db=fwd_db)
            txn.put(sid.encode(STREAM_ENCODING), surface.encode(STREAM_ENCODING), db=rev_db)
    env.close()

    # ----------------------------------------------------------------------
    # Integer ID assignment by overall frequency rank (for LLM use)
    # ----------------------------------------------------------------------
    all_records_in_dict: list[tuple[str, int, str, int]] = [
        (s, f, k, n) for (s, f, k, n) in all_records if s in string_id_of
    ]
    # Re-sort to be explicit; should already be by frequency desc
    all_records_in_dict.sort(key=lambda e: -e[1])

    integer_id_of: dict[str, int] = {}
    for int_id, (surface, _f, _k, _n) in enumerate(all_records_in_dict):
        integer_id_of[surface] = int_id

    # ----------------------------------------------------------------------
    # Profile cuts (rank thresholds)
    # ----------------------------------------------------------------------
    content_total = len(all_records_in_dict)
    profile_cuts = {}
    for name, size in PROFILE_CONTENT_SIZES.items():
        if size is None or size > content_total:
            cut = content_total
        else:
            cut = size
        profile_cuts[name] = {
            'content_size': cut,
            'byte_fallback_start': cut,
            'special_tokens_start': cut + BYTE_FALLBACK_SIZE,
            'total_vocab': cut + BYTE_FALLBACK_SIZE + SPECIAL_TOKEN_COUNT,
        }

    # ----------------------------------------------------------------------
    # Emit profile artifacts
    # ----------------------------------------------------------------------

    # 1. token-ids-v1.csv.gz
    print(f'Writing {TOKEN_IDS_CSV}...')
    TOKEN_IDS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(TOKEN_IDS_CSV, 'wt', encoding=STREAM_ENCODING, newline='') as gz:
        writer = csv.writer(gz)
        writer.writerow(['id', 'base64_id', 'surface', 'tier', 'freq',
                         'kind', 'tiny', 'compact', 'standard', 'full'])
        for int_id, (surface, freq, kind, _n) in enumerate(all_records_in_dict):
            sid = string_id_of[surface]
            tier = tier_of[surface]
            row = [
                int_id,
                sid,
                surface,
                tier,
                freq,
                kind,
                'Y' if int_id < profile_cuts['tiny']['content_size']    else 'N',
                'Y' if int_id < profile_cuts['compact']['content_size'] else 'N',
                'Y' if int_id < profile_cuts['standard']['content_size'] else 'N',
                'Y' if int_id < profile_cuts['full']['content_size']    else 'N',
            ]
            writer.writerow(row)

    # 2. special-tokens-v1.json
    print(f'Writing {SPECIAL_TOK_JSON}...')
    SPECIAL_TOK_JSON.parent.mkdir(parents=True, exist_ok=True)
    special_doc = {
        'count': SPECIAL_TOKEN_COUNT,
        'names': SPECIAL_TOKENS,
        'description': (
            "LLM bookkeeping tokens reserved at the tail of every profile. "
            "Per-profile integer IDs: profile.special_tokens_start + slot_index."
        ),
        'per_profile_ids': {
            name: {
                tok: cut['special_tokens_start'] + i
                for i, tok in enumerate(SPECIAL_TOKENS)
            }
            for name, cut in profile_cuts.items()
        },
    }
    with open(SPECIAL_TOK_JSON, 'w', encoding=STREAM_ENCODING) as f:
        json.dump(special_doc, f, indent=2)

    # 3. byte-fallback-v1.csv
    print(f'Writing {BYTE_FALLBACK_CSV}...')
    with open(BYTE_FALLBACK_CSV, 'w', encoding=STREAM_ENCODING, newline='') as f:
        writer = csv.writer(f)
        header = ['byte_value', 'hex']
        for name in profile_cuts:
            header.append(f'id_in_{name}')
        writer.writerow(header)
        for b in range(BYTE_FALLBACK_SIZE):
            row = [b, f'0x{b:02X}']
            for name, cut in profile_cuts.items():
                row.append(cut['byte_fallback_start'] + b)
            writer.writerow(row)

    # 4. profile-cuts-v1.json
    print(f'Writing {PROFILE_CUTS_JSON}...')
    with open(PROFILE_CUTS_JSON, 'w', encoding=STREAM_ENCODING) as f:
        json.dump(profile_cuts, f, indent=2)

    # ----------------------------------------------------------------------
    # Stats
    # ----------------------------------------------------------------------
    word_count_in_dict = sum(1 for _,_,k,_ in all_records_in_dict if k == 'word')
    phrase_count_in_dict = sum(1 for _,_,k,_ in all_records_in_dict if k == 'phrase')
    tier0_in_dict = sum(1 for _,_,k,_ in all_records_in_dict if k == 'tier0')

    stats = {
        'format_version':           FORMAT_VERSION_V03,
        'source_word_freq_file':    str(word_freq_file),
        'source_phrase_file':       str(phrase_file),
        'lmdb_path':                str(lmdb_path),
        'total_corpus_tokens':      total_word_tokens,
        'unique_words':             len(word_freq),
        'unique_phrase_candidates': len(phrase_records),
        'min_freq_threshold':       min_freq,
        'total_entries':            len(string_id_of),
        'tier0_count':              tier_counts[0],
        'tier1_count':              tier_counts[1],
        'tier2_count':              tier_counts[2],
        'tier3_count':              tier_counts[3],
        'words_in_dict':            word_count_in_dict + tier0_in_dict,
        'phrases_in_dict':          phrase_count_in_dict,
        'tier_capacity':            TIER_CAPACITY,
        'forced_assigned':          forced_assigned,
        'profile_cuts':             profile_cuts,
        'special_token_count':      SPECIAL_TOKEN_COUNT,
        'byte_fallback_size':       BYTE_FALLBACK_SIZE,
        'overflow_skipped':         skipped_overflow,
    }

    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with open(stats_file, 'w', encoding=STREAM_ENCODING) as f:
        json.dump(stats, f, indent=2)

    _print_stats(stats)
    return stats


def _print_stats(s: dict) -> None:
    print()
    print('=== v0.3 Dictionary Build Complete ===')
    print(f"  Format version:           {s['format_version']}")
    print(f"  Total entries:            {s['total_entries']:>10,}")
    print(f"    Tier 0 (1-char):        {s['tier0_count']:>10,}")
    print(f"    Tier 1 (2-char):        {s['tier1_count']:>10,}")
    print(f"    Tier 2 (3-char):        {s['tier2_count']:>10,}")
    print(f"    Tier 3 (4-char):        {s['tier3_count']:>10,}")
    print(f"  Words in dict:            {s['words_in_dict']:>10,}")
    print(f"  Phrases in dict:          {s['phrases_in_dict']:>10,}")
    print(f"  Overflow (dropped):       {s['overflow_skipped']:>10,}")
    print(f"  Forced seeds:             {s['forced_assigned']}")
    print()
    print('  Profile cuts (LLM vocab sizes):')
    for name, cut in s['profile_cuts'].items():
        print(f"    {name:<10}  content={cut['content_size']:>7,}  "
              f"total_vocab={cut['total_vocab']:>7,}")
    print()
    print(f"  LMDB: {s['lmdb_path']}")
    print(f"  Stats: {STATS_FILE}")


# ---------------------------------------------------------------------------
# Spot-check helper
# ---------------------------------------------------------------------------

def spot_check(words: list[str], lmdb_path: Path = LMDB_PATH) -> None:
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=2, lock=False)
    fwd_db = env.open_db(b'forward')
    rev_db = env.open_db(b'reverse')
    print(f"\n{'WORD':<30} {'ID':<8} {'DECODED':<30} {'MATCH'}")
    print('-' * 75)
    with env.begin() as txn:
        for w in words:
            raw_id = txn.get(w.encode(STREAM_ENCODING), db=fwd_db)
            sid = raw_id.decode(STREAM_ENCODING) if raw_id else None
            if sid:
                back = txn.get(sid.encode(STREAM_ENCODING), db=rev_db)
                back = back.decode(STREAM_ENCODING) if back else None
                match = 'OK' if back == w else 'MISMATCH'
            else:
                back, match = None, 'OOV'
            print(f"{w:<30} {str(sid):<8} {str(back):<30} {match}")
    env.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description='Build v0.3 dictionary with phrase atoms')
    p.add_argument('--min', type=int, default=MIN_FREQ_FOR_DICT,
                   help=f'minimum frequency to enter dict (default {MIN_FREQ_FOR_DICT})')
    args = p.parse_args()
    build(min_freq=args.min)


if __name__ == '__main__':
    main()
