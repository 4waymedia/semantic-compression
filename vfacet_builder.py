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
import json
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
    epa_keys: str = 'auto',
) -> dict:
    lmdb_path = Path(lmdb_path)
    epa_lmdb_path = Path(epa_lmdb_path)

    if not lmdb_path.exists():
        raise FileNotFoundError(f'Dictionary LMDB not found: {lmdb_path}')

    # Load EPA lookup (surface → E,P,A)
    epa_lookup: dict[str, tuple] = {}
    if epa_lmdb_path.exists():
        print(f'  Loading EPA from {epa_lmdb_path}...')
    # Sniffed dual-source load (2026-08-27): id-keyed dictionary channel is
    # PRIMARY when the target carries one; the external surface substrate blends
    # underneath. If --epa-db points at a surface-keyed store, behaviour is
    # unchanged from before.
    epa_id_lookup: dict = {}
    _scheme = (epa_keys if epa_keys in ('surface', 'id')
               else _sniff_epa_key_scheme(epa_lmdb_path, lmdb_path))
    if _scheme == 'id':
        epa_id_lookup = _load_epa_lookup_by_id(epa_lmdb_path)
        epa_lookup = {}
        print(f'  EPA source: ID-KEYED ({len(epa_id_lookup):,} entries) from {epa_lmdb_path} [scheme={_scheme}, {"forced" if epa_keys != "auto" else "sniffed"}]')
    else:
        epa_lookup = _load_epa_lookup(epa_lmdb_path)
        print(f'  EPA source: surface-keyed en| substrate ({len(epa_lookup):,} entries) [scheme={_scheme}, {"forced" if epa_keys != "auto" else "sniffed"}]')
    # Blend: when reading the dictionary itself, ALSO load the external substrate
    # as surface fallback if it exists beside the repo (best of both).
    if epa_id_lookup and DEFAULT_EPA_DB.exists() and Path(str(DEFAULT_EPA_DB)) != epa_lmdb_path:
        try:
            epa_lookup = _load_epa_lookup(DEFAULT_EPA_DB)
            print(f'  EPA blend fallback: {len(epa_lookup):,} surface-keyed entries')
        except Exception:
            epa_lookup = {}
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

    # ENRICHMENT SUBSTRATE JOIN (2026-08-10): agency/direction come from the
    # persistent surface-keyed cache vfacet_llm writes through to
    # (vfacet_substrate.lmdb). Deterministic offline given the substrate file;
    # unknown surface -> UNKNOWN, same as before the substrate existed. This is
    # what lets enrichment SURVIVE rebuilds -- only new surfaces owe the LLM.
    from vfacet_substrate import load_all as _load_substrate, \
        substrate_version as _substrate_version, DEFAULT_SUBSTRATE as _SUB_DEFAULT
    _sub = _load_substrate(_SUB_DEFAULT)
    _sub_version = _substrate_version(_SUB_DEFAULT)
    if _sub:
        print(f'  Enrichment substrate: {len(_sub):,} surfaces (version {_sub_version})')
    else:
        print('  Enrichment substrate: empty/absent -- agency/direction stay UNKNOWN')

    if not dry_run:
        with env.begin(write=True) as txn:
            db_vf = env.open_db(VFACETS_DB, txn=txn, create=True)
            for id_bytes, surface in surfaces:
                # id-keyed primary (the dictionary's own channel), surface-
                # keyed external substrate as blend fallback -- free, and
                # covers any term the channel lacks.
                epa = epa_id_lookup.get(id_bytes) or epa_lookup.get(surface)
                if epa:
                    polarity = _polarity_from_e(epa[0])
                    stats['epa_hit'] += 1
                else:
                    polarity = POLARITY['NEUTRAL']
                    stats['epa_miss'] += 1

                temporal  = _classify_temporal(surface)
                domain    = _classify_domain(surface)
                _enr = _sub.get(surface)
                if _enr is not None:
                    agency, direction = _enr
                    stats['enriched_from_substrate'] += 1
                else:
                    agency    = AGENCY['UNKNOWN']
                    direction = DIRECTION['UNKNOWN']

                txn.put(id_bytes, pack_vfacet(agency, direction, temporal, domain, polarity),
                        db=db_vf)
                stats['written'] += 1
                stats[f't_{temporal}'] += 1
                stats[f'd_{domain}']   += 1
                stats[f'p_{polarity}'] += 1
    else:
        for id_bytes, surface in surfaces:
            temporal = _classify_temporal(surface)
            domain   = _classify_domain(surface)
            epa      = epa_id_lookup.get(id_bytes) or epa_lookup.get(surface)
            polarity = _polarity_from_e(epa[0]) if epa else POLARITY['NEUTRAL']
            stats[f't_{temporal}'] += 1
            stats[f'd_{domain}']   += 1
            stats[f'p_{polarity}'] += 1

    stats['elapsed_s'] = round(time.perf_counter() - t0, 3)

    # Bind + record (2026-08-10 review, §5.1/§5.2): write vfacets_stats.json next to
    # the LMDB so (a) the artifact registry can say whether a build HAS vfacets --
    # the absence of this record is how the missing channel went unnoticed -- and
    # (b) `llm_enriched: false` distinguishes "deterministic pass only" from "also
    # got vfacet_llm.py". The fingerprint is READ from the build's meta, never typed.
    if not dry_run:
        try:
            meta_db = env.open_db(b'meta', create=False)
            with env.begin() as txn:
                _fp = txn.get(b'dictionary_fingerprint', db=meta_db)
        except Exception:
            _fp = None
        # PROVENANCE (2026-08-27 review): the substrate's agency/direction values
        # originate from vfacet_llm's passes -- carrying them forward via the
        # substrate join makes them MODEL-DERIVED DATA in this artifact, even
        # though no LLM ran in this build. `llm_enriched` therefore answers
        # "does any field trace to an LLM?" (the question a reader actually
        # asks), not "did vfacet_llm run in this build?" (which `llm_pass_ran`
        # now records). `unknown_fields` was wrong once the join landed: those
        # fields are partially populated, so report them as substrate_fields
        # with the count instead of declaring them absent.
        _n_sub = int(stats.get('enriched_from_substrate', 0))
        out = {
            'dictionary_fingerprint': _fp.decode() if _fp else None,
            'vfacets_format_version': 1,
            'record_width': 2,
            'key_scheme': 'base64_id',           # id-keyed, NOT vocab index n
            'llm_enriched': _n_sub > 0,          # any field traces to an LLM
            'llm_pass_ran': False,               # flipped by vfacet_llm.py when it runs
            'deterministic_fields': ['polarity', 'temporality', 'domain'],
            # substrate-joined fields: LLM-derived values carried forward from
            # the persistent cache (survives rebuilds; vfacet_llm writes
            # through to it). Entries the substrate lacks stay UNKNOWN.
            'substrate_fields': ['agency', 'direction'],
            'enriched_from_substrate': _n_sub,
            'substrate_version': _sub_version,
            **{k: v for k, v in stats.items()},
        }
        (Path(str(lmdb_path)).parent / 'vfacets_stats.json').write_text(
            json.dumps(out, indent=2), encoding='utf-8')
    env.close()
    return dict(stats)


# ---------------------------------------------------------------------------
# ID-KEYED EPA (2026-08-27). The dictionary grew its OWN `epa` sub-DB (236,645
# entries under v04) keyed by id_bytes -- the convention every asset over the
# dictionary follows. The surface loader above was written earlier, against the
# EXTERNAL epa_substrate.lmdb (67,936 entries, keys b'en|surface' -- terms,
# because human EPA ratings exist on terms before ids do), and was never
# revisited when the dictionary's channel landed. Joining 437,995 surfaces
# against 67,936 terms is the ROOT CAUSE of the polarity hole (50,174 matched /
# 387,821 NEUTRAL, measured 2026-08-11) while 3.5x more ratings sat unused in
# the same LMDB this builder already iterates. Finding: NLU/NLG lane
# 2026-08-27, audited and validated same day.
#
# KEY SCHEME IS SNIFFED FROM THE ARTIFACT, never declared: sample the first
# key -- b'en|' prefix means the external substrate, otherwise id-keyed. The
# same derive-don't-declare rule as every identity read this month.
# ---------------------------------------------------------------------------

def _sniff_epa_key_scheme(epa_lmdb_path: Path, dict_lmdb_path: Path,
                          sample: int = 1000) -> str:
    """Return 'surface' or 'id' -- or raise if the sample is ambiguous.

    MEASURED DESIGN (NLU/NLG lane fix doc sec.5 guard 1, 2026-08-28): sniff
    membership against `reverse` (whose keys ARE ids -- 100.00%% vs 0.97%%
    separation), never `forward` (73.85%% vs 3.46%%), and sample >=1000 keys,
    never one -- a single key misclassifies id-keyed sources 3.46%% of the
    time under a forward test. `b'|'` is outside the base64 id charset, so any
    sampled key containing it is decisive for the en|surface substrate.
    REFUSE THE MIDDLE: an ambiguous artifact gets an error naming --epa-keys,
    not a guess -- absence degrades to UNKNOWN, never to a wrong join.
    """
    keys = []
    env = lmdb.open(str(epa_lmdb_path), max_dbs=24, readonly=True, lock=False)
    try:
        with env.begin() as txn:
            db = env.open_db(b'epa', txn=txn)
            cur = txn.cursor(db=db)
            for k, _ in cur.iternext():
                keys.append(bytes(k))
                if len(keys) >= sample:
                    break
    finally:
        env.close()
    if not keys:
        return 'surface'   # empty epa db: either loader yields {}; keep old path
    if any(b'|' in k for k in keys):
        return 'surface'
    denv = lmdb.open(str(dict_lmdb_path), max_dbs=24, readonly=True, lock=False)
    try:
        with denv.begin() as txn:
            rev = denv.open_db(b'reverse', txn=txn)
            hits = sum(1 for k in keys if txn.get(k, db=rev) is not None)
    finally:
        denv.close()
    frac = hits / len(keys)
    if frac >= 0.99:
        return 'id'
    if frac <= 0.05:
        return 'surface'
    raise SystemExit(
        f"[vfacet_builder] EPA key scheme AMBIGUOUS: {hits}/{len(keys)} sampled "
        f"keys resolve in the dictionary's reverse db ({frac:.1%}). Refusing to "
        f"guess -- pass --epa-keys surface or --epa-keys id explicitly.")


def _load_epa_lookup_by_id(epa_lmdb_path: Path) -> dict[bytes, tuple[float, float, float]]:
    """Load id-keyed EPA: id_bytes -> (E, P, A). No join needed downstream --
    the build loop already iterates (id_bytes, surface)."""
    lookup: dict[bytes, tuple[float, float, float]] = {}
    env = lmdb.open(str(epa_lmdb_path), max_dbs=24, readonly=True, lock=False)
    with env.begin() as txn:
        db = env.open_db(b'epa', txn=txn)
        cur = txn.cursor(db=db)
        for k, v in cur.iternext():
            try:
                if len(v) == 12:
                    lookup[bytes(k)] = _EPA_STRUCT.unpack(v)
            except Exception:
                continue
    env.close()
    return lookup


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    # REQUIRED (2026-08-26 gotcha report): this defaulted to the LEGACY ROOT
    # db/dictionary.lmdb, so a bare run rebuilt the wrong database. Same lesson
    # export_browser_assets already carries: there is no "current" build, and
    # defaulting to one is how the wrong dictionary gets rebuilt.
    ap.add_argument('--db', required=True,
                    help='build package LMDB, e.g. db/builds/<name>/dictionary.lmdb '
                         '(REQUIRED -- no default; the legacy root default rebuilt '
                         'the wrong database)')
    ap.add_argument('--epa-db', default=str(DEFAULT_EPA_DB))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--epa-keys', choices=['auto', 'surface', 'id'], default='auto',
                    help='EPA key scheme; auto sniffs >=1000 keys against reverse and refuses ambiguity')
    args = ap.parse_args()

    tag = 'DRY RUN — ' if args.dry_run else ''
    print(f'[vfacet_builder] {tag}db={args.db}')
    stats = build_vfacets(Path(args.db), Path(args.epa_db), dry_run=args.dry_run, epa_keys=args.epa_keys)

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
