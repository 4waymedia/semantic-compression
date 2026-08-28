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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import lmdb
from tqdm import tqdm

sys.path.insert(0, '.')

from config import (
    BASE64_CHARS, STREAM_ENCODING, STRUCTURAL_IDS, TIER_WORD_FIRST_CHARS, WORD_IDS,
)

# v0.3 format version (NEW - distinct from v0.2's FORMAT_VERSION=1)
FORMAT_VERSION_V03 = 3

# Paths
DATA_DIR        = Path('data')
DB_DIR          = Path('db')

WORD_FREQ_FILE  = DATA_DIR / 'word_frequencies.txt'
PHRASE_FILE     = DATA_DIR / 'phrase_candidates.txt'

LMDB_PATH       = DB_DIR / 'dictionary.lmdb'
STATS_FILE      = DB_DIR / 'dict_stats_v03.json'

TOKEN_IDS_CSV   = DATA_DIR / 'token-ids-v1.csv.gz'
SPECIAL_TOK_JSON = DATA_DIR / 'special-tokens-v1.json'
BYTE_FALLBACK_CSV = DATA_DIR / 'byte-fallback-v1.csv'
PROFILE_CUTS_JSON = DATA_DIR / 'profile-cuts-v1.json'

MAP_SIZE_GB = 1
MIN_FREQ_FOR_DICT = 1   # include all words (match v0.2 coverage); phrases are filtered separately

# Forced first-Tier-1 seeds (cannot appear in OOV body)
FORCED_DICT_TOKENS = ['|']

# Compression-aware phrase filter and Tier 1 reservation defaults.
# These convert v0.3's compression gain from +8% to +15-20% by:
#   1. Dropping phrases whose promotion saves zero bytes (2-grams of Tier 0
#      words: 1+1 byte-cost equals the Tier 1 phrase-cost of 2 bytes -- no win)
#   2. Reserving the top N Tier 1 slots exclusively for single words so the
#      most-frequent words keep their 2-byte IDs instead of being demoted to
#      Tier 2 by ambitious phrases.
TIER1_WORD_RESERVE_DEFAULT = 1024     # of 1280 Tier 1 slots, reserve 1024 for words
DROP_TIER0_BIGRAMS_DEFAULT = True     # filter zero-savings 2-grams

# ---------------------------------------------------------------------------
# TIER FIRST CHARS — a BUILD PARAMETER, not a global constant.
#
# The original design encoded the tier in the first character, so each tier needed a
# disjoint character range; `TIER_WORD_FIRST_CHARS = 'g-z'` (20 of 64) is what remains
# of that. The shipped design does not work that way: `detect_tier` uses LENGTH, the
# binary format carries the tier in the top two tag bits with the first char in the low
# six, and the Rust port derives tier from `id.chars().count()`. Verified 2026-07-31
# across config.py, compressor.py, elo.rs and verify_config.py — nothing left standing
# depends on the restriction. See handoffs/HANDOFF-tier-first-char-capacity.md.
#
# Widening to the 63 non-'-' characters multiplies every tier by 3.15x. Re-priced over
# elo-browser-v01b's UNCHANGED ranking, that promotes 2,752 entries into Tier 1 and
# 176,128 into Tier 2, worth 2.7-4.0% of the encoded stream on captured pages.
#
# It is a PARAMETER because changing the global would silently alter every future
# rebuild of v01a/v01b, and those must stay byte-reproducible. Default = the historical
# 20; a build opts in via `tier_first_chars` in its spec.
# ---------------------------------------------------------------------------
TIER_FIRST_CHARS_LEGACY   = TIER_WORD_FIRST_CHARS                 # 'g-z', 20
TIER_FIRST_CHARS_EXPANDED = ''.join(c for c in BASE64_CHARS if c != '-')   # 63

def tier_capacity_for(first_chars: str) -> dict:
    """Tier capacity is len(first_chars) x 64^(n-1)."""
    return {1: len(first_chars) * 64,
            2: len(first_chars) * 64 ** 2,
            3: len(first_chars) * 64 ** 3}

# Module default — the historical 20-char behaviour.
TIER_CAPACITY = tier_capacity_for(TIER_FIRST_CHARS_LEGACY)   # 1,280 / 81,920 / 5,242,880

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

def _encode_id(tier: int, counter: int, first_chars: str = TIER_FIRST_CHARS_LEGACY) -> str:
    """Encode sequential counter to Base64 ID at given tier (2/3/4 chars).

    `first_chars` is the alphabet for the LEADING character only; the trailing
    characters always use the full 64. It is the single point in the build where a
    first character is chosen, so it is also the single lever for tier capacity.
    """
    length = tier + 1
    chars = []
    remaining = counter
    for _ in range(length - 1):
        chars.append(BASE64_CHARS[remaining % 64])
        remaining //= 64
    if remaining >= len(first_chars):
        raise OverflowError(f'Tier {tier} ID space exhausted at counter={counter}')
    chars.append(first_chars[remaining])
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

# Byte-cost constants for the bytes_saved scorer (match phrase_miner.py).
WORD_AVG_BYTES = 1.8     # avg current encoded cost per word (v0.2-binary model)
REF_ID_WIDTH   = 2       # shallowest content ID width (Tier 1, 2 bytes)


# Selection strategies (pure, deterministic scorers). Contract per
# spec-dict-testgroups.md §5:  (surface, freq, kind, n, pmi) -> float.
# The competing pool is ordered by DESCENDING score.
def score_by_frequency(surface, freq, kind, n, pmi=0.0):
    """S0 baseline: raw corpus frequency (ignores surface length)."""
    return float(freq)


def score_by_bytes_saved(surface, freq, kind, n, pmi=0.0):
    """S1: expected bytes removed from the corpus by promoting this surface
    to a short ID. cost_now = utf8 length (words) or n*WORD_AVG_BYTES (phrases);
    first-cut shallowest-tier approximation prices the ID at REF_ID_WIDTH.
    Self-pruning: surfaces with cost_now <= REF_ID_WIDTH score <= 0.
    """
    if kind == 'phrase':
        cost_now = n * WORD_AVG_BYTES
    else:
        cost_now = len(surface.encode('utf-8'))
    return float(freq) * (cost_now - REF_ID_WIDTH)


def build(
    word_freq_file: Path = WORD_FREQ_FILE,
    phrase_file: Path = PHRASE_FILE,
    nonlexical_surfaces: "set[str] | None" = None,
    lmdb_path: Path = LMDB_PATH,
    stats_file: Path | None = None,
    map_size_gb: int = MAP_SIZE_GB,
    min_freq: int = MIN_FREQ_FOR_DICT,
    drop_tier0_bigrams: bool = DROP_TIER0_BIGRAMS_DEFAULT,
    tier1_word_reserve: int = TIER1_WORD_RESERVE_DEFAULT,
    select_strategy: Callable[..., float] = score_by_frequency,
    force_include: "list[str] | None" = None,
    tier_first_chars: str = TIER_FIRST_CHARS_LEGACY,
    max_tier: int = 3,
    token_ids_csv: Path | None = None,
    special_tok_json: Path | None = None,
    byte_fallback_csv: Path | None = None,
    profile_cuts_json: Path | None = None,
) -> dict:
    # ----------------------------------------------------------------------
    # Per-build artifact paths.  Every build writes its OWN stats + profile
    # artifacts so successive builds never clobber each other.  The default
    # *production* build (lmdb_path == LMDB_PATH) keeps writing the versioned
    # contract files under data/ + db/ for backward compatibility; any other
    # output location auto-co-locates its artifacts next to its own LMDB.
    # Explicit path args always win.
    # ----------------------------------------------------------------------
    lmdb_path = Path(lmdb_path)
    _is_default_build = lmdb_path.resolve() == LMDB_PATH.resolve()
    _out = lmdb_path.parent
    if _is_default_build:
        stats_file        = stats_file        or STATS_FILE
        token_ids_csv     = token_ids_csv     or TOKEN_IDS_CSV
        special_tok_json  = special_tok_json  or SPECIAL_TOK_JSON
        byte_fallback_csv = byte_fallback_csv or BYTE_FALLBACK_CSV
        profile_cuts_json = profile_cuts_json or PROFILE_CUTS_JSON
    else:
        stats_file        = stats_file        or _out / 'dict_stats.json'
        token_ids_csv     = token_ids_csv     or _out / 'token-ids.csv.gz'
        special_tok_json  = special_tok_json  or _out / 'special-tokens.json'
        byte_fallback_csv = byte_fallback_csv or _out / 'byte-fallback.csv'
        profile_cuts_json = profile_cuts_json or _out / 'profile-cuts.json'

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
    tier0_set = set(tier0_map.keys())

    # ----------------------------------------------------------------------
    # Phrase filter: drop entries that would save zero bytes when promoted.
    # A 2-gram of two Tier 0 words costs 1+1 = 2 bytes today; a Tier 1
    # phrase ID also costs 2 bytes. Promoting yields ZERO byte savings
    # while taking a Tier 1 slot away from a more valuable single word.
    # ----------------------------------------------------------------------
    filtered_phrases: list[tuple[str, int, int]] = []   # (phrase, freq, n)
    dropped_tier0_bigrams = 0

    for phrase, n, freq, _pmi in phrase_records:
        if freq < min_freq:
            continue
        if phrase in word_freq:
            continue   # collides with existing word entry
        if drop_tier0_bigrams and n == 2:
            words = phrase.split(' ')
            if all(w in tier0_set for w in words):
                dropped_tier0_bigrams += 1
                continue
        filtered_phrases.append((phrase, freq, n))

    print(f'  Phrase filter: dropped {dropped_tier0_bigrams:,} Tier-0 bigrams '
          f'(zero-savings); kept {len(filtered_phrases):,}')

    # ----------------------------------------------------------------------
    # Build non-tier0 word pool
    # ----------------------------------------------------------------------
    # PMI lookup exposed to select_strategy (words have no PMI -> 0.0).
    pmi_of: dict[str, float] = {ph: pm for (ph, _n, _f, pm) in phrase_records}

    word_pool: list[tuple[str, int, str, int]] = []
    for w, f in word_freq.items():
        if w in tier0_set:
            continue
        if f < min_freq:
            continue
        word_pool.append((w, f, 'word', 1))
    word_pool.sort(key=lambda e: -select_strategy(e[0], e[1], e[2], e[3], pmi_of.get(e[0], 0.0)))

    phrase_pool: list[tuple[str, int, str, int]] = [
        (p, f, 'phrase', n) for (p, f, n) in filtered_phrases
    ]
    phrase_pool.sort(key=lambda e: -select_strategy(e[0], e[1], e[2], e[3], pmi_of.get(e[0], 0.0)))

    # ----------------------------------------------------------------------
    # Tier 1 reservation: top tier1_word_reserve WORDS get reserved Tier 1
    # slots BEFORE phrases compete. Remaining Tier 1 slots are filled by
    # frequency-ranked mix of remaining words + filtered phrases.
    # ----------------------------------------------------------------------
    reserved_words = word_pool[:tier1_word_reserve]
    remaining_words = word_pool[tier1_word_reserve:]

    # Pool that competes for tier slots AFTER reserved-Tier-1 words:
    pool: list[tuple[str, int, str, int]] = remaining_words + phrase_pool
    pool.sort(key=lambda e: -select_strategy(e[0], e[1], e[2], e[3], pmi_of.get(e[0], 0.0)))

    word_count_in_pool   = sum(1 for _,_,k,_ in pool if k == 'word')
    phrase_count_in_pool = sum(1 for _,_,k,_ in pool if k == 'phrase')
    print(f'  Tier 1 word reserve: {len(reserved_words):,} top words pre-claimed')
    print(f'  Competing pool: {len(pool):,} entries  '
          f'({word_count_in_pool:,} words + {phrase_count_in_pool:,} phrases)')

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
    for token, char_id in tier0_map.items():
        tier0_records.append((token, word_freq.get(token, 0), 'tier0', 1))

    # ----------------------------------------------------------------------
    # STRUCTURAL FLOOR (force_include). Structural characters are GRAMMAR, not
    # vocabulary: `|`, backtick, `~`, `\r`, `\xa0`, `{`, `}`, `\`, `\t` are rare
    # or absent in a conversational corpus, so pure frequency ranking pushes them
    # past every profile cut (measured on elo-browser-v01a: `{`=276,119,
    # `}`=276,120, `\`=328,675, `\t`=437,988 — all outside the 261,872 `full` cut).
    #
    # That is a correctness problem, not a density one. `|` frames the .elo stream
    # (`ELO|1|txt|tok|tok`); when it is missing from the cut it falls to
    # encode_oov -> `OOV::|` — a token containing a RAW DELIMITER — and decode
    # shatters the frame ("unknown stream token:" with an empty token). It is also
    # the injection primitive: a character that can escape its own encoding can
    # forge token boundaries. Total coverage removes the escape; a blacklist cannot.
    #
    # So forced surfaces sort BEFORE frequency-ranked ones and land in every cut,
    # at any size, regardless of corpus counts. Frequencies are not falsified —
    # only the ordering key is.
    # ----------------------------------------------------------------------
    forced: set[str] = {s for s in (force_include or ()) if s}
    if forced:
        # A FORCED SURFACE THE TOKENIZER CANNOT PRODUCE IS DEAD WEIGHT.
        #
        # force_include guarantees a slot, not a match. The encoder only ever looks up
        # surfaces that `tokenize` emitted, so forcing a string that tokenize splits (or
        # never yields) reserves an id nothing can hit while the real token stays OOV —
        # and force_include_count still reports success.
        #
        # v01b run 1 forced "\r": tokenize("a\r\nb") -> ['a', '\r\n', 'b'], so every
        # CRLF file emitted '\r\n' (OOV, 7 chars) while the forced '\r' sat unused. The
        # count said 10/10. Coverage was 9. Hence: verify, don't trust the count.
        try:
            from semantic_compression.tokenizer import tokenize as _tok
        except Exception:                       # builder used standalone
            _tok = None
        if _tok is not None:
            unreachable = [s for s in sorted(forced) if list(_tok(s)) != [s]]
            if unreachable:
                print(f'  [WARN] {len(unreachable)} forced surface(s) are NOT single '
                      f'tokens — they can never be matched by the encoder:')
                for s in unreachable:
                    print(f'         {s!r}  ->  {[t for t in _tok(s)]}')

        have = {r[0] for r in tier0_records} | {r[0] for r in reserved_words} | {r[0] for r in pool}
        # sorted(): `forced` is a SET, and set iteration order varies per process
        # (hash randomization). Unsorted, equal-frequency injected surfaces got
        # PERMUTED ids between otherwise-identical builds -- measured 2026-08-13:
        # v01c vs v04 differed in exactly 4 pairs ('~', '\r', '\r\n', '\xa0'
        # swapped ids), changing the dictionary fingerprint. Determinism (G3) says
        # same corpus + same spec => same fingerprint; iteration order must be fixed.
        missing = [s for s in sorted(forced) if s not in have]
        for s in missing:                      # absent from the corpus entirely
            pool.append((s, word_freq.get(s, 0), 'word', 1))
        print(f'  Structural floor: {len(forced)} forced surface(s), '
              f'{len(missing)} injected (absent from corpus)')

    all_records: list[tuple[str, int, str, int]] = tier0_records + reserved_words + pool

    # ----------------------------------------------------------------------
    # RANK FLOOR (bug fix, not a tuning choice).
    #
    # Rank is not just an ordering: it determines BOTH the id length (tier) and
    # profile-cut membership. So a Tier-0 primitive ranked by raw corpus frequency
    # can end up OUTSIDE every cut while still holding a 1-char id — an id nothing
    # can reach. Measured on elo-browser-v01a:
    #     '\t' -> id 'i', rank 437,988, in NO cut   (corpus freq 0)
    #     '\\' -> id 'w', rank 328,675, in NO cut   (corpus freq 1)
    # and eight more Tier-0 characters ('* + @ = #' ...) missing from `tiny`.
    # A page using them pays OOV (7 chars) for a character that owns the cheapest
    # id in the system — ~62k wasted chars on one 7.3MB page.
    #
    # Tier 0 is GRAMMAR: 64 hand-assigned primitives that exist precisely because
    # every text needs them. They are not vocabulary competing on frequency, so
    # they sort first and are present in every cut, at any size. Then the declared
    # structural floor (force_include), then frequency-ranked vocabulary.
    #
    # This changes ranks vs pre-2026-07-30 builds. That is intended — the old
    # ordering produced unreachable ids — and `tier0_rank_floor` is recorded in
    # dict_stats so any build declares which convention produced it.
    # ----------------------------------------------------------------------
    def _rank_key(e):
        # Surface as the FINAL tiebreak: equal-frequency entries must order
        # deterministically or ids permute between runs (G3 determinism; see the
        # sorted(forced) note above -- same 2026-08-13 fix).
        surface, freq, kind, _n = e
        if kind == 'tier0':
            return (0, -freq, surface)  # grammar — always inside every cut
        if surface in forced:
            return (1, -freq, surface)  # declared structural floor
        return (2, -freq, surface)      # vocabulary — frequency decides
    all_records.sort(key=_rank_key)

    # ----------------------------------------------------------------------
    # Assign Base64 string IDs (Tier 0 pre-set, Tier 1+ sequential)
    # ----------------------------------------------------------------------
    string_id_of: dict[str, str] = {}
    # Capacity follows the chosen first-char alphabet (see _encode_id).
    TIER_CAP = tier_capacity_for(tier_first_chars)
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
        token_id = _encode_id(1, t1_counter, tier_first_chars)
        t1_counter += 1
        string_id_of[token] = token_id
        tier_of[token] = 1
        forced_assigned[token] = token_id

    t2_counter = 0
    t3_counter = 0
    tier_counts = {0: len(tier0_map), 1: len(forced_assigned), 2: 0, 3: 0}
    skipped_overflow = 0

    def _assign(surface: str) -> None:
        """Place 'surface' in the next available tier slot."""
        nonlocal t1_counter, t2_counter, t3_counter, skipped_overflow
        if max_tier >= 1 and t1_counter < TIER_CAP[1]:
            sid = _encode_id(1, t1_counter, tier_first_chars)
            t1_counter += 1
            tier_counts[1] += 1
            tier_of[surface] = 1
        elif max_tier >= 2 and t2_counter < TIER_CAP[2]:
            sid = _encode_id(2, t2_counter, tier_first_chars)
            t2_counter += 1
            tier_counts[2] += 1
            tier_of[surface] = 2
        elif max_tier >= 3 and t3_counter < TIER_CAP[3]:
            sid = _encode_id(3, t3_counter, tier_first_chars)
            t3_counter += 1
            tier_counts[3] += 1
            tier_of[surface] = 3
        else:
            skipped_overflow += 1
            return
        string_id_of[surface] = sid

    # ----------------------------------------------------------------------
    # HARD RULE (2026-08-10): a size limit NEVER drops words.
    #
    # The select strategy scores and orders every candidate; this partition sits
    # ON TOP of it. When capacity binds, the overflow is taken from NON-LEXICAL
    # surfaces first (tokens that appear ONLY in non-lexical corpus sources --
    # CSS names, JS classes, web-structure tokens; provenance is computed by
    # build_from_spec.resolve_corpus and passed as `nonlexical_surfaces`).
    # Within each class the strategy's order still decides who goes. If the
    # LEXICAL candidates alone exceed capacity, the build FAILS LOUDLY -- raise
    # `size:`/`expand_tiers`, never silently drop a word.
    # ----------------------------------------------------------------------
    if nonlexical_surfaces:
        _cap_left = sum(TIER_CAP[t] for t in (1, 2, 3) if max_tier >= t) - t1_counter
        _to_place = [s for s, _f, _k, _n in pool if s not in string_id_of]
        _to_place_n = len(set(_to_place)) + sum(
            1 for s, _f, _k, _n in reserved_words if s not in string_id_of)
        _overflow = _to_place_n - _cap_left
        evicted_nonlexical = 0
        if _overflow > 0:
            _evict: set[str] = set()
            for s, _f, _k, _n in reversed(pool):            # lowest-scored first
                if len(_evict) >= _overflow:
                    break
                if s in nonlexical_surfaces and s not in string_id_of:
                    _evict.add(s)
            still_over = _overflow - len(_evict)
            if still_over > 0:
                raise SystemExit(
                    f"HARD RULE: capacity short by {_overflow:,} but only "
                    f"{len(_evict):,} non-lexical candidates are evictable -- "
                    f"{still_over:,} WORDS would be dropped. Raise `size:` "
                    f"(char-4) or set `expand_tiers: true`; a dictionary never "
                    f"drops words to fit a limit.")
            pool = [(s, f, k, n) for (s, f, k, n) in pool if s not in _evict]
            evicted_nonlexical = len(_evict)
            print(f'  HARD RULE: capacity short by {_overflow:,} -> evicted '
                  f'{evicted_nonlexical:,} non-lexical surfaces (lowest-scored '
                  f'first); 0 words dropped.')
    else:
        evicted_nonlexical = 0

    # Step A: reserved Tier 1 words first (guaranteed 2-byte IDs)
    print(f'Reserving {len(reserved_words):,} top words for Tier 1...')
    for surface, _freq, _kind, _n in tqdm(reserved_words, desc='reserve'):
        if surface not in string_id_of:
            _assign(surface)

    # Step B: competing pool (remaining words + filtered phrases) in freq order
    print('Assigning remaining Tier 1/2/3 slots in unified frequency order...')
    for surface, _freq, _kind, _n in tqdm(pool, desc='assign'):
        if surface in string_id_of:
            continue
        _assign(surface)

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
    # PRESERVE THE RANKING ORDER — do NOT re-sort by raw frequency here.
    #
    # `all_records` was already ordered by `_rank_key` (tier-0 grammar first, then the
    # declared structural floor, then frequency). A plain `sort(key=-freq)` at this point
    # discards that and re-ranks by corpus count alone, which decides the INTEGER ids and
    # therefore the PROFILE CUTS. The Base64 ids keep the intended order while the cuts
    # get a different one — so a surface can hold a cheap id that no cut can reach.
    #
    # Measured on the first v01b build, before this fix:
    #     '|'  -> base64 'gA' (Tier 1!)  but integer rank 437,989  -> in NO cut
    #     '\t' -> base64 'i'  (Tier 0)   but integer rank 437,988  -> in NO cut
    # i.e. exactly the bug the rank floor exists to prevent, reintroduced two hundred
    # lines later by a "should already be" comment that stopped being true.
    #
    # `all_records` is already in the correct order; filtering preserves it.
    pass

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
    print(f'Writing {token_ids_csv}...')
    token_ids_csv.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(token_ids_csv, 'wt', encoding=STREAM_ENCODING, newline='') as gz:
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
    print(f'Writing {special_tok_json}...')
    special_tok_json.parent.mkdir(parents=True, exist_ok=True)
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
    with open(special_tok_json, 'w', encoding=STREAM_ENCODING) as f:
        json.dump(special_doc, f, indent=2)

    # 3. byte-fallback-v1.csv
    print(f'Writing {byte_fallback_csv}...')
    byte_fallback_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(byte_fallback_csv, 'w', encoding=STREAM_ENCODING, newline='') as f:
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
    print(f'Writing {profile_cuts_json}...')
    profile_cuts_json.parent.mkdir(parents=True, exist_ok=True)
    with open(profile_cuts_json, 'w', encoding=STREAM_ENCODING) as f:
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
        'tier1_word_reserve':       tier1_word_reserve,
        'max_tier':                 max_tier,
        'select_strategy':          getattr(select_strategy, '__name__', str(select_strategy)),
        # recorded so a build can PROVE its structural floor rather than assert it
        'force_include':            sorted(forced),
        'force_include_count':      len(forced),
        # which ranking convention produced this build (absent = pre-2026-07-30 builds,
        # where a Tier-0 primitive could rank outside every profile cut)
        'tier0_rank_floor':         True,
        'drop_tier0_bigrams':       drop_tier0_bigrams,
        'dropped_tier0_bigrams':    dropped_tier0_bigrams,
        'total_entries':            len(string_id_of),
        'tier0_count':              tier_counts[0],
        'tier1_count':              tier_counts[1],
        'tier2_count':              tier_counts[2],
        'tier3_count':              tier_counts[3],
        'words_in_dict':            word_count_in_dict + tier0_in_dict,
        'phrases_in_dict':          phrase_count_in_dict,
        'tier_capacity':            TIER_CAP,
        'tier_first_chars':         tier_first_chars,
        'tier_first_char_count':    len(tier_first_chars),
        'forced_assigned':          forced_assigned,
        'profile_cuts':             profile_cuts,
        'special_token_count':      SPECIAL_TOKEN_COUNT,
        'byte_fallback_size':       BYTE_FALLBACK_SIZE,
        'overflow_skipped':         skipped_overflow,
        # HARD RULE accounting: capacity overflow is taken from non-lexical
        # surfaces first; words are NEVER dropped (the build fails instead).
        'evicted_nonlexical':       evicted_nonlexical,
    }

    stats['stats_file_path'] = str(stats_file)
    stats['artifact_paths'] = {
        'token_ids_csv':     str(token_ids_csv),
        'special_tok_json':  str(special_tok_json),
        'byte_fallback_csv': str(byte_fallback_csv),
        'profile_cuts_json': str(profile_cuts_json),
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
    print(f"  Stats: {s.get('stats_file_path', '')}")

# ---------------------------------------------------------------------------
# Spot-check helper
# ---------------------------------------------------------------------------

def spot_check(words: list[str], lmdb_path: Path = LMDB_PATH) -> None:
    # max_dbs=4 so this coexists with the facets + meta sub-DBs (facet_builder.py).
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=4, lock=False)
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
# Build package (dedicated deliverables folder)
# ---------------------------------------------------------------------------

BUILDS_ROOT = DB_DIR / 'builds'      # parent for unique new-build directories
SIZE_NAME   = {1: 'char-2', 2: 'char-3', 3: 'char-4'}


def _read_build_lock(lmdb_path: Path) -> dict:
    """Return {status, bound_model, fingerprint} from an existing build's meta,
    or {} if there is no readable meta sub-DB. Used to protect locked builds."""
    if not Path(lmdb_path).exists():
        return {}
    try:
        from config import META_DB_NAME
        env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=4, lock=False)
        try:
            meta_db = env.open_db(META_DB_NAME, create=False)
            out = {}
            with env.begin() as txn:
                for k in (b'dictionary_status', b'bound_model', b'dictionary_fingerprint'):
                    v = txn.get(k, db=meta_db)
                    if v is not None:
                        out[k.decode()] = v.decode('utf-8')
            return out
        finally:
            env.close()
    except Exception:
        return {}


def build_package(out_dir: Path, *, overwrite: bool = False, force: bool = False,
                  tier_first_chars: str = TIER_FIRST_CHARS_LEGACY,
                  max_tier: int = 3, min_freq: int = MIN_FREQ_FOR_DICT,
                  tier1_word_reserve: int = TIER1_WORD_RESERVE_DEFAULT,
                  drop_tier0_bigrams: bool = DROP_TIER0_BIGRAMS_DEFAULT,
                  select_strategy: Callable = score_by_frequency,
                  force_include: "list[str] | None" = None,
                  with_facets: bool = False,
                  overrides: str = 'data/facet_overrides.tsv',
                  word_freq_file: Path | None = None,
                  phrase_file: Path | None = None,
                  nonlexical_file: Path | None = None,
                  extra_manifest: dict | None = None) -> dict:
    """Build a self-contained dictionary deliverables package into out_dir:
    dictionary.lmdb + dict_stats.json + token-ids.csv.gz + special-tokens.json
    + byte-fallback.csv + profile-cuts.json (+ facets in-LMDB + facets_stats.json
    when with_facets) + manifest.json.

    overwrite=False : refuse if out_dir already exists (new build -> use a unique dir).
    overwrite=True  : rebuild in place, BUT refuse if the existing build is
                      status=locked (bound to an LLM retrain) unless force=True.
    """
    out_dir = Path(out_dir)
    lmdb_path = out_dir / 'dictionary.lmdb'

    if out_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"{out_dir} already exists. Use a unique --new-build dir, or pass "
                f"--build-dir with --overwrite to rebuild in place.")
        lock = _read_build_lock(lmdb_path)
        if lock.get('dictionary_status') == 'locked' and not force:
            raise PermissionError(
                f"REFUSING to overwrite {out_dir}: dictionary is LOCKED to an LLM "
                f"retrain (bound_model={lock.get('bound_model', '?')}, "
                f"fingerprint={lock.get('dictionary_fingerprint', '?')[:16]}...). "
                f"A locked dictionary is paired with a trained model and must not "
                f"change. Pass force=True only if you are intentionally breaking "
                f"that pairing.")

    out_dir.mkdir(parents=True, exist_ok=True)
    stats = build(lmdb_path=lmdb_path, max_tier=max_tier, min_freq=min_freq,
                  tier1_word_reserve=tier1_word_reserve,
                  drop_tier0_bigrams=drop_tier0_bigrams,
                  select_strategy=select_strategy,
                  force_include=force_include,             # structural floor (slots spec §3)
                  tier_first_chars=tier_first_chars,       # tier capacity (HANDOFF-tier-first-char-capacity)
                  word_freq_file=word_freq_file or WORD_FREQ_FILE,
                  phrase_file=phrase_file or PHRASE_FILE,
                  # HARD RULE: surfaces present ONLY in non-lexical corpus sources
                  # (CSS/JS/web-structure) -- evicted first under a size limit;
                  # words are never dropped (build fails instead).
                  nonlexical_surfaces=(
                      {ln.split('\t')[-1].strip() for ln in
                       Path(nonlexical_file).read_text(encoding='utf-8').splitlines()
                       if ln.strip() and not ln.startswith('#')}
                      if nonlexical_file and Path(nonlexical_file).exists() else None))

    facets_done = False
    if with_facets:
        import subprocess
        _here = Path(__file__).resolve().parent
        _ov = Path(overrides)
        if not _ov.is_absolute():
            _ov = _here / overrides
        subprocess.run([sys.executable, str(_here / 'facet_builder.py'),
                        '--db', str(Path(lmdb_path).resolve()),
                        '--overrides', str(_ov),
                        '--stats', str((out_dir / 'facets_stats.json').resolve())],
                       cwd=str(_here), check=True)
        facets_done = True

    manifest = {
        'build_dir':        str(out_dir),
        'created_utc':      datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'size':             SIZE_NAME.get(max_tier, f'tier{max_tier}'),
        'max_tier':         max_tier,
        'select_strategy':  getattr(select_strategy, '__name__', str(select_strategy)),
        'min_freq':         min_freq,
        'tier1_word_reserve': tier1_word_reserve,
        'entries':          stats['total_entries'],
        'tier_counts':      [stats['tier0_count'], stats['tier1_count'],
                             stats['tier2_count'], stats['tier3_count']],
        'with_facets':      facets_done,
        # Lifecycle: a fresh build is always 'staged'. Promote with stamp_meta.py
        # (staged -> frozen -> locked). 'locked' = bound to an LLM retrain.
        'dictionary_status': 'staged',
        'bound_model':      None,
        'deliverables':     sorted(p.name for p in out_dir.iterdir() if p.is_file()),
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2),
                                           encoding=STREAM_ENCODING)
    print(f"\n[PACKAGE] {out_dir}  ({manifest['size']}, "
          f"{stats['total_entries']:,} entries, facets={facets_done}, status=staged)")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description='Build v0.3 dictionary with phrase atoms')
    p.add_argument('--min', type=int, default=MIN_FREQ_FOR_DICT,
                   help=f'minimum frequency to enter dict (default {MIN_FREQ_FOR_DICT})')
    p.add_argument('--tier1-reserve', type=int, default=TIER1_WORD_RESERVE_DEFAULT,
                   help=f'number of top Tier-1 slots reserved for single words '
                        f'(default {TIER1_WORD_RESERVE_DEFAULT})')
    p.add_argument('--keep-tier0-bigrams', action='store_true',
                   help='retain 2-grams of Tier-0 words even though they save '
                        'zero bytes (off by default)')
    p.add_argument('--max-tier', type=int, default=3, choices=(1, 2, 3),
                   help='build depth / size: 1=char-2, 2=char-3, 3=char-4 '
                        '(default 3, behavior-identical to v0.3)')
    p.add_argument('--with-facets', action='store_true',
                   help='derive the facets layer after the build '
                        '(runs facet_builder.py on the new LMDB)')
    # ---- build-package output modes (mutually exclusive) ----
    out = p.add_mutually_exclusive_group()
    out.add_argument('--build-dir', metavar='PATH',
                     help='write/rebuild a self-contained build package at PATH. '
                          'With --overwrite, rebuilds in place (refused if the '
                          'existing build is locked to an LLM retrain).')
    out.add_argument('--new-build', nargs='?', const='build', metavar='NAME',
                     help='create a NEW build package in a unique directory '
                          'db/builds/<NAME>_<UTC-timestamp>/ (never clobbers).')
    p.add_argument('--overwrite', action='store_true',
                   help='with --build-dir: rebuild in place over an existing package')
    p.add_argument('--force', action='store_true',
                   help='with --overwrite: override the LLM-retrain lock guard '
                        '(intentionally break a dictionary<->model pairing)')
    args = p.parse_args()

    common = dict(max_tier=args.max_tier, min_freq=args.min,
                  tier1_word_reserve=args.tier1_reserve,
                  drop_tier0_bigrams=not args.keep_tier0_bigrams,
                  with_facets=args.with_facets)

    if args.build_dir:
        build_package(Path(args.build_dir), overwrite=args.overwrite,
                      force=args.force, **common)
    elif args.new_build is not None:
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        build_package(BUILDS_ROOT / f'{args.new_build}_{ts}',
                      overwrite=False, **common)
    else:
        # Default: production contract build (data/*-v1 + db/dict_stats_v03.json),
        # behavior-preserving for the existing pipeline.
        build(min_freq=args.min, tier1_word_reserve=args.tier1_reserve,
              drop_tier0_bigrams=not args.keep_tier0_bigrams, max_tier=args.max_tier)
        if args.with_facets:
            import subprocess
            subprocess.run([sys.executable, 'facet_builder.py',
                            '--db', str(LMDB_PATH)], check=True)


if __name__ == '__main__':
    main()
