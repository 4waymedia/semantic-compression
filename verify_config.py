"""verify_config.py — validates the Tier 0 layout in config.py"""
import sys
sys.path.insert(0, '.')

from semantic_compression.config import (
    BASE64_CHARS, PRIMITIVES, PRIMITIVES_REVERSE,
    SYSTEM_IDS, WORD_IDS, STRUCTURAL_IDS, RESERVED_IDS,
    WORD_TO_ID, STRUCTURAL_TO_ID,
    TIER_WORD_FIRST_CHARS, TIER_CAPACITY,
    TIER_FIRST_CHARS_EXPANDED, tier_capacity_for,
    MULTI_WORD_FILLERS, SINGLE_WORD_FILLERS, COMPRESSION_MODES,
    detect_tier,
)

# ---- 1. Charset
assert len(BASE64_CHARS) == 64 and len(set(BASE64_CHARS)) == 64
print('[OK] Charset: 64 unique chars')

# ---- 2. Tier 0 component sizes
assert len(SYSTEM_IDS)     ==  5,  f"SYSTEM_IDS = {len(SYSTEM_IDS)}"
assert len(WORD_IDS)       == 26,  f"WORD_IDS = {len(WORD_IDS)}"
assert len(STRUCTURAL_IDS) == 27,  f"STRUCTURAL_IDS = {len(STRUCTURAL_IDS)}"
assert len(RESERVED_IDS)   ==  6,  f"RESERVED_IDS = {len(RESERVED_IDS)}"
print(f'[OK] Tier 0 sizes: SYSTEM=5, WORDS=26, STRUCTURAL=27, RESERVED=6')

# ---- 3. Total slots = 64 (active + reserved)
total_slots = len(SYSTEM_IDS) + len(WORD_IDS) + len(STRUCTURAL_IDS) + len(RESERVED_IDS)
assert total_slots == 64, f"Total slot count = {total_slots}, expected 64"
print(f'[OK] Total Tier 0 slots: 64 (all Base64 chars accounted for)')

# ---- 4. PRIMITIVES
assert len(PRIMITIVES) == 58, f"Expected 58 active primitives, got {len(PRIMITIVES)}"
assert all(k in BASE64_CHARS for k in PRIMITIVES), 'Primitive key outside Base64 charset'
assert all(k in BASE64_CHARS for k in RESERVED_IDS), 'Reserved key outside Base64 charset'
assert not (set(PRIMITIVES) & set(RESERVED_IDS)), 'Active/reserved key collision'
print(f'[OK] PRIMITIVES = 58 active (5 system + 26 words + 27 structural)')

# ---- 5. All 26 uppercase letters are word IDs
assert set(WORD_IDS.keys()) == set('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
print('[OK] All 26 uppercase letters are word IDs')

# ---- 6. Tier 0 token values are unique
all_tier0_tokens = list(WORD_IDS.values()) + list(STRUCTURAL_IDS.values())
assert len(all_tier0_tokens) == len(set(all_tier0_tokens)), \
    'Duplicate token mapped to multiple Tier 0 IDs'
print(f'[OK] {len(all_tier0_tokens)} Tier 0 token values are all distinct')

# ---- 7. STRUCTURAL_IDS includes critical chars for v1 universal formats
required_structural = [' ', '\n', '\t', '.', ',', ':', '"', "'", '(', ')']
for ch in required_structural:
    assert ch in STRUCTURAL_TO_ID, f'Critical structural token missing: {ch!r}'
print(f'[OK] Critical structural chars present: space, \\n, \\t, . , : " \' ( )')

# ---- 8. WORD_TO_ID + STRUCTURAL_TO_ID round-trip
for char, word in WORD_IDS.items():
    assert WORD_TO_ID[word] == char
for char, sym in STRUCTURAL_IDS.items():
    assert STRUCTURAL_TO_ID[sym] == char
print('[OK] WORD_TO_ID and STRUCTURAL_TO_ID round-trip correctly')

# ---- 9. PRIMITIVES_REVERSE round-trip
for k, v in PRIMITIVES.items():
    assert PRIMITIVES_REVERSE[v] == k
print('[OK] PRIMITIVES_REVERSE inverts PRIMITIVES correctly')

# ---- 10. Tier detection
# LENGTH decides. The first character carries no tier information except '-',
# which marks the Tier 4 phrase namespace.
#
# The samples below MUST include leading characters outside the legacy g-z
# range. Until 2026-08-02 every multi-char sample here started with 'g' or '-',
# so the suite passed while detect_tier raised ValueError on every id minted by
# elo-browser-v01c -- it asserted an output it had itself constrained. Real ids
# from that build are marked (v01c) and are the regression guard.
samples = [
    ('T',    0),    # word ID (the)
    ('g',    0),    # structural single char (space)
    ('0',    0),    # system marker
    ('gA',   1),    # Tier 1, legacy alphabet
    ('gAA',  2),    # Tier 2, legacy alphabet
    ('gAAA', 3),    # Tier 3, legacy alphabet
    ('-AAA', 4),    # phrase
    ('AA',   1),    # (v01c) '|'        -- leading 'A', outside g-z
    ('CV',   1),    # (v01c) '_'
    ('1j',   1),    # (v01c) 'car'      -- leading digit
    ('Aq7y', 3),    # (v01c) backtick
]
for tid, expected in samples:
    got = detect_tier(tid)
    assert got == expected, f'detect_tier({tid!r}) = {got}, want {expected}'
# Every non-'-' charset character must be a legal leading character.
for c in TIER_FIRST_CHARS_EXPANDED:
    assert detect_tier(c + 'A') == 1, f'leading {c!r} rejected at Tier 1'
print(f'[OK] Tier detection: length decides; all {len(TIER_FIRST_CHARS_EXPANDED)} '
      f'non-"-" leading chars accepted')

# ---- 11. Tier capacities
# Capacity is a function of the BUILD's leading-character alphabet, not a
# constant -- a hardcoded 1,280 is what made the v01c expansion look like a
# config violation. But assert the RELATIONSHIP *and* known-good literals for
# both alphabets: a relationship checked against itself is a tautology, and on
# 2026-08-02 that tautology passed a tier_capacity_for() written with the wrong
# exponent (64^(n-1)). The literals below are what caught it.
assert TIER_CAPACITY == tier_capacity_for(TIER_WORD_FIRST_CHARS)
assert len(TIER_FIRST_CHARS_EXPANDED) == 63 and '-' not in TIER_FIRST_CHARS_EXPANDED
_exp = tier_capacity_for(TIER_FIRST_CHARS_EXPANDED)
# A Tier n id is n+1 chars: 1 from the alphabet, n from all 64.
assert TIER_CAPACITY[1] ==  1_280 and _exp[1] ==      4_032   # 2-char
assert TIER_CAPACITY[2] == 81_920 and _exp[2] ==    258_048   # 3-char
assert TIER_CAPACITY[3] == 5_242_880 and _exp[3] == 16_515_072  # 4-char
print(f'[OK] Tier capacity legacy  ({len(TIER_WORD_FIRST_CHARS)} chars): '
      f'T1={TIER_CAPACITY[1]:,}  T2={TIER_CAPACITY[2]:,}  T3={TIER_CAPACITY[3]:,}')
print(f'[OK] Tier capacity expanded({len(TIER_FIRST_CHARS_EXPANDED)} chars): '
      f'T1={_exp[1]:,}  T2={_exp[2]:,}  T3={_exp[3]:,}')

# ---- 12. Filler maps still present
print(f'[OK] Multi-word fillers: {len(MULTI_WORD_FILLERS)} (longest-first)')
print(f'[OK] Single-word fillers: {len(SINGLE_WORD_FILLERS)}')

# ---- 13. Compression modes
assert set(COMPRESSION_MODES) == {'STABLE', 'FLEX'}
for mode_cfg in COMPRESSION_MODES.values():
    assert mode_cfg.get('preserve_case') is True
    assert mode_cfg.get('preserve_whitespace') is True
print('[OK] Compression modes: STABLE + FLEX both preserve case + whitespace')

print()
print('=== config.py verification PASSED ===')
print()
print('Tier 0 layout:')
print(f"  {'SLOT':<5}  {'TOKEN'}")
print(f"  {'-'*40}")
print(f"  -- system (5)")
for k in sorted(SYSTEM_IDS):
    print(f"  {k!r:<5}  <{SYSTEM_IDS[k]}>")
print(f"  -- words (26)")
for k in sorted(WORD_IDS):
    print(f"  {k!r:<5}  {WORD_IDS[k]!r}")
print(f"  -- structural (27)")
for k in sorted(STRUCTURAL_IDS):
    v = STRUCTURAL_IDS[k]
    if v == ' ':    shown = '<SPACE>'
    elif v == '\n': shown = '<NEWLINE>'
    elif v == '\t': shown = '<TAB>'
    else:           shown = repr(v)
    print(f"  {k!r:<5}  {shown}")
print(f"  -- reserved (6)")
for k in sorted(RESERVED_IDS):
    print(f"  {k!r:<5}  <{RESERVED_IDS[k]}>")

# ---------------------------------------------------------------------------
# Structure band (Tier 4 carve, 2026-08-21) -- spec-tier-system §5.1.
# The contract file is the source of truth; verify its internal invariants and,
# when a build is given (--db), that the '-' namespace stayed unminted.
# ---------------------------------------------------------------------------
import json as _json
import sys as _sys
from pathlib import Path as _Path

_sc = _json.loads((_Path(__file__).parent / 'data' / 'structure-ids-v2.json')
                  .read_text(encoding='utf-8'))
_CS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
_base = _sc['namespace']['base'][:-1]          # '-S' -- derived, never retyped
assert all(a['atom'] == _base + _CS[a['k']] for a in _sc['atoms']), \
    'structure-ids-v2.json: atom/charset-order mismatch'
# WIRE ENCODABILITY (2026-08-26 ruling): a 4-char '-'/'_'-leading id packs into
# TAG_CAP/TAG_OOV (0xFE/0xFF) and cannot travel on .eloB. No contract may assign one.
assert all(len(a['atom']) == 3 for a in _sc['atoms']), \
    'structure band atoms must be 3-char (4-char -/_ leads are UNENCODABLE on .eloB)'
_opens = {a['name']: a['k'] for a in _sc['atoms'] if a['class'] == 'container_open'}
_closes = {a['name']: a['k'] for a in _sc['atoms'] if a['class'] == 'container_close'}
assert all(_closes[n.replace('_OPEN', '_CLOSE')] == k + 1 for n, k in _opens.items()), \
    'structure-ids-v2.json: container CLOSE != OPEN + 1'
assert len(_sc['atoms']) + _sc['held']['count'] == 64
print(f"[OK] Structure band v2: 41 assigned + 23 held = 64 ('{_base}A'..'{_base}_', 3-char, "
      f"wire byte 0xBE); charset order + close==open+1 + encodability verified")

if '--db' in _sys.argv:
    import lmdb as _lmdb
    _db = _sys.argv[_sys.argv.index('--db') + 1]
    _env = _lmdb.open(_db, readonly=True, lock=False, max_dbs=16)
    _bad = []
    for _name in (b'forward', b'reverse'):
        try:
            _h = _env.open_db(_name, create=False)
        except _lmdb.Error:
            continue
        with _env.begin() as _t:
            for _k, _v in _t.cursor(db=_h):
                _idb = _v if _name == b'forward' else _k
                if _idb.startswith(b'-'):
                    _bad.append((_name.decode(), _idb.decode('utf-8', 'replace')))
                    if len(_bad) >= 5:
                        break
    _env.close()
    assert not _bad, f"'-' namespace MINTED (must never happen): {_bad}"
    print(f"[OK] '-' namespace unminted in {_db} (forward+reverse clean)")
