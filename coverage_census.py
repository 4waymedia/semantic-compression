"""
coverage_census.py -- per-SEGMENT channel coverage for a build. Stage 14.

WHY THIS EXISTS (Paul, 2026-08-28): an aggregate coverage number over this dictionary
is not just imprecise, it is WRONG IN BOTH DIRECTIONS. The dictionary holds English
words, phrases, HTML/CSS/JS identifiers, numerals and symbols in one id space. Measured
on v04: overall polarity read 24.0%, which simultaneously

  * UNDERSTATED the part that matters -- phrases are at 35.9% and are the best-covered
    segment we have (compositional phrase EPA works), and
  * OVERSTATED a failure that is not one -- `div`, `border-radius`, `0xFF` and `<br>`
    sit at 0.0% EPA because THAT IS THE CORRECT ANSWER. CSS has no affect.

So 24,805 entries were being scored as a coverage hole when they were a coverage
SUCCESS: the channel correctly declining to invent affect for a stylesheet token.
A single number cannot say that. A segmented one can.

AND IT MUST RUN EVERY BUILD. A census taken once goes stale the moment a channel is
re-derived -- which is every build, since facets/epa/vfacets/wordclass are all
re-derived per fingerprint. The whole point of the artifact-identity work is that
numbers travel WITH the build that produced them; a coverage table pasted into a doc
is the same defect class as a restated fingerprint.

SEGMENTS (surface shape + provenance; deterministic, no model):
    phrase          multi-token entries -- EPA composes, so coverage SHOULD be high
    word            single alphabetic surfaces -- the lexical core
    name            capitalised single surfaces -- proper nouns, brands, people
    web_structure   CSS/JS/HTML identifiers (provenance file + shape) -- affect N/A
    numeric         numerals, versions, measurements -- affect N/A
    symbol          punctuation/operators/structural -- affect N/A

EXPECTATION per segment, so the report can say PASS/N-A instead of a bare percentage:
channels are only expected where they are meaningful. `expected: false` segments are
reported but never counted as a gap -- and a NON-ZERO value there is the anomaly worth
looking at (affect on a CSS token means something leaked).

    python coverage_census.py --db db/builds/<name>/dictionary.lmdb
    python coverage_census.py --db ... --json      # machine-readable only
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import time
from collections import Counter, defaultdict
from pathlib import Path

import lmdb

REC2 = struct.Struct('<BB')          # vfacets
REC3 = struct.Struct('<BBB')         # wordclass

# Channels examined. name -> (sub-db, predicate on the raw record)
# Value EXTRACTORS, parallel to CHANNELS. Coverage alone cannot tell a measurement
# from a blanket assignment: `direction` reads 100% over its qualifying classes, which
# looks perfect and means nothing until you see 46.9% of it is a single value. A
# channel that is 100% covered and 90% one value is not covered, it is DEFAULTED.
# So the census records the value distribution next to the rate and flags saturation.
VALUES: dict[str, object] = {
    'polarity':  lambda r: (REC2.unpack(r)[1] >> 2) & 0b11,
    'temporal':  lambda r: REC2.unpack(r)[0] & 0b111,
    'agency':    lambda r: (REC2.unpack(r)[0] >> 6) & 0b11,
    'direction': lambda r: (REC2.unpack(r)[0] >> 3) & 0b111,
    'wordclass': lambda r: (REC3.unpack(r)[0] >> 5) & 0b111,
}
SATURATION_WARN = 0.60   # one value holding >60% of a channel is a default, not data

CHANNELS: dict[str, tuple] = {
    'facets':    (b'facets',    lambda r: r is not None and len(r) == 4),
    'epa':       (b'epa',       lambda r: r is not None and len(r) == 12),
    # polarity reads the KNOWN BIT, not a non-zero value. NEUTRAL is 0b00, so a
    # `!= 0` test scores every MEASURED-neutral surface as missing -- it reported
    # 7.1% where the channel actually holds 54.0%. This is the absent-vs-zero rule
    # the whole build follows; the census was violating it while measuring it.
    'polarity':  (b'vfacets',   lambda r: r is not None and ((REC2.unpack(r)[1] >> 1) & 1) == 1),
    'temporal':  (b'vfacets',   lambda r: r is not None and (REC2.unpack(r)[0] & 0b111) != 0),
    'agency':    (b'vfacets',   lambda r: r is not None and ((REC2.unpack(r)[0] >> 6) & 0b11) != 0),
    'direction': (b'vfacets',   lambda r: r is not None and ((REC2.unpack(r)[0] >> 3) & 0b111) != 0),
    'wordclass': (b'wordclass', lambda r: r is not None and ((REC3.unpack(r)[0] >> 5) & 0b111) != 0),
}

# Where each channel is MEANINGFUL. False = absence is correct, not a gap; a value
# there is an ANOMALY worth investigating, not coverage worth celebrating.
#
# CORRECTED 2026-08-28 (Paul). My first table was three assumptions, two wrong:
#
#   symbol EXPECTS temporal -- punctuation is not affect-free structure. '?' opens an
#     unresolved state, '!' marks a completed assertion, '.' closes, '...' suspends.
#     Tense/aspect on a symbol is real and the census must count its absence as a gap.
#   name EXPECTS direction -- a named entity is a participant, and participants have
#     orientation in an event (toward/away, source/target). Excluding names would have
#     hidden the largest referent population from the only channel that tracks it.
#   web_structure DOES NOT expect EPA -- confirmed. `border-radius` has no affect;
#     a value here means affect leaked onto a stylesheet token.
#
# The general rule this settles: a segment is excluded from a channel only when the
# property is UNDEFINED for it, never merely when it is hard to derive.
EXPECTED: dict[str, set] = {
    'facets':    {'phrase', 'word', 'name', 'web_structure', 'numeric', 'symbol'},
    'epa':       {'phrase', 'word', 'name'},
    'polarity':  {'phrase', 'word', 'name'},
    'temporal':  {'phrase', 'word', 'symbol'},
    'agency':    {'phrase', 'word', 'name'},
    'direction': {'phrase', 'word', 'name'},
    'wordclass': {'phrase', 'word', 'name', 'numeric', 'symbol'},
}


# CLASS-AWARE QUALIFICATION (2026-08-29) -- the second half of the segment idea.
#
# Segmenting fixed the "CSS has no affect" error. It does NOT fix the same error one
# level down: `temporal` was scored over the WHOLE `word` segment, so every noun in
# the dictionary counted as a temporality gap. `engine` has no aspect. Scoring it as
# missing coverage is the identical mistake as scoring `border-radius` as missing EPA
# -- just with a subtler denominator.
#
# So a channel may further restrict its denominator by WORD CLASS. `temporal` is
# defined for verbs; `agency`/`direction` for the participants of an event (verbs and
# the nouns/names that fill roles). A surface outside the qualifying class is `n/a`,
# exactly like an out-of-segment one.
#
# The AMBIVALENT exclusion is the load-bearing part. `stone` is NOUN|VERB, and the
# vfacet builder deliberately DECLINES to give an ambivalent base form a static
# aspect -- which reading applies is token-level and this channel cannot know it.
# That refusal is correct behaviour, so the census must not count it as a gap, or the
# metric would reward the builder for guessing. Precision and the number that
# measures it have to agree about what good looks like.
QUALIFYING_CLASSES: dict[str, set] = {
    'temporal':  {'VERB'},
    'agency':    {'VERB', 'NOUN', 'NAME'},
    'direction': {'VERB', 'NOUN', 'NAME'},
}
# Channels where an AMBIVALENT surface is legitimately declined by the builder and
# therefore must leave the denominator too.
AMBIVALENT_EXEMPT: set = {'temporal'}

_ALPHA_LOWER = re.compile(r"[a-z][a-z'\-]*\Z")
_ALPHA_ANY = re.compile(r"[A-Za-z][A-Za-z'\-]*\Z")
_NUMERIC = re.compile(r"[\d][\d.,:%$#+\-/]*\Z")
# Shape tests for web identifiers that the provenance file cannot catch (a token is
# only in nonlexical_terms_* if it appears EXCLUSIVELY in non-lexical sources, so
# anything that also occurs in prose -- 'style', 'link', 'table' -- is absent from it).
_WEBISH = re.compile(r"""(\A[.#][A-Za-z_-]|          # .class  #id
                          \A<|>\Z|                    # <br>  markup
                          \A--|                       # --css-var
                          [_-]{2,}|                   # snake__case, --mod
                          \A[a-z]+-[a-z-]+\Z|         # border-radius, flex-grow
                          \A[a-z]+[A-Z][a-z]+\Z)      # camelCase
                       """, re.X)


def segment_of(surface: str, nonlexical: set) -> str:
    """Deterministic segment. Order matters: provenance beats shape, shape beats default."""
    if ' ' in surface:
        return 'phrase'
    low = surface.lower()
    if low in nonlexical or _WEBISH.search(surface):
        return 'web_structure'
    if _NUMERIC.match(surface):
        return 'numeric'
    if _ALPHA_LOWER.match(surface):
        return 'word'
    if _ALPHA_ANY.match(surface):
        return 'name' if surface[:1].isupper() else 'word'
    return 'symbol'


def load_nonlexical(pkg: Path) -> set:
    """The HARD RULE provenance set (tokens seen ONLY in non-lexical sources).

    Partial by construction -- a web token that also appears in prose is not in it --
    which is why segment_of ALSO applies a shape test. Recorded so the limitation is
    visible in the report rather than discovered later."""
    out: set = set()
    for cand in list((pkg.parent.parent / 'data').glob('nonlexical_terms_*.txt')) + \
                list((Path(__file__).parent / 'data').glob('nonlexical_terms_*.txt')):
        try:
            for line in cand.read_text(encoding='utf-8', errors='replace').splitlines():
                s = line.strip()
                if s and not s.startswith('#'):
                    out.add(s.lower())
        except Exception:
            continue
    return out


def census(lmdb_path: Path) -> dict:
    t0 = time.perf_counter()
    pkg = Path(lmdb_path).parent
    nonlexical = load_nonlexical(pkg)
    env = lmdb.open(str(lmdb_path), readonly=True, lock=False, max_dbs=16)
    fwd = env.open_db(b'forward', create=False)
    handles: dict[bytes, object] = {}
    for _, (dbname, _p) in CHANNELS.items():
        if dbname not in handles:
            try:
                handles[dbname] = env.open_db(dbname, create=False)
            except lmdb.Error:
                handles[dbname] = None

    # word-class channel, if the build has it -- powers QUALIFYING_CLASSES.
    wc: dict = {}
    try:
        wc_db = env.open_db(b'wordclass', create=False)
        with env.begin() as _t:
            for _k, _v in _t.cursor(db=wc_db):
                b0 = REC3.unpack(bytes(_v))[0]
                cls = ['UNKNOWN', 'NOUN', 'VERB', 'MOD', 'FUNCTION',
                       'NAME', 'NUMERAL', 'OTHER'][(b0 >> 5) & 0b111]
                mask = ((b0 >> 5) & 0b111,)
                wc[bytes(_k)] = (cls, bool((b0 >> 2) & 1))
    except lmdb.Error:
        pass

    seg_total: Counter = Counter()
    seg_hit: dict = defaultdict(Counter)
    seg_qual: dict = defaultdict(Counter)      # class-aware denominator
    seg_offclass: dict = defaultdict(Counter)  # values on NON-qualifying surfaces
    val_dist: dict = defaultdict(Counter)      # value histogram per channel
    # Handles are opened BEFORE begin() -- python-lmdb raises MDB_INCOMPATIBLE/
    # InvalidParameter when open_db runs inside a read transaction.
    meta_db = env.open_db(b'meta', create=False)
    with env.begin() as txn:
        fp = txn.get(b'dictionary_fingerprint', db=meta_db)
        for k, v in txn.cursor(db=fwd):
            surface = k.decode('utf-8', 'replace')
            idb = bytes(v)
            seg = segment_of(surface, nonlexical)
            seg_total[seg] += 1
            wcrec = wc.get(idb)
            for ch, (dbname, pred) in CHANNELS.items():
                # class-aware denominator: does this surface even qualify?
                q = QUALIFYING_CLASSES.get(ch)
                if q is None or not wc:
                    qualifies = True
                else:
                    qualifies = bool(wcrec) and wcrec[0] in q and \
                        not (ch in AMBIVALENT_EXEMPT and wcrec[1])
                if qualifies:
                    seg_qual[ch][seg] += 1
                else:
                    # NUMERATOR MUST MATCH DENOMINATOR. Counting a hit on a surface
                    # excluded from the denominator produced 391% `direction` and
                    # 1225% `temporal` -- a rate is only a rate if both halves range
                    # over the same population. A value on a non-qualifying surface
                    # is not coverage; it is the same ANOMALY signal as affect on a
                    # CSS token, and is counted as one.
                    h = handles.get(dbname)
                    if h is not None:
                        try:
                            if pred(txn.get(idb, db=h)):
                                seg_offclass[ch][seg] += 1
                        except Exception:
                            pass
                    continue
                h = handles.get(dbname)
                if h is None:
                    continue
                raw = txn.get(idb, db=h)
                try:
                    if pred(raw):
                        seg_hit[ch][seg] += 1
                        if ch in VALUES:
                            val_dist[ch][VALUES[ch](raw)] += 1
                except Exception:
                    pass
    env.close()

    total = sum(seg_total.values())
    report = {
        'dictionary_fingerprint': fp.decode() if fp else None,
        'census_format_version': 2,   # v2 adds class-aware denominators
        'wordclass_channel_present': bool(wc),
        'total_entries': total,
        'elapsed_s': round(time.perf_counter() - t0, 2),
        'nonlexical_provenance_entries': len(nonlexical),
        'nonlexical_provenance_note':
            'partial by construction: only tokens seen EXCLUSIVELY in non-lexical '
            'sources; segment_of also applies a shape test',
        'segments': {s: {'entries': n, 'share': round(n / total, 4)}
                     for s, n in seg_total.most_common()},
        'channels': {},
    }
    for ch in CHANNELS:
        per = {}
        for seg, n in seg_total.items():
            hit = seg_hit[ch][seg]
            qn = seg_qual[ch][seg] if ch in QUALIFYING_CLASSES else n
            per[seg] = {
                'covered': hit,
                'of': n,
                'qualifying': qn,          # class-aware denominator
                'pct': round(100 * hit / n, 1) if n else 0.0,
                # the honest rate: covered / (surfaces the channel is DEFINED for)
                'pct_of_qualifying': round(100 * hit / qn, 1) if qn else None,
                'expected': seg in EXPECTED[ch],
                # a value where none is meaningful is an ANOMALY, not coverage
                'off_class_values': seg_offclass[ch][seg],
                'anomaly': ((seg not in EXPECTED[ch]) and hit > 0) or seg_offclass[ch][seg] > 0,
            }
        exp_tot = sum(v['qualifying'] for s, v in per.items() if v['expected'])
        exp_hit = sum(v['covered'] for s, v in per.items() if v['expected'])
        report['channels'][ch] = {
            'by_segment': per,
            'qualifying_classes': sorted(QUALIFYING_CLASSES[ch]) if ch in QUALIFYING_CLASSES else None,
            'ambivalent_excluded': ch in AMBIVALENT_EXEMPT,
            'coverage_where_expected_pct': round(100 * exp_hit / exp_tot, 1) if exp_tot else None,
            'raw_overall_pct': round(100 * sum(v['covered'] for v in per.values()) / total, 1),
        }
        vd = val_dist.get(ch)
        if vd:
            tot_v = sum(vd.values())
            mode_v, mode_n = vd.most_common(1)[0]
            share = mode_n / tot_v
            report['channels'][ch]['value_distribution'] = dict(vd.most_common())
            report['channels'][ch]['mode_share'] = round(share, 3)
            # 100% coverage carried by one value is a DEFAULT wearing coverage's clothes
            report['channels'][ch]['saturated'] = bool(share >= SATURATION_WARN)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--db', required=True,
                    help='build package LMDB, e.g. db/builds/<name>/dictionary.lmdb (REQUIRED)')
    ap.add_argument('--json', action='store_true', help='print the JSON only')
    ap.add_argument('--dry-run', action='store_true', help='do not write the stats file')
    a = ap.parse_args()
    rep = census(Path(a.db))
    if not a.dry_run:
        out = Path(a.db).parent / 'coverage_census.json'
        out.write_text(json.dumps(rep, indent=2), encoding='utf-8')
    if a.json:
        print(json.dumps(rep, indent=2))
        return 0
    print(f"coverage census  fp={str(rep['dictionary_fingerprint'])[:16]}  "
          f"{rep['total_entries']:,} entries  {rep['elapsed_s']}s")
    print('\nsegments:')
    for s, d in rep['segments'].items():
        print(f"  {s:14}{d['entries']:>9,}  {100*d['share']:5.1f}%")
    print(f"\n{'channel':11}" + ''.join(f'{s[:9]:>11}' for s in rep['segments']) + f"{'WHERE EXP':>11}")
    for ch, d in rep['channels'].items():
        row = ''.join(
            ((f"{d['by_segment'][s]['pct_of_qualifying']:>10.1f}%"
              if d['by_segment'][s]['pct_of_qualifying'] is not None
              else f"{d['by_segment'][s]['pct']:>10.1f}%") if d['by_segment'][s]['expected']
             else ('   ANOMALY' if d['by_segment'][s]['anomaly'] else '        n/a'))
            for s in rep['segments'])
        we = d['coverage_where_expected_pct']
        print(f'{ch:11}' + row + (f'{we:>10.1f}%' if we is not None else f"{'-':>11}"))
    for ch, d in rep['channels'].items():
        if d.get('qualifying_classes'):
            amb = ' (AMBIVALENT excluded)' if d['ambivalent_excluded'] else ''
            print(f"  {ch}: scored only over {'/'.join(d['qualifying_classes'])}{amb}")
    sat = [(c, d) for c, d in rep['channels'].items() if d.get('saturated')]
    if sat:
        print('\n  SATURATION WARNING -- high coverage carried by a single value:')
        for c, d in sat:
            print(f"    {c}: mode holds {100*d['mode_share']:.0f}% of all values "
                  f"-- treat this channel's coverage as UNCONFIRMED, not measured")
    print('\n  n/a = channel not meaningful for that segment (absence is CORRECT, not a gap)')
    print('  WHERE EXP = coverage counted only over segments where the channel applies')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
