"""verify_config.py — validates the Tier 0 layout in config.py"""
import sys
sys.path.insert(0, '.')

from semantic_compression.config import (
    BASE64_CHARS, PRIMITIVES, PRIMITIVES_REVERSE,
    SYSTEM_IDS, WORD_IDS, STRUCTURAL_IDS, RESERVED_IDS,
    WORD_TO_ID, STRUCTURAL_TO_ID,
    TIER_WORD_FIRST_CHARS, TIER_CAPACITY,
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
# Length disambiguates: single char = Tier 0, 2/3/4 = Tier 1/2/3
samples = [
    ('T',    0),    # word ID (the)
    ('g',    0),    # structural single char (space)
    ('0',    0),    # system marker
    ('gA',   1),    # Tier 1 dictionary entry
    ('gAA',  2),    # Tier 2
    ('gAAA', 3),    # Tier 3
    ('-AAA', 4),    # phrase
]
for tid, expected in samples:
    got = detect_tier(tid)
    assert got == expected, f'detect_tier({tid!r}) = {got}, want {expected}'
print('[OK] Tier detection: length disambiguates Tier 0 from 1/2/3')

# ---- 11. Tier capacities
assert TIER_CAPACITY[1] == 1280
assert TIER_CAPACITY[2] == 81920
assert TIER_CAPACITY[3] == 5_242_880
print(f'[OK] Tier capacities: T1={TIER_CAPACITY[1]:,}  T2={TIER_CAPACITY[2]:,}  T3={TIER_CAPACITY[3]:,}')

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
