"""
epa_phrase_composer.py
EloAI — O-EPA4: Phrase EPA composition

Extends `epa_substrate.lmdb` with composed EPA vectors for multi-word
dictionary entries that have no Warriner/NRC-VAD rating.

Algorithm:
  For each surface in dictionary.lmdb b'forward' not already in b'epa':
    1. Tokenize on whitespace and hyphens.
    2. Strip stopwords; keep alpha content tokens.
    3. Look up each content token in the existing EPA substrate.
    4. If ≥1 rated token: write mean EPA (±4 scale) to b'epa'.
       - STRONG (≥2 rated tokens): weight = 1.0  (full mean)
       - WEAK   (1 rated token):   weight = 0.8  (attenuated — single-word signal)
    5. Single-word entries without EPA are left unchanged (no composition
       possible; they remain neutral at query time via get_with_source).

Output:
  Mutates epa_substrate.lmdb in-place (adds composed entries to b'epa').
  Prints a stats report; saves epa_compose_stats.json.

Usage:
    python epa_phrase_composer.py
    python epa_phrase_composer.py --dry-run        # count without writing
    python epa_phrase_composer.py --min-rated 2    # strong-only (≥2 rated tokens)
    python epa_phrase_composer.py --rebuild-faiss  # rebuild FAISS after composing
"""

from __future__ import annotations

import argparse
import json
import re
import re
import struct
import sys
import time
from pathlib import Path
from typing import Optional

import lmdb

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE    = Path(__file__).parent
DICT_DB  = _HERE / 'db' / 'dictionary.lmdb'
EPA_DB   = _HERE / '..' / 'Memory' / 'data' / 'epa_substrate.lmdb'
STATS_OUT = _HERE / 'db' / 'epa_compose_stats.json'

# ---------------------------------------------------------------------------
# Stopwords
# ---------------------------------------------------------------------------

STOPWORDS: frozenset[str] = frozenset({
    'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or',
    'but', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'that', 'this',
    'it', 'its', 'with', 'by', 'as', 'from', 'into', 'through', 'during',
    'before', 'after', 'above', 'below', 'up', 'down', 'out', 'off', 'over',
    'under', 'not', 'no', 'nor', 'so', 'yet', 'both', 'either', 'neither',
    'each', 'few', 'more', 'most', 'other', 'some', 'such', 'than', 'too',
    'very', 'my', 'your', 'his', 'her', 'our', 'their', 'me', 'him', 'us',
    'them', 'who', 'what', 'where', 'when', 'how', 'why', 'which', 'all',
    'any', 'about', 'also', 'just', 'even', 'only', 'still', 'then', 'well',
    'now', 'here', 'there', 'these', 'those', 'they', 'we', 'he', 'she', 'i',
    'do', 'did', 'does', 'have', 'has', 'had', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'get', 'got',
    'go', 'going', 'like', 'want', 'know', 'think', 'see', 'come', 'make',
    'take', 'give', 'say', 'tell', 'let', 'put', 'use', 'one', 'two', 'three',
})


# Phrases must be lowercase-alpha (no digits, no special chars beyond space/hyphen/apostrophe)
_ALPHA_PHRASE = re.compile(r"^[a-z][a-z '\-]*[a-z]$")

# Weight applied when only 1 content token has EPA (head-word proxy)
WEAK_WEIGHT: float = 0.8


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _tokenize(surface: str) -> list[str]:
    """Split on whitespace and hyphens; return lowercased alpha tokens."""
    return [t for t in re.split(r'[\s\-]+', surface.lower()) if t.isalpha()]


def _content_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in STOPWORDS]


def _compose_epa(
    surface: str,
    epa_map: dict[str, tuple[float, float, float]],
    min_rated: int = 1,
    min_content: int = 2,
) -> Optional[tuple[float, float, float, str]]:
    """
    Compute composed EPA for *surface*.

    Returns (E, P, A, tier) where tier is 'strong' | 'weak', or None if
    the entry cannot be composed.
    """
    # Alpha-only, 2-6 tokens
    if not _ALPHA_PHRASE.match(surface.lower()):
        return None
    tokens   = _tokenize(surface)
    if not (2 <= len(tokens) <= 6):
        return None
    content  = _content_tokens(tokens)
    if len(content) < min_content:
        return None

    rated = [(t, epa_map[f'en|{t}']) for t in content if f'en|{t}' in epa_map]
    if len(rated) < min_rated:
        return None

    # Mean EPA over rated tokens
    e = sum(v[0] for _, v in rated) / len(rated)
    p = sum(v[1] for _, v in rated) / len(rated)
    a = sum(v[2] for _, v in rated) / len(rated)

    tier = 'strong' if len(rated) >= 2 else 'weak'
    if tier == 'weak':
        e *= WEAK_WEIGHT
        p *= WEAK_WEIGHT
        a *= WEAK_WEIGHT

    return e, p, a, tier


def compose(
    min_rated: int = 1,
    min_content: int = 2,
    dry_run: bool = False,
    rebuild_faiss: bool = False,
    verbose: bool = False,
) -> dict:
    """
    Main entry point.

    Returns stats dict.
    """
    t0 = time.perf_counter()

    # ---- load existing EPA map ------------------------------------------
    epa_env  = lmdb.open(str(EPA_DB), max_dbs=5, readonly=True)
    epa_db_h = epa_env.open_db(b'epa')
    epa_map: dict[str, tuple[float, float, float]] = {}
    with epa_env.begin(db=epa_db_h) as txn:
        cur = txn.cursor()
        cur.first()
        while True:
            k = bytes(cur.key()).decode('utf-8')
            v = bytes(cur.value())
            if len(v) == 12:
                epa_map[k] = struct.unpack('<fff', v)
            if not cur.next():
                break
    epa_env.close()
    print(f'  existing EPA entries: {len(epa_map):,}')

    # ---- iterate dictionary forward db ----------------------------------
    dict_env = lmdb.open(str(DICT_DB), readonly=True, max_dbs=10)
    fwd_db   = dict_env.open_db(b'forward')

    to_write: dict[str, tuple[float, float, float]] = {}   # key → (E, P, A)
    n_total  = 0
    n_skipped_existing = 0
    n_skipped_single   = 0
    n_skipped_nocompose= 0
    n_strong = 0
    n_weak   = 0

    with dict_env.begin() as txn:
        cur = txn.cursor(db=fwd_db)
        cur.first()
        while True:
            surface = bytes(cur.key()).decode('utf-8', errors='replace')
            n_total += 1

            epa_key = f'en|{surface.lower()}'
            if epa_key in epa_map:
                n_skipped_existing += 1
            elif ' ' not in surface and '-' not in surface:
                n_skipped_single += 1
            else:
                result = _compose_epa(surface, epa_map, min_rated=min_rated, min_content=min_content)
                if result is None:
                    n_skipped_nocompose += 1
                else:
                    e, p, a, tier = result
                    to_write[epa_key] = (e, p, a)
                    if tier == 'strong':
                        n_strong += 1
                    else:
                        n_weak += 1
                    if verbose and len(to_write) <= 10:
                        print(f'  {surface!r:40s}  E={e:+.2f} P={p:+.2f} A={a:+.2f}  [{tier}]')

            if not cur.next():
                break

    dict_env.close()

    n_composed = len(to_write)
    print(f'  dictionary scanned: {n_total:,}')
    print(f'  already rated:      {n_skipped_existing:,}')
    print(f'  single-word miss:   {n_skipped_single:,}')
    print(f'  no composable:      {n_skipped_nocompose:,}')
    print(f'  to compose — strong:{n_strong:,}  weak:{n_weak:,}  total:{n_composed:,}')

    # ---- write -----------------------------------------------------------
    if not dry_run and to_write:
        epa_env_w = lmdb.open(
            str(EPA_DB), max_dbs=5, readonly=False,
            map_size=256 * 1024 * 1024,  # 256 MB headroom
        )
        epa_db_w = epa_env_w.open_db(b'epa')
        BATCH = 5_000
        keys = list(to_write)
        written = 0
        for i in range(0, len(keys), BATCH):
            batch = keys[i:i + BATCH]
            with epa_env_w.begin(db=epa_db_w, write=True) as txn:
                for key in batch:
                    e, p, a = to_write[key]
                    txn.put(key.encode('utf-8'), struct.pack('<fff', e, p, a))
                    written += 1
            if verbose:
                print(f'  wrote {written:,}/{n_composed:,}...', end='\r')
        epa_env_w.close()
        print(f'\n  written to epa_substrate.lmdb: {written:,}')

    elapsed = time.perf_counter() - t0

    stats = {
        'existing_epa':         len(epa_map),
        'dictionary_total':     n_total,
        'skipped_existing':     n_skipped_existing,
        'skipped_single_miss':  n_skipped_single,
        'skipped_no_compose':   n_skipped_nocompose,
        'composed_strong':      n_strong,
        'composed_weak':        n_weak,
        'composed_total':       n_composed,
        'new_total_epa':        len(epa_map) + (n_composed if not dry_run else 0),
        'coverage_pct':         round(100 * (len(epa_map) + n_composed) / max(n_total, 1), 2),
        'dry_run':              dry_run,
        'elapsed_s':            round(elapsed, 2),
    }

    if not dry_run:
        STATS_OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(STATS_OUT, 'w') as f:
            json.dump(stats, f, indent=2)
        print(f'  stats → {STATS_OUT}')

    # ---- rebuild FAISS --------------------------------------------------
    if rebuild_faiss and not dry_run:
        print('\nRebuilding FAISS index...')
        import faiss_builder  # type: ignore
        result = faiss_builder.build_faiss_index()
        print(f'  FAISS rebuilt: {result["entry_count"]:,} vectors')
        stats['faiss_rebuilt'] = result

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description='O-EPA4 phrase EPA composer')
    ap.add_argument('--dry-run',       action='store_true',
                    help='Count composable entries without writing')
    ap.add_argument('--min-rated',     type=int, default=1,
                    help='Minimum rated content tokens (1=include weak, 2=strong only)')
    ap.add_argument('--min-content', type=int, default=2,
                    help='Minimum content tokens required (default 2)')
    ap.add_argument('--rebuild-faiss', action='store_true',
                    help='Rebuild FAISS index after composing')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    print(f'epa_phrase_composer  min_rated={args.min_rated}  min_content={args.min_content}  '
          f'dry_run={args.dry_run}  rebuild_faiss={args.rebuild_faiss}')

    stats = compose(
        min_rated=args.min_rated,
        min_content=args.min_content,
        dry_run=args.dry_run,
        rebuild_faiss=args.rebuild_faiss,
        verbose=args.verbose,
    )

    print(f'\n--- Results ---')
    print(f'  composed:    {stats["composed_total"]:,}  '
          f'(strong={stats["composed_strong"]:,}  weak={stats["composed_weak"]:,})')
    print(f'  new EPA total: {stats["new_total_epa"]:,}  '
          f'({stats["coverage_pct"]:.1f}% of dictionary)')
    print(f'  elapsed:     {stats["elapsed_s"]:.1f}s')
    if args.dry_run:
        print('  [DRY RUN — nothing written]')
    else:
        print('  ✓ epa_substrate.lmdb updated')


if __name__ == '__main__':
    main()
