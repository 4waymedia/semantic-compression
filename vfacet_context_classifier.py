#!/usr/bin/env python3
"""
vfacet_context_classifier.py

For vfacet UNKNOWN entries (agency / direction), search the transcript corpus
for real usage examples, extract context windows, send them to the LLM, then
aggregate votes across multiple matches to determine the most likely
classification.

Why: an isolated word like "run" is ambiguous. Seeing it in the context
"he decided to <<run>> for office" makes SELF/TOWARD clear.

Usage:
    # Classify direction unknowns with context
    python vfacet_context_classifier.py --field direction --openai --openai-model gpt-5.4-nano

    # Both fields, require 2+ matches, 70% agreement threshold
    python vfacet_context_classifier.py --field both --min-matches 2 --min-confidence 0.7

    # Dry run — scan corpus and show context hits, no LLM or writes
    python vfacet_context_classifier.py --field both --dry-run --max-unknown 200

    # Stats only
    python vfacet_context_classifier.py --stats
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from struct import pack as _pack, unpack as _unpack
from typing import Iterator

import lmdb

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
DEFAULT_DB      = _HERE / 'db' / 'dictionary.lmdb'
DEFAULT_CORPUS  = _HERE / '..' / 'Resources' / 'transcripts'
VFACETS_DB      = b'vfacets'

# ---------------------------------------------------------------------------
# Vfacet codec (mirrors vfacet_llm.py)
# ---------------------------------------------------------------------------
DIRECTION = {'UNKNOWN':0,'TOWARD':1,'AWAY':2,'STABLE':3,'REVERSAL':4,'NEUTRAL':5}
AGENCY    = {'UNKNOWN':0,'SELF':1,'OTHER':2,'SYSTEM':3}
TEMPORAL  = {'UNKNOWN':0,'STATE':1,'PROCESS':2,'EVENT':3,'OUTCOME':4,'CONDITION':5}
DOMAIN    = {'GENERAL':0,'CODING':1,'MEDICAL':2,'LEGAL':3,'FINANCE':4,'SCIENCE':5,'PSYCHOLOGY':6}
POLARITY  = {'NEUTRAL':0,'POSITIVE':1,'NEGATIVE':2,'BIPOLAR':3}

INV_DIRECTION = {v: k for k, v in DIRECTION.items()}
INV_AGENCY    = {v: k for k, v in AGENCY.items()}

def pack_vfacet(agency, direction, temporal, domain, polarity) -> bytes:
    b0 = ((agency & 0x3) << 6) | ((direction & 0x7) << 3) | ((temporal & 0x7))
    b1 = ((domain & 0xF) << 4) | ((polarity & 0x3) << 2)
    return bytes([b0, b1])

def unpack_vfacet(data: bytes) -> dict:
    b0, b1 = data[0], data[1]
    return {
        'agency':    (b0 >> 6) & 0x3,
        'direction': (b0 >> 3) & 0x7,
        'temporal':  b0 & 0x7,
        'domain':    (b1 >> 4) & 0xF,
        'polarity':  (b1 >> 2) & 0x3,
    }

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------
def _load_dotenv():
    for d in [_HERE, _HERE / '..']:
        p = d / '.env'
        if p.exists():
            for line in p.read_text().splitlines():
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

# ---------------------------------------------------------------------------
# Step 1: Load UNKNOWN entries from vfacets DB
# ---------------------------------------------------------------------------
def load_unknowns(lmdb_path: Path, field: str) -> list[tuple[bytes, str]]:
    """Return list of (id_bytes, surface) for entries with UNKNOWN in field."""
    env = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=8, readonly=True)
    db_vf  = env.open_db(VFACETS_DB)
    targets = []
    with env.begin() as txn:
        db_fwd = env.open_db(b'forward', txn=txn)
        cur = txn.cursor(db=db_fwd)
        for surface_bytes, id_bytes in cur.iternext():
            v = txn.get(bytes(id_bytes), db=db_vf)
            if v is None:
                continue
            f = unpack_vfacet(bytes(v))
            want = False
            if field in ('agency', 'both') and f['agency'] == AGENCY['UNKNOWN']:
                want = True
            if field in ('direction', 'both') and f['direction'] == DIRECTION['UNKNOWN']:
                want = True
            if want:
                try:
                    surface = surface_bytes.decode('utf-8', errors='replace')
                except Exception:
                    continue
                targets.append((bytes(id_bytes), surface))
    env.close()
    return targets

# ---------------------------------------------------------------------------
# Step 2: Corpus scan — build context windows
# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    return html.unescape(text)

def _highlight(text: str, surface: str, context_words: int) -> str | None:
    """
    Find surface in text (case-insensitive), extract ±context_words context,
    and highlight the match as <<surface>>.
    Returns None if not found.
    """
    pattern = re.compile(r'\b' + re.escape(surface) + r'\b', re.IGNORECASE)
    m = pattern.search(text)
    if m is None:
        return None
    words = text.split()
    # Find which word index the match starts at
    pos = 0
    match_start_word = 0
    for i, w in enumerate(words):
        if pos + len(w) >= m.start():
            match_start_word = i
            break
        pos += len(w) + 1

    match_len_words = max(1, len(surface.split()))
    lo  = max(0, match_start_word - context_words)
    hi  = min(len(words), match_start_word + match_len_words + context_words)

    before = ' '.join(words[lo:match_start_word])
    matched = ' '.join(words[match_start_word:match_start_word + match_len_words])
    after  = ' '.join(words[match_start_word + match_len_words:hi])

    parts = []
    if lo > 0:
        parts.append('...')
    parts.append(f'{before} <<{matched}>> {after}'.strip())
    if hi < len(words):
        parts.append('...')
    return ' '.join(parts)


def scan_corpus(
    corpus_dir: Path,
    targets: list[tuple[bytes, str]],
    max_contexts: int = 5,
    context_words: int = 25,
) -> dict[str, list[str]]:
    """
    Two-phase scan:
      Phase A — build a corpus word index: {word → [(text, chunk_ref), ...]}
                One pass through all JSON files, O(corpus_tokens).
      Phase B — for each unknown surface, look it up in the index and extract
                context windows. O(unknowns × avg_hits_per_word).

    Returns: {surface: [context_string, ...]}
    """
    surfaces = [s for _, s in targets]
    surface_map: dict[str, str] = {s.lower(): s for s in surfaces}

    _SKIP_SOLO = frozenset({
        'a','an','the','in','of','on','at','to','for','by','as','or','and',
        'but','so','yet','nor','is','it','its','be','was','are','were','been',
        'has','have','had','not','no','do','did','does','i','we','he','she',
        'they','you','my','our','his','her','their','this','that','with','from',
        'if','up','out','about','into','over','after','before','than','then',
        'when','which','who','what','where','how','will','would','could',
        'should','may','might','can','just','also','only','even','both','each',
        'all','any','more','most','very','too','now','still','already','again',
        'here','there',
    })

    # Determine anchor word for each surface
    surf_anchors: list[tuple[str, str]] = []   # [(surf_lower, anchor_word)]
    for s in surfaces:
        words = s.lower().split()
        if not words:
            continue
        if len(words) == 1 and words[0] in _SKIP_SOLO:
            continue   # single stopword — skip entirely
        anchor = next((w for w in words if w not in _SKIP_SOLO), words[0])
        surf_anchors.append((s.lower(), anchor))

    anchor_to_surfs: dict[str, list[str]] = defaultdict(list)
    for surf_lower, anchor in surf_anchors:
        anchor_to_surfs[anchor].append(surf_lower)

    anchor_set = set(anchor_to_surfs.keys())

    # --- Phase A: build corpus word index ---
    # word → list of chunk texts where that word appears (capped at 50 per word
    # to bound memory; we only need max_contexts hits per surface anyway)
    print(f'  Phase A: building corpus word index...')
    word_chunks: dict[str, list[str]] = defaultdict(list)
    json_files = sorted(corpus_dir.rglob('*.json'))
    total = len(json_files)
    _WORD_CAP = max(max_contexts * 4, 20)   # cap chunks stored per word

    for fi, path in enumerate(json_files):
        if fi % 1000 == 0:
            print(f'    Indexed {fi:,}/{total:,} files...', end='\r')
        try:
            data = json.loads(path.read_bytes())
        except Exception:
            continue
        for chunk in data.get('chunks', data.get('segments', [])):
            raw = chunk.get('text', '')
            if not raw:
                continue
            text = _clean(raw)
            words_seen: set[str] = set()
            for w in re.findall(r"[\w']+", text.lower()):
                if w in anchor_set and w not in words_seen:
                    if len(word_chunks[w]) < _WORD_CAP:
                        word_chunks[w].append(text)
                    words_seen.add(w)

    print(f'  Phase A complete: {len(word_chunks):,} anchor words indexed.     ')

    # --- Phase B: look up each surface in the index ---
    hits: dict[str, list[str]] = defaultdict(list)

    for surf_lower, anchor in surf_anchors:
        surface = surface_map.get(surf_lower)
        if surface is None or not word_chunks.get(anchor):
            continue
        for text in word_chunks[anchor]:
            if len(hits[surface]) >= max_contexts:
                break
            ctx = _highlight(text, surface, context_words)
            if ctx is not None:
                hits[surface].append(ctx)

    print(f'  Phase B complete: {len(hits):,}/{len(surf_anchors):,} surfaces matched.')
    return dict(hits)

# ---------------------------------------------------------------------------
# Step 3: LLM classification with context
# ---------------------------------------------------------------------------
_CONTEXT_PROMPT = """\
Classify the AGENCY and DIRECTION of each highlighted word/phrase based on how
it is actually used in the transcript context shown. The word/phrase is marked <<like this>>.

AGENCY: SELF=acts on self or for themselves | OTHER=acts on others | SYSTEM=automated/institutional | UNKNOWN=genuinely indeterminate
DIRECTION: TOWARD=approach/gain/build/offer | AWAY=avoid/reject/retreat | STABLE=persist/anchor/continue | REVERSAL=undo/pivot/rethink | NEUTRAL=filler/reference/no stance | UNKNOWN=genuinely indeterminate

Return a JSON array in SAME ORDER as input, one object per entry:
[{{"word": "...exact word/phrase...", "agency": "SELF", "direction": "TOWARD"}}, ...]

Entries:
{entries}
"""


def _call_llm(entries: list[tuple[str, str]], url: str, api_key: str, model: str) -> list[dict]:
    """
    Call LLM with (surface, context) pairs.
    entries: [(surface, highlighted_context), ...]
    Returns list of {word, agency, direction} dicts.
    """
    import json as _json, urllib.request as _req, urllib.error as _uerr

    lines = []
    for i, (surface, ctx) in enumerate(entries, 1):
        lines.append(f'{i}. word="{surface}" | context: "{ctx}"')

    prompt = _CONTEXT_PROMPT.format(entries='\n'.join(lines))
    payload = _json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_completion_tokens': 4096,
        'temperature': 0.0,
    }).encode()
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    request = _req.Request(
        f'{url.rstrip("/")}/v1/chat/completions',
        data=payload, headers=headers, method='POST',
    )
    try:
        with _req.urlopen(request, timeout=60) as resp:
            body = _json.loads(resp.read())
    except _uerr.HTTPError as e:
        txt = ''
        try: txt = e.read().decode('utf-8', errors='replace')[:200]
        except: pass
        if e.code >= 500:
            raise RuntimeError(f'SERVICE_UNAVAILABLE:{e.code}') from e
        raise RuntimeError(f'API_ERROR:{e.code} {txt}') from e
    except _uerr.URLError as e:
        raise RuntimeError(f'SERVICE_UNAVAILABLE:connection_refused') from e

    text = body['choices'][0]['message']['content'].strip()
    start, end = text.find('['), text.rfind(']') + 1
    if start == -1 or end == 0:
        return []
    try:
        return _json.loads(text[start:end])
    except Exception:
        return []


def classify_with_context(
    hits: dict[str, list[str]],
    url: str,
    api_key: str,
    model: str,
    batch_size: int = 20,
    parallel: int = 10,
    dry_run: bool = False,
) -> dict[str, list[dict]]:
    """
    For each (surface, [contexts]), call LLM to classify.
    Returns: {surface: [{agency, direction}, ...]}  (one result per context)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Build flat list of (surface, context) pairs
    flat: list[tuple[str, str]] = []
    for surface, contexts in hits.items():
        for ctx in contexts:
            flat.append((surface, ctx))

    if dry_run:
        print(f'  [DRY RUN] would send {len(flat):,} (word, context) pairs to LLM')
        return {}

    print(f'  Classifying {len(flat):,} context samples '
          f'({len(hits):,} unique surfaces) via LLM...')

    # Batch into groups
    batches = [flat[i:i+batch_size] for i in range(0, len(flat), batch_size)]
    results: dict[str, list[dict]] = defaultdict(list)
    errors = 0
    done = 0

    def _run(batch):
        r = _call_llm(batch, url, api_key, model)
        return batch, r

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for chunk_start in range(0, len(batches), parallel):
            chunk = batches[chunk_start:chunk_start + parallel]
            futures = {pool.submit(_run, b): b for b in chunk}
            for fut in as_completed(futures):
                try:
                    batch, r = fut.result()
                except Exception as e:
                    if 'SERVICE_UNAVAILABLE' in str(e):
                        print(f'\n  [ABORT] server unavailable: {e}')
                        return dict(results)
                    errors += len(futures[fut])
                    continue
                # Map results back to surfaces positionally
                for j, (surface, _ctx) in enumerate(batch):
                    if j < len(r) and isinstance(r[j], dict):
                        results[surface].append(r[j])
                done += len(batch)
                if done % (batch_size * parallel) == 0:
                    print(f'  LLM: {done:,}/{len(flat):,}...', end='\r')

    print(f'  LLM complete: {done:,} classified, {errors} errors.          ')
    return dict(results)

# ---------------------------------------------------------------------------
# Step 4: Vote aggregation
# ---------------------------------------------------------------------------
def aggregate_votes(
    surface_results: dict[str, list[dict]],
    min_confidence: float = 0.6,
    field: str = 'both',
) -> dict[str, dict]:
    """
    For each surface with multiple context classifications, vote.
    Returns: {surface: {agency, direction, agency_conf, direction_conf, n_votes}}
    """
    aggregated = {}
    for surface, results in surface_results.items():
        if not results:
            continue
        n = len(results)
        agency_votes    = Counter(r.get('agency',    'UNKNOWN').upper() for r in results)
        direction_votes = Counter(r.get('direction', 'UNKNOWN').upper() for r in results)

        best_agency,    agency_count    = agency_votes.most_common(1)[0]
        best_direction, direction_count = direction_votes.most_common(1)[0]

        agency_conf    = agency_count / n
        direction_conf = direction_count / n

        entry = {
            'agency':         best_agency    if agency_conf    >= min_confidence else 'UNKNOWN',
            'direction':      best_direction if direction_conf >= min_confidence else 'UNKNOWN',
            'agency_conf':    round(agency_conf, 3),
            'direction_conf': round(direction_conf, 3),
            'n_votes':        n,
            'agency_dist':    dict(agency_votes),
            'direction_dist': dict(direction_votes),
        }

        # Only include fields we're classifying
        if field == 'agency':
            entry['direction'] = None
        elif field == 'direction':
            entry['agency'] = None

        aggregated[surface] = entry

    return aggregated

# ---------------------------------------------------------------------------
# Step 5: Write to DB
# ---------------------------------------------------------------------------
def write_results(
    lmdb_path: Path,
    targets: list[tuple[bytes, str]],
    aggregated: dict[str, dict],
    field: str,
    min_confidence: float,
    dry_run: bool = False,
) -> Counter:
    id_map = {surface: id_bytes for id_bytes, surface in targets}
    stats: Counter = Counter()

    env = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=8, readonly=dry_run)
    db_vf = env.open_db(VFACETS_DB)

    with env.begin(write=not dry_run) as txn:
        for surface, vote in aggregated.items():
            id_bytes = id_map.get(surface)
            if id_bytes is None:
                continue
            existing = txn.get(id_bytes, db=db_vf)
            if existing is None:
                continue
            old = unpack_vfacet(existing)

            agency_code    = old['agency']
            direction_code = old['direction']

            if field in ('agency', 'both') and vote.get('agency') not in (None, 'UNKNOWN'):
                new_code = AGENCY.get(vote['agency'], AGENCY['UNKNOWN'])
                if new_code != AGENCY['UNKNOWN']:
                    agency_code = new_code
                    stats['agency_written'] += 1

            if field in ('direction', 'both') and vote.get('direction') not in (None, 'UNKNOWN'):
                new_code = DIRECTION.get(vote['direction'], DIRECTION['UNKNOWN'])
                if new_code != DIRECTION['UNKNOWN']:
                    direction_code = new_code
                    stats['direction_written'] += 1

            if not dry_run:
                txn.put(id_bytes, pack_vfacet(
                    agency_code, direction_code,
                    old['temporal'], old['domain'], old['polarity'],
                ), db=db_vf)
            stats['entries_updated'] += 1

    env.close()
    return stats

# ---------------------------------------------------------------------------
# Stats printer
# ---------------------------------------------------------------------------
def print_stats(lmdb_path: Path):
    env = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=8, readonly=True)
    db_vf = env.open_db(VFACETS_DB)
    agency_c = Counter()
    direction_c = Counter()
    with env.begin() as txn:
        cur = txn.cursor(db=db_vf)
        for _, v in cur.iternext():
            f = unpack_vfacet(bytes(v))
            agency_c[INV_AGENCY[f['agency']]] += 1
            direction_c[INV_DIRECTION[f['direction']]] += 1
    total = sum(agency_c.values())
    env.close()
    print(f'  Total vfacet records: {total:,}')
    print('  Agency:')
    for k in ['UNKNOWN','SELF','OTHER','SYSTEM']:
        print(f'    {k:<12} {agency_c[k]:>8,}  ({agency_c[k]/total*100:.1f}%)')
    print('  Direction:')
    for k in ['UNKNOWN','TOWARD','AWAY','STABLE','REVERSAL','NEUTRAL']:
        print(f'    {k:<12} {direction_c[k]:>8,}  ({direction_c[k]/total*100:.1f}%)')

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    _load_dotenv()
    ap = argparse.ArgumentParser(description='Context-aware vfacet classifier')
    ap.add_argument('--db',             default=str(DEFAULT_DB))
    ap.add_argument('--corpus-dir',     default=str(DEFAULT_CORPUS))
    ap.add_argument('--field',          default='both', choices=['agency','direction','both'])
    ap.add_argument('--context-words',  type=int, default=25,
                    help='Words of context each side of match (default: 25)')
    ap.add_argument('--max-contexts',   type=int, default=5,
                    help='Max transcript matches per surface to send to LLM (default: 5)')
    ap.add_argument('--min-matches',    type=int, default=1,
                    help='Min corpus matches required to classify an entry (default: 1)')
    ap.add_argument('--min-confidence', type=float, default=0.6,
                    help='Fraction of contexts that must agree to write result (default: 0.6)')
    ap.add_argument('--max-unknown',    type=int, default=0,
                    help='Cap unknown entries to process (default: 0 = all)')
    ap.add_argument('--batch-size',     type=int, default=20,
                    help='Entries per LLM call (default: 20)')
    ap.add_argument('--parallel',       type=int, default=20,
                    help='Concurrent LLM calls (default: 20)')
    ap.add_argument('--openai',         action='store_true')
    ap.add_argument('--openai-model',   default='gpt-5.4-nano')
    ap.add_argument('--local',          action='store_true')
    ap.add_argument('--local-url',      default='http://localhost:8080')
    ap.add_argument('--dry-run',        action='store_true',
                    help='Scan corpus and show hits; no LLM calls or writes')
    ap.add_argument('--stats',          action='store_true')
    args = ap.parse_args()

    db_path     = Path(args.db)
    corpus_dir  = Path(args.corpus_dir)

    if args.stats:
        print(f'[vfacet_context] stats for {db_path}')
        print_stats(db_path)
        return

    api_url = ('https://api.openai.com' if args.openai
               else args.local_url if args.local
               else 'http://localhost:8080')
    api_key = os.environ.get('OPENAI_API_KEY', '') if args.openai else ''
    model   = args.openai_model if args.openai else 'local'

    print(f'[vfacet_context] db={db_path}')
    print(f'  Field: {args.field}  |  corpus: {corpus_dir}')

    # --- Step 1: Load unknowns ---
    t0 = time.perf_counter()
    print(f'\nStep 1 — Loading UNKNOWN entries for field={args.field}...')
    unknowns = load_unknowns(db_path, args.field)
    print(f'  Found {len(unknowns):,} UNKNOWN entries')

    # Filter: skip numeric fragments and blanks BEFORE applying cap
    unknowns = [(k, s) for k, s in unknowns if s and not s[0].isdigit()]
    print(f'  After numeric filter: {len(unknowns):,}')

    if args.max_unknown:
        unknowns = unknowns[:args.max_unknown]
        print(f'  Capped to {len(unknowns):,} (--max-unknown {args.max_unknown})')

    # --- Step 2: Corpus scan ---
    print(f'\nStep 2 — Scanning corpus for context windows...')
    print(f'  Context: ±{args.context_words} words | max {args.max_contexts} hits/surface')
    hits = scan_corpus(corpus_dir, unknowns,
                       max_contexts=args.max_contexts,
                       context_words=args.context_words)

    matched   = {s for s in [x for _, x in unknowns] if hits.get(s)}
    unmatched = len(unknowns) - len(matched)
    total_ctx = sum(len(v) for v in hits.values())
    print(f'  Matched: {len(matched):,} surfaces  |  '
          f'no corpus hit: {unmatched:,}  |  '
          f'total context samples: {total_ctx:,}')

    # Apply min-matches filter
    hits = {s: ctxs for s, ctxs in hits.items() if len(ctxs) >= args.min_matches}
    print(f'  After min-matches={args.min_matches}: {len(hits):,} surfaces remain')

    if args.dry_run:
        # Show sample hits
        print('\n  Sample context hits:')
        for surface, ctxs in list(hits.items())[:10]:
            print(f'    [{surface!r}]')
            for ctx in ctxs[:2]:
                print(f'      {ctx[:120]}')
        return

    if not hits:
        print('  No matches found — nothing to classify.')
        return

    # --- Step 3: LLM classification ---
    print(f'\nStep 3 -- LLM classification (model={model}, '
          f'batch={args.batch_size}, parallel={args.parallel})...')
    surface_results = classify_with_context(
        hits, api_url, api_key, model,
        batch_size=args.batch_size,
        parallel=args.parallel,
        dry_run=args.dry_run,
    )

    # --- Step 4: Vote aggregation ---
    print(f'\nStep 4 -- Aggregating votes (min_confidence={args.min_confidence})...')
    aggregated = aggregate_votes(surface_results, args.min_confidence, args.field)

    agency_dist    = Counter(v['agency']    for v in aggregated.values() if v.get('agency'))
    direction_dist = Counter(v['direction'] for v in aggregated.values() if v.get('direction'))
    print(f'  Surfaces with votes: {len(aggregated):,}')
    if agency_dist:
        print(f'  Agency vote dist:    {dict(agency_dist)}')
    if direction_dist:
        print(f'  Direction vote dist: {dict(direction_dist)}')

    # --- Step 5: Write ---
    print(f'\nStep 5 -- Writing results to DB...')
    write_stats = write_results(
        db_path, unknowns, aggregated, args.field,
        args.min_confidence, args.dry_run,
    )
    print(f'  Entries updated:    {write_stats["entries_updated"]:,}')
    if args.field in ('agency', 'both'):
        print(f'  Agency written:     {write_stats["agency_written"]:,}')
    if args.field in ('direction', 'both'):
        print(f'  Direction written:  {write_stats["direction_written"]:,}')
    print(f'  Total elapsed: {time.perf_counter() - t0:.1f}s')

    print_stats(db_path)


if __name__ == '__main__':
    main()
