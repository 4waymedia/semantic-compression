"""
vfacet_builder.py
EloAI — Verbalizer Facet Pass

Adds b'vfacets' sub-database to an existing dictionary.lmdb.
Reads EPA values from a separate epa_substrate.lmdb (key: b'en|surface').

Record format: 2 bytes, struct '<BB'
  Byte 0:  [7:6] agency  [5:3] directionality  [2:0] temporality
  Byte 1:  [7:4] domain  [3:2] polarity         [1:0] reserved

  polarity    — derived from EPA.E threshold (free, no LLM)
  temporality — suffix heuristics (deterministic, no LLM)
  domain      — corpus word-list membership (deterministic, no LLM)
  agency      — UNKNOWN on first pass; patched by vfacet_llm.py
  direction   — UNKNOWN on first pass; patched by vfacet_llm.py

Usage:
    python vfacet_builder.py
    python vfacet_builder.py --db PATH --epa-db PATH
    python vfacet_builder.py --dry-run
"""
from __future__ import annotations

import argparse
import struct
import time
from collections import Counter
from pathlib import Path

import lmdb

# ---------------------------------------------------------------------------
# Record layout
# ---------------------------------------------------------------------------

AGENCY    = {'UNKNOWN': 0b00, 'SELF': 0b01, 'OTHER': 0b10, 'SYSTEM': 0b11}
DIRECTION = {'UNKNOWN': 0b000, 'TOWARD': 0b001, 'AWAY': 0b010,
             'STABLE': 0b011, 'REVERSAL': 0b100, 'NEUTRAL': 0b101}
TEMPORAL  = {'UNKNOWN': 0b000, 'STATE': 0b001, 'PROCESS': 0b010,
             'EVENT': 0b011, 'OUTCOME': 0b100, 'CONDITION': 0b101}
DOMAIN    = {'GENERAL': 0x0, 'CODING': 0x1, 'MEDICAL': 0x2, 'LEGAL': 0x3,
             'FINANCE': 0x4, 'SCIENCE': 0x5, 'PSYCHOLOGY': 0x6,
             'MILITARY': 0x7, 'CULINARY': 0x8, 'EDUCATION': 0x9}
POLARITY  = {'NEUTRAL': 0b00, 'POSITIVE': 0b01, 'NEGATIVE': 0b10, 'BIPOLAR': 0b11}

AGENCY_SHIFT    = 6;  AGENCY_MASK    = 0b11000000
DIRECTION_SHIFT = 3;  DIRECTION_MASK = 0b00111000
TEMPORAL_SHIFT  = 0;  TEMPORAL_MASK  = 0b00000111
DOMAIN_SHIFT    = 4;  DOMAIN_MASK    = 0xF0
POLARITY_SHIFT  = 2;  POLARITY_MASK  = 0b00001100

_POL_POS = 0.8
_POL_NEG = -0.8

VFACETS_DB = b'vfacets'
_EPA_STRUCT = struct.Struct('<fff')


def pack_vfacet(agency, direction, temporal, domain, polarity) -> bytes:
    b0 = ((agency    << AGENCY_SHIFT)    & AGENCY_MASK) | \
         ((direction  << DIRECTION_SHIFT) & DIRECTION_MASK) | \
         ((temporal   << TEMPORAL_SHIFT)  & TEMPORAL_MASK)
    b1 = ((domain    << DOMAIN_SHIFT)    & DOMAIN_MASK) | \
         ((polarity   << POLARITY_SHIFT)  & POLARITY_MASK)
    return struct.pack('<BB', b0, b1)


def unpack_vfacet(value: bytes) -> dict:
    b0, b1 = struct.unpack('<BB', value)
    return {
        'agency':    (b0 & AGENCY_MASK)    >> AGENCY_SHIFT,
        'direction': (b0 & DIRECTION_MASK) >> DIRECTION_SHIFT,
        'temporal':  (b0 & TEMPORAL_MASK)  >> TEMPORAL_SHIFT,
        'domain':    (b1 & DOMAIN_MASK)    >> DOMAIN_SHIFT,
        'polarity':  (b1 & POLARITY_MASK)  >> POLARITY_SHIFT,
    }


# ---------------------------------------------------------------------------
# Temporality: suffix heuristics
# ---------------------------------------------------------------------------

_CONDITION_WORDS = frozenset({
    'if', 'unless', 'until', 'provided', 'assuming', 'conditional',
    'hypothetical', 'whenever', 'given', 'whether',
})
_EVENT_WORDS = frozenset({
    'event', 'incident', 'occurrence', 'arrival', 'departure', 'crash',
    'launch', 'release', 'attack', 'collapse', 'explosion', 'death',
    'birth', 'election', 'meeting', 'announcement', 'discovery',
})
_PROCESS_SUFFIXES = ('ing',)
_OUTCOME_SUFFIXES = ('ed', 'tion', 'sion', 'ment', 'ance', 'ence', 'al')
_STATE_SUFFIXES   = ('ness', 'ity', 'ism', 'ship', 'hood', 'dom')


def _classify_temporal(surface: str) -> int:
    parts = surface.lower().split()
    if not parts:
        return TEMPORAL['UNKNOWN']
    w = parts[-1] if ' ' in surface else parts[0]
    if w in _CONDITION_WORDS: return TEMPORAL['CONDITION']
    if w in _EVENT_WORDS:     return TEMPORAL['EVENT']
    for s in _PROCESS_SUFFIXES:
        if w.endswith(s) and len(w) > len(s) + 2: return TEMPORAL['PROCESS']
    for s in _OUTCOME_SUFFIXES:
        if w.endswith(s) and len(w) > len(s) + 2: return TEMPORAL['OUTCOME']
    for s in _STATE_SUFFIXES:
        if w.endswith(s) and len(w) > len(s) + 2: return TEMPORAL['STATE']
    return TEMPORAL['UNKNOWN']


# ---------------------------------------------------------------------------
# Domain: word-list membership
# ---------------------------------------------------------------------------

_DOMAIN_LISTS: dict[str, frozenset] = {
    'CODING': frozenset({
        'function', 'variable', 'array', 'loop', 'class', 'object', 'method',
        'module', 'package', 'library', 'api', 'endpoint', 'database', 'query',
        'schema', 'server', 'client', 'request', 'response', 'token', 'parse',
        'compile', 'runtime', 'stack', 'heap', 'pointer', 'thread', 'async',
        'callback', 'interface', 'algorithm', 'recursion', 'iteration', 'syntax',
        'debug', 'deploy', 'pipeline', 'repository', 'commit', 'branch', 'merge',
        'boolean', 'integer', 'float', 'null', 'exception', 'cache', 'latency',
    }),
    'MEDICAL': frozenset({
        'diagnosis', 'symptom', 'treatment', 'therapy', 'surgery', 'medication',
        'prescription', 'dosage', 'patient', 'physician', 'clinical', 'chronic',
        'acute', 'benign', 'malignant', 'tumor', 'infection', 'inflammation',
        'immune', 'antibody', 'vaccine', 'prognosis', 'pathology', 'anatomy',
        'physiology', 'neurology', 'cardiology', 'oncology', 'biopsy',
    }),
    'LEGAL': frozenset({
        'statute', 'jurisdiction', 'plaintiff', 'defendant', 'verdict',
        'testimony', 'prosecution', 'defense', 'appeal', 'legislation',
        'regulation', 'compliance', 'liability', 'tort', 'contract',
        'amendment', 'constitutional', 'precedent', 'injunction', 'indictment',
    }),
    'FINANCE': frozenset({
        'equity', 'dividend', 'portfolio', 'liquidity', 'volatility',
        'derivative', 'hedge', 'arbitrage', 'leverage', 'collateral',
        'amortization', 'depreciation', 'valuation', 'yield', 'treasury',
        'deficit', 'inflation', 'recession', 'gdp', 'monetary', 'fiscal',
    }),
    'SCIENCE': frozenset({
        'hypothesis', 'experiment', 'variable', 'particle', 'quantum',
        'molecule', 'atom', 'electron', 'protein', 'genome', 'ecosystem',
        'entropy', 'wavelength', 'frequency', 'velocity', 'acceleration',
        'empirical', 'methodology', 'replication', 'falsifiable',
    }),
    'PSYCHOLOGY': frozenset({
        'cognition', 'perception', 'memory', 'emotion', 'motivation',
        'behavior', 'personality', 'anxiety', 'depression', 'trauma',
        'cognitive', 'behavioral', 'unconscious', 'attachment', 'resilience',
        'empathy', 'narcissism', 'reinforcement', 'conditioning', 'heuristic',
        'bias', 'attribution',
    }),
}

_DOMAIN_LOOKUP: dict[str, int] = {}
for _dname, _words in _DOMAIN_LISTS.items():
    _code = DOMAIN[_dname]
    for _w in _words:
        if _w not in _DOMAIN_LOOKUP:
            _DOMAIN_LOOKUP[_w] = _code


def _classify_domain(surface: str) -> int:
    key  = surface.lower().strip()
    last = key.split()[-1] if ' ' in key else key
    return _DOMAIN_LOOKUP.get(last) or _DOMAIN_LOOKUP.get(key) or DOMAIN['GENERAL']


# ---------------------------------------------------------------------------
# Polarity from EPA.E
# ---------------------------------------------------------------------------

def _polarity_from_e(e: float) -> int:
    if e > _POL_POS:  return POLARITY['POSITIVE']
    if e < _POL_NEG:  return POLARITY['NEGATIVE']
    return POLARITY['NEUTRAL']


# ---------------------------------------------------------------------------
# Load EPA lookup: en|surface -> (E, P, A)
# ---------------------------------------------------------------------------

def _load_epa_lookup(epa_lmdb_path: Path) -> dict[str, tuple[float, float, float]]:
    """Load all EPA entries keyed by surface (strips 'en|' prefix)."""
    lookup: dict[str, tuple[float, float, float]] = {}
    env = lmdb.open(str(epa_lmdb_path), max_dbs=5, readonly=True)
    with env.begin() as txn:
        db = env.open_db(b'epa', txn=txn)
        cur = txn.cursor(db=db)
        for k, v in cur.iternext():
            try:
                raw = k.decode('utf-8')
                surface = raw[3:] if raw.startswith('en|') else raw
                e, p, a = _EPA_STRUCT.unpack(v)
                lookup[surface] = (e, p, a)
            except Exception:
                continue
    env.close()
    return lookup


# ---------------------------------------------------------------------------
# Main build pass
# ---------------------------------------------------------------------------

DEFAULT_DB     = Path('db/dictionary.lmdb')
DEFAULT_EPA_DB = Path('../Memory/data/epa_substrate.lmdb')


def build_vfacets(
    lmdb_path: Path = DEFAULT_DB,
    epa_lmdb_path: Path = DEFAULT_EPA_DB,
    dry_run: bool = False,
) -> dict:
    lmdb_path = Path(lmdb_path)
    epa_lmdb_path = Path(epa_lmdb_path)

    if not lmdb_path.exists():
        raise FileNotFoundError(f'Dictionary LMDB not found: {lmdb_path}')

    # Load EPA lookup (surface → E,P,A)
    epa_lookup: dict[str, tuple] = {}
    if epa_lmdb_path.exists():
        print(f'  Loading EPA from {epa_lmdb_path}...')
        epa_lookup = _load_epa_lookup(epa_lmdb_path)
        print(f'  EPA entries loaded: {len(epa_lookup):,}')
    else:
        print(f'  WARNING: EPA lmdb not found at {epa_lmdb_path} — polarity will be NEUTRAL')

    # Open dictionary
    env = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=6,
                    readonly=False)

    # Collect surfaces
    surfaces: list[tuple[bytes, str]] = []  # (id_bytes, surface)
    with env.begin() as txn:
        db_fwd = env.open_db(b'forward', txn=txn)
        cur = txn.cursor(db=db_fwd)
        for surface_bytes, id_bytes in cur.iternext():
            surfaces.append((id_bytes, surface_bytes.decode('utf-8', errors='replace')))

    stats: Counter = Counter()
    stats['total_entries'] = len(surfaces)
    t0 = time.perf_counter()

    if not dry_run:
        with env.begin(write=True) as txn:
            db_vf = env.open_db(VFACETS_DB, txn=txn, create=True)
            for id_bytes, surface in surfaces:
                epa = epa_lookup.get(surface)
                if epa:
                    polarity = _polarity_from_e(epa[0])
                    stats['epa_hit'] += 1
                else:
                    polarity = POLARITY['NEUTRAL']
                    stats['epa_miss'] += 1

                temporal  = _classify_temporal(surface)
                domain    = _classify_domain(surface)
                agency    = AGENCY['UNKNOWN']
                direction = DIRECTION['UNKNOWN']

                txn.put(id_bytes, pack_vfacet(agency, direction, temporal, domain, polarity),
                        db=db_vf)
                stats['written'] += 1
                stats[f't_{temporal}'] += 1
                stats[f'd_{domain}']   += 1
                stats[f'p_{polarity}'] += 1
    else:
        for _, surface in surfaces:
            temporal = _classify_temporal(surface)
            domain   = _classify_domain(surface)
            epa      = epa_lookup.get(surface)
            polarity = _polarity_from_e(epa[0]) if epa else POLARITY['NEUTRAL']
            stats[f't_{temporal}'] += 1
            stats[f'd_{domain}']   += 1
            stats[f'p_{polarity}'] += 1

    stats['elapsed_s'] = round(time.perf_counter() - t0, 3)
    env.close()
    return dict(stats)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db',     default=str(DEFAULT_DB))
    ap.add_argument('--epa-db', default=str(DEFAULT_EPA_DB))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    tag = 'DRY RUN — ' if args.dry_run else ''
    print(f'[vfacet_builder] {tag}db={args.db}')
    stats = build_vfacets(Path(args.db), Path(args.epa_db), dry_run=args.dry_run)

    print(f"  entries : {stats['total_entries']:,}")
    if not args.dry_run:
        print(f"  written : {stats['written']:,}")
        print(f"  epa hit : {stats.get('epa_hit',0):,}")
        print(f"  epa miss: {stats.get('epa_miss',0):,}")
    print(f"  elapsed : {stats['elapsed_s']}s\n")

    pol_names = {v: k for k, v in POLARITY.items()}
    print('  Polarity:')
    for code, name in sorted(pol_names.items()):
        print(f'    {name:<12} {stats.get(f"p_{code}", 0):>8,}')

    tmp_names = {v: k for k, v in TEMPORAL.items()}
    print('\n  Temporality:')
    for code, name in sorted(tmp_names.items()):
        print(f'    {name:<12} {stats.get(f"t_{code}", 0):>8,}')

    dom_names = {v: k for k, v in DOMAIN.items()}
    print('\n  Domain (non-GENERAL):')
    shown = False
    for code, name in sorted(dom_names.items()):
        if code == DOMAIN['GENERAL']: continue
        n = stats.get(f'd_{code}', 0)
        if n:
            print(f'    {name:<12} {n:>8,}')
            shown = True
    if not shown:
        print('    (none detected)')


if __name__ == '__main__':
    main()
