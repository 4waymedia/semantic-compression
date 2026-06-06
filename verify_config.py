import sys
sys.path.insert(0, ".")
from semantic_compression.config import (
    BASE64_CHARS, PRIMITIVES, SYSTEM_IDS, WORD_IDS, RESERVED_IDS,
    WORD_TO_ID, TIER_WORD_FIRST_CHARS, TIER_CAPACITY, MULTI_WORD_FILLERS,
    SINGLE_WORD_FILLERS, COMPRESSION_MODES, detect_tier,
)

# 1. Charset
assert len(BASE64_CHARS) == 64 and len(set(BASE64_CHARS)) == 64
print("[OK] Charset: 64 unique chars")

# 2. Primitives
assert len(SYSTEM_IDS) == 5
assert len(WORD_IDS) == 26
assert len(PRIMITIVES) == 31
assert all(k in BASE64_CHARS for k in PRIMITIVES)
print(f"[OK] Primitives: {len(SYSTEM_IDS)} system + {len(WORD_IDS)} words = {len(PRIMITIVES)} active")

# 3. No collision between active and reserved
assert not (set(PRIMITIVES) & set(RESERVED_IDS)), "Active/reserved collision"
print(f"[OK] Reserved: {len(RESERVED_IDS)} slots (a-f, System 2 stages)")

# 4. All 26 uppercase letters used as word IDs
assert set(WORD_IDS.keys()) == set('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
print("[OK] All 26 uppercase letters assigned as word IDs")

# 5. Open reserved slots count
open_slots = 64 - len(PRIMITIVES) - len(RESERVED_IDS)
print(f"[OK] Open reserved: {open_slots} slots (g-z + 3-9)")

# 6. WORD_TO_ID round-trips
for char, word in WORD_IDS.items():
    assert WORD_TO_ID[word] == char, f"Round-trip failed for {word!r}"
print("[OK] WORD_TO_ID: all 26 words round-trip correctly")

# 7. Tier detection
samples = [
    ('T',    0),   # Tier 0 word — 'the'
    ('0',    0),   # Tier 0 system — STREAM_START
    ('gA',   1),   # Tier 1 — 2-char, g prefix
    ('gAA',  2),   # Tier 2 — 3-char, g prefix
    ('gAAA', 3),   # Tier 3 — 4-char, g prefix
    ('-AAA', 4),   # Phrase
]
for tid, expected in samples:
    got = detect_tier(tid)
    assert got == expected, f"detect_tier({tid!r}) = {got}, want {expected}"
print("[OK] Tier detection: all samples correct")

# 8. Tier word first chars
assert len(TIER_WORD_FIRST_CHARS) == 20
assert 'a' not in TIER_WORD_FIRST_CHARS  # reserved for System 2
assert 'g' in TIER_WORD_FIRST_CHARS
print(f"[OK] Tier word first chars: {len(TIER_WORD_FIRST_CHARS)} (g-z, a-f excluded)")

# 9. Capacities
assert TIER_CAPACITY[1] == 1280
assert TIER_CAPACITY[2] == 81920
print(f"[OK] Tier capacities: T1={TIER_CAPACITY[1]:,}  T2={TIER_CAPACITY[2]:,}  T3={TIER_CAPACITY[3]:,}")

# 10. Filler maps
print(f"[OK] Multi-word fillers: {len(MULTI_WORD_FILLERS)} (longest-first)")
print(f"[OK] Single-word fillers: {len(SINGLE_WORD_FILLERS)}")

# 11. Compression modes
assert set(COMPRESSION_MODES) == {'STABLE', 'FLEX'}
print("[OK] Compression modes: STABLE + FLEX defined")

print()
print("=== config.py verification PASSED ===")
print()
print("Tier 0 word map:")
print(f"  {'ID':3}  {'WORD'}")
print(f"  {'-'*20}")
for char in sorted(WORD_IDS):
    print(f"  {char!r:3}  {WORD_IDS[char]}")
