"""
caps_codec.py verification
Tests: encode_caps, decode_caps, encode_oov, decode_oov, round-trip invariant
"""
import sys
sys.path.insert(0, ".")

from semantic_compression.caps_codec import (
    encode_caps, decode_caps, encode_oov, decode_oov, is_oov_token
)
from semantic_compression.config import BASE64_CHARS

# ---------------------------------------------------------------------------
# 1. Known examples from spec
# ---------------------------------------------------------------------------
cases = [
    # (original,      expected_lower, expected_cap, expected_token)
    ("hello",         "hello",  "A",  "OOV:A:hello"),
    ("Hello",         "hello",  "g",  "OOV:g:hello"),
    ("NASA",          "nasa",   "8",  "OOV:8:nasa"),
    ("iPhone",        "iphone", "Q",  "OOV:Q:iphone"),
    ("HELLO",         "hello",  None, None),   # check round-trip only
    ("McDonald",      "mcdonald","oA","OOV:oA:mcdonald"),
]

for original, exp_lower, exp_cap, exp_token in cases:
    lower, cap = encode_caps(original)
    assert lower == exp_lower, f"{original!r}: lower={lower!r}, want {exp_lower!r}"
    if exp_cap is not None:
        assert cap == exp_cap, (
            f"{original!r}: cap={cap!r} (value={BASE64_CHARS.index(cap[0])}), "
            f"want {exp_cap!r}"
        )
    if exp_token is not None:
        token = encode_oov(original)
        assert token == exp_token, f"{original!r}: token={token!r}, want {exp_token!r}"
    print(f"[OK] {original!r:15}  cap={cap!r:4}  token={encode_oov(original)!r}")

# ---------------------------------------------------------------------------
# 2. Round-trip invariant: decode(encode(word)) == word
# ---------------------------------------------------------------------------
round_trip_words = [
    "hello", "Hello", "HELLO", "hElLo",
    "NASA", "iPhone", "McDonald", "SpeLLeD",
    "a", "I", "A",                              # single chars
    "lowercase",                                 # all lower fast path
    "ALLCAPS",                                   # all upper
    "CamelCase", "camelCase", "mixedMIXED",
    "word123",                                   # with digits (non-alpha -> bit 0)
    "it's",                                      # with apostrophe
    "U.S.A.",                                    # dots
    "superlongwordthatexceedssixchars",           # >6 chars, all lower
    "SuperLongWordThatExceedsSixChars",           # >6 chars, mixed
    "",                                           # empty string edge case
]

print()
for word in round_trip_words:
    token = encode_oov(word)
    recovered = decode_oov(token)
    assert recovered == word, (
        f"Round-trip FAILED: {word!r} -> {token!r} -> {recovered!r}"
    )
print(f"[OK] Round-trip invariant: {len(round_trip_words)} words all pass")

# ---------------------------------------------------------------------------
# 3. All-lowercase fast path
# ---------------------------------------------------------------------------
lower, cap = encode_caps("hello")
assert cap == "A", f"Fast path: expected 'A', got {cap!r}"
assert BASE64_CHARS.index("A") == 0
print("[OK] All-lowercase fast path emits 'A' (value 0)")

# ---------------------------------------------------------------------------
# 4. Non-alpha chars treated as 0-bit (unchanged)
# ---------------------------------------------------------------------------
word = "it's"   # apostrophe at position 2
lower, cap = encode_caps(word)
assert lower == "it's"
recovered = decode_oov(encode_oov(word))
assert recovered == word
print(f"[OK] Non-alpha passthrough: {word!r} -> {encode_oov(word)!r} -> {recovered!r}")

# ---------------------------------------------------------------------------
# 5. CAP_CHAR count = ceil(len(word) / 6)
# ---------------------------------------------------------------------------
import math
for word in ["a", "hello", "McDonald", "superlongwordexceeds"]:
    _, cap = encode_caps(word)
    expected_len = max(1, math.ceil(len(word) / 6)) if word else 1
    # fast path always 1
    if word == word.lower():
        expected_len = 1
    assert len(cap) == expected_len, (
        f"{word!r}: cap_len={len(cap)}, expected {expected_len}"
    )
print("[OK] CAP_CHAR count correct for all word lengths")

# ---------------------------------------------------------------------------
# 6. is_oov_token helper
# ---------------------------------------------------------------------------
assert is_oov_token("OOV:g:hello")
assert is_oov_token("OOV:A:word")
assert not is_oov_token("gA")
assert not is_oov_token("T")
assert not is_oov_token("OOVfake")
print("[OK] is_oov_token correctly identifies OOV tokens")

# ---------------------------------------------------------------------------
# 7. Exhaustive bit correctness check
# ---------------------------------------------------------------------------
# Verify each bit position independently
for pos in range(6):
    word = ['a'] * 6
    word[pos] = 'A'
    word = ''.join(word)
    lower, cap = encode_caps(word)
    expected_value = 1 << (5 - pos)   # MSB-first
    actual_value = BASE64_CHARS.index(cap)
    assert actual_value == expected_value, (
        f"Bit {pos}: value={actual_value}, expected={expected_value}"
    )
print("[OK] All 6 bit positions encode correctly (MSB-first)")

print()
print("=== caps_codec.py verification PASSED ===")
