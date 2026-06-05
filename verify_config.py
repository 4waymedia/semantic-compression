import sys
sys.path.insert(0, ".")
from semantic_compression.config import (
    BASE64_CHARS, PRIMITIVES, FILLER_PRIMITIVES,
    MULTI_WORD_FILLERS, SINGLE_WORD_FILLERS, COMPRESSION_MODES,
    detect_tier
)

assert len(BASE64_CHARS) == 64 and len(set(BASE64_CHARS)) == 64
print("[OK] Charset: 64 unique chars")

assert len(PRIMITIVES) == 38
assert all(k in BASE64_CHARS for k in PRIMITIVES)
print(f"[OK] Primitives: 38 defined, {64 - 38} open reserved slots")

assert FILLER_PRIMITIVES <= set(PRIMITIVES.keys())
print(f"[OK] Filler primitives: {sorted(FILLER_PRIMITIVES)}")

assert not (FILLER_PRIMITIVES & {"-", "_"})
print("[OK] No filler/separator collision")

samples = [("AA", 1), ("KAA", 2), ("aAA", 2), ("kAAA", 3), ("0AAA", 3), ("-AAA", 4)]
for tid, expected in samples:
    got = detect_tier(tid)
    assert got == expected, f"detect_tier({tid!r}) = {got}, want {expected}"
print("[OK] Tier detection: all samples correct")

print(f"[OK] Multi-word fillers: {len(MULTI_WORD_FILLERS)} entries (longest-first)")
print(f"[OK] Single-word fillers: {len(SINGLE_WORD_FILLERS)} entries")
print(f"     Top 3 longest: {[f for f, _ in MULTI_WORD_FILLERS[:3]]}")

assert set(COMPRESSION_MODES.keys()) == {"STABLE", "FLEX"}
print("[OK] Compression modes: STABLE + FLEX defined")

print()
print("=== Step 1 verification PASSED ===")
