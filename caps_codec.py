"""
caps_codec.py — OOV capitalization encoding for System 1

Scope
-----
Applies to OOV tokens only. In-vocabulary tokens (Tier 0/1/2) are always
stored and decoded as lowercase; case restoration for IV words is deferred
to System 2.

Token format  (single pipe-delimited stream element)
-----------------------------------------------------
    OOV:{cap_chars}:{lowercased_word}

    cap_chars   — ceil(len(word) / 6) Base64 characters, each encoding
                  6 bits of capitalization state (1=upper, 0=lower), MSB-first.
                  Non-alpha characters always contribute a 0 bit and are
                  passed through unchanged by the decoder.

    lowercased_word — the word fully lowercased.

Examples (using the project's URL-safe Base64 alphabet)
--------------------------------------------------------
    "hello"     → OOV:A:hello       all-lowercase → bitmask 000000 → 'A'
    "Hello"     → OOV:g:hello       100000 = 32 → BASE64[32] = 'g'
    "NASA"      → OOV:8:nasa        111100 = 60 → BASE64[60] = '8'
    "iPhone"    → OOV:Q:iphone      010000 = 16 → BASE64[16] = 'Q'
    "McDonald"  → OOV:oA:mcdonald   McDona→101000=40→'o', ld????→000000=0→'A'

Invariants
----------
    decode_oov(encode_oov(word)) == word   for any string
    All-lowercase fast path: bitmask all zeros → cap_chars all 'A' → skip restore
"""

from semantic_compression.config import BASE64_CHARS

OOV_SENTINEL = 'OOV'
OOV_SEP      = ':'          # internal OOV field separator (distinct from stream '|')
BITS_PER_CAP = 6            # bits packed into each Base64 CAP_CHAR


# ---------------------------------------------------------------------------
# Core codec
# ---------------------------------------------------------------------------

def encode_caps(word: str) -> tuple[str, str]:
    """
    Encode a word's capitalization pattern into Base64 CAP_CHAR(s).

    Returns:
        (lowercased_word, cap_chars)
        where cap_chars is a string of ceil(len(word)/6) Base64 characters.

    Fast path: if word is already all-lowercase, cap_chars = 'A' (value 0).
    """
    lower = word.lower()

    # Fast path — common for ASR transcripts (all lowercase)
    if word == lower:
        return lower, BASE64_CHARS[0]   # 'A' = 000000

    # Build bit array: 1 = uppercase at this position, 0 = lowercase/non-alpha
    bits = [1 if c.isupper() else 0 for c in word]

    # Pad to next multiple of 6
    remainder = len(bits) % BITS_PER_CAP
    if remainder:
        bits += [0] * (BITS_PER_CAP - remainder)

    # Pack each 6-bit chunk into one Base64 character (MSB-first)
    cap_chars = []
    for i in range(0, len(bits), BITS_PER_CAP):
        chunk = bits[i:i + BITS_PER_CAP]
        value = sum(b << (BITS_PER_CAP - 1 - j) for j, b in enumerate(chunk))
        cap_chars.append(BASE64_CHARS[value])

    return lower, ''.join(cap_chars)


def decode_caps(lower: str, cap_str: str) -> str:
    """
    Restore capitalization from a lowercased word and its CAP_CHAR string.

    Fast path: if all CAP_CHARs are 'A' (value 0), return lower unchanged.
    """
    # Fast path
    if all(c == BASE64_CHARS[0] for c in cap_str):
        return lower

    # Expand each CAP_CHAR into 6 bits
    bits = []
    for ch in cap_str:
        value = BASE64_CHARS.index(ch)
        bits += [(value >> (BITS_PER_CAP - 1 - i)) & 1 for i in range(BITS_PER_CAP)]

    # Apply bits to characters (only use as many bits as there are characters)
    result = []
    for i, ch in enumerate(lower):
        result.append(ch.upper() if bits[i] else ch)

    return ''.join(result)


# ---------------------------------------------------------------------------
# OOV token assembly / parsing
# ---------------------------------------------------------------------------

def encode_oov(word: str) -> str:
    """
    Encode an OOV word as a single stream token.

    Format:  OOV:{cap_chars}:{lowercased_word}
    Example: encode_oov("Hello") → "OOV:g:hello"
    """
    lower, cap_chars = encode_caps(word)
    return f"{OOV_SENTINEL}{OOV_SEP}{cap_chars}{OOV_SEP}{lower}"


def decode_oov(token: str) -> str:
    """
    Decode an OOV stream token back to the original word.

    Expects:  OOV:{cap_chars}:{lowercased_word}
    Example:  decode_oov("OOV:g:hello") → "Hello"
    """
    parts = token.split(OOV_SEP, 2)   # split into at most 3 parts
    if len(parts) != 3 or parts[0] != OOV_SENTINEL:
        raise ValueError(f"Malformed OOV token: {token!r}")
    _, cap_chars, lower = parts
    return decode_caps(lower, cap_chars)


def is_oov_token(token: str) -> bool:
    """Return True if this stream token is an OOV marker."""
    return token.startswith(OOV_SENTINEL + OOV_SEP)
