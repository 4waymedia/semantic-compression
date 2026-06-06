# ---------------------------------------------------------------------------
# Format versioning — increment if stream format changes (C reader checks this)
# ---------------------------------------------------------------------------
FORMAT_VERSION  = 1        # embedded in every .elo file header
STREAM_ENCODING = 'utf-8'  # all text in streams and LMDB keys/values
PIPE_BYTE       = 0x7C     # b'|' — stream token delimiter, never changes
OOV_SEP_BYTE    = 0x3A     # b':' — OOV internal field delimiter

# Storage convention: all integer values packed as little-endian uint32
# import struct; struct.pack('<I', frequency)   — C can read this
# Never use pickle for stored values.

# ---------------------------------------------------------------------------
# URL-safe Base64 charset (no +, no /)
# ---------------------------------------------------------------------------
BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

# Reverse lookup: char → index
BASE64_INDEX = {ch: i for i, ch in enumerate(BASE64_CHARS)}


# ---------------------------------------------------------------------------
# Tier 0 — single-char IDs
#
# System 1 v1 principle:
#   Surface form = ID lookup key.
#   No lemmatization.
#   No lowercasing before tokenization.
#   No whitespace collapse.
#
# Tier 0 should prioritize:
#   1. System stream markers
#   2. High-frequency universal words
#   3. Byte-exact structural tokens: whitespace + punctuation
#   4. Reserved System 2 process slots
# ---------------------------------------------------------------------------

SYSTEM_IDS = {
    '0': 'STREAM_START',
    '1': 'STREAM_END',
    '2': 'CHUNK_BOUNDARY',
    '-': 'ATTR_DELIMITER',
    '_': 'CONTINUATION',
}

WORD_IDS = {
    'A': 'a',
    'B': 'be',
    'C': 'we',
    'D': 'do',
    'E': 'he',
    'F': 'of',
    'G': 'to',
    'H': 'have',
    'I': 'in',
    'J': 'on',
    'K': 'for',
    'L': 'they',
    'M': 'i',
    'N': 'and',
    'O': 'or',
    'P': 'not',
    'Q': 'all',
    'R': 'she',
    'S': 'this',
    'T': 'the',
    'U': 'it',
    'V': 'with',
    'W': 'will',
    'X': 'but',
    'Y': 'you',
    'Z': 'that',
}

STRUCTURAL_IDS = {
    'g': ' ',
    'h': '\n',
    'i': '\t',
    'j': '.',
    'k': ',',
    'l': ':',
    'm': ';',
    'n': '!',
    'o': '?',
    'p': "'",
    'q': '"',
    'r': '(',
    's': ')',
    't': '[',
    'u': ']',
    'v': '/',
    'w': '\\',
    'x': '-',
    'y': '—',
    'z': '&',
    '3': '%',
    '4': '$',
    '5': '#',
    '6': '@',
    '7': '*',
    '8': '+',
    '9': '=',
}

RESERVED_IDS = {
    'a': 'RESERVED_STAGE_PERCEPTION',
    'b': 'RESERVED_STAGE_NOVELTY',
    'c': 'RESERVED_STAGE_GOAL_PLAN',
    'd': 'RESERVED_STAGE_ACTION',
    'e': 'RESERVED_STAGE_PROGRESS',
    'f': 'RESERVED_STAGE_RESULT',
}

PRIMITIVES = {
    **SYSTEM_IDS,
    **WORD_IDS,
    **STRUCTURAL_IDS,
}

PRIMITIVES_REVERSE = {v: k for k, v in PRIMITIVES.items()}

WORD_TO_ID = {v: k for k, v in WORD_IDS.items()}
STRUCTURAL_TO_ID = {v: k for k, v in STRUCTURAL_IDS.items()}

assert len(PRIMITIVES) == 58, f"Expected 58 active primitives, got {len(PRIMITIVES)}"
assert all(k in BASE64_CHARS for k in PRIMITIVES), "Primitive key outside Base64 charset"
assert all(k in BASE64_CHARS for k in RESERVED_IDS), "Reserved key outside Base64 charset"
assert not (set(PRIMITIVES) & set(RESERVED_IDS)), "Collision between active and reserved IDs"

# ---------------------------------------------------------------------------
# Tier system — word library ID tiers
#
# Tier 0: single-char (defined above)
# Tier 1: 2-char, first char from g-z  →  20 × 64 = 1,280 IDs
# Tier 2: 3-char, first char from g-z  →  20 × 64² = 81,920 IDs
# Tier 3: 4-char, first char from g-z  →  20 × 64³ = 5,242,880 IDs
# Phrase: 4-char, first char = '-'     →  64³ = 262,144 IDs
#
# Tier detection: first char + length (no ambiguity with Tier 0 since
# active Tier 0 uses A-Z + 0-2 + -_, and Tier 1-3 use g-z as first char).
# ---------------------------------------------------------------------------

# First chars for Tier 1/2/3 dictionary IDs.
# Important:
#   g-z and 3-9 are now active Tier 0 structural IDs when length == 1.
#   They are still valid first chars for multi-character Tier 1/2/3 IDs.
#   Tier detection remains unambiguous because length determines Tier 0.
TIER_WORD_FIRST_CHARS = 'ghijklmnopqrstuvwxyz'

TIER_CAPACITY = {
    1: len(TIER_WORD_FIRST_CHARS) * 64,            # 1,280
    2: len(TIER_WORD_FIRST_CHARS) * 64 ** 2,       # 81,920
    3: len(TIER_WORD_FIRST_CHARS) * 64 ** 3,       # 5,242,880
}

TIER_FREQ_RANK = {
    1: (1, 1000),       # top ~1,000 by corpus frequency (capped at Tier 1 capacity)
    2: (1001, 10000),   # next 9,000
    3: (10001, None),   # remainder (unbounded)
}


def detect_tier(token_id: str) -> int:
    """Return tier of a token ID without a DB lookup."""
    n = len(token_id)
    if n == 1:
        return 0
    first = token_id[0]
    if first == '-':
        return 4   # phrase / collocation / pragmatic
    if first in TIER_WORD_FIRST_CHARS:
        if n == 2:
            return 1
        if n == 3:
            return 2
        if n == 4:
            return 3
    raise ValueError(f"Unknown tier for ID: {token_id!r}")


# ---------------------------------------------------------------------------
# Filler classification
# (Fillers are regular words in the library — no special Tier 0 slot.
#  filler_detector.py uses these maps to tag filler occurrences for System 2.)
# ---------------------------------------------------------------------------

FILLER_MAP = {
    'COGNITIVE': [    # um, uh, er, hmm — processing delay / cognitive load
        'uhh', 'umm', 'uhm', 'um', 'uh', 'er', 'hmm',
    ],
    'DISCOURSE': [    # like, so, right — turn management / floor-holding
        'alright', 'okay', 'right', 'well', 'like', 'now', 'so',
    ],
    'VALIDATION': [   # you know — seeking listener confirmation
        'you know what i mean', 'know what i mean', 'know what im saying',
        'you feel me', 'you see what i mean', 'you know',
    ],
    'HEDGE': [        # kind of, sort of — softening a claim
        'more or less', 'something like', 'pretty much', 'kind of',
        'sort of', 'basically', 'in a way', 'almost',
    ],
    'EMPHASIS': [     # literally, honestly — amplifying a claim
        'i mean it', 'absolutely', 'definitely', 'seriously', 'genuinely',
        'literally', 'honestly', 'actually', 'truly',
    ],
    'EMOTIONAL': [    # i mean, look — signalling emotional/important content ahead
        'here is the thing', 'let me tell you', 'i will say this',
        'hear me out', 'the thing is', 'i mean', 'listen', 'look',
    ],
}

# Weight deltas for System 2 probability adjustment on adjacent tokens
FILLER_WEIGHT_DELTA = {
    'COGNITIVE':  {'certainty': -0.2, 'cognitive_load':   +0.3},
    'DISCOURSE':  {'certainty':  0.0, 'transition':        +0.2},
    'VALIDATION': {'certainty': -0.1, 'validation_need':  +0.3},
    'HEDGE':      {'certainty': -0.3, 'commitment':        -0.2},
    'EMPHASIS':   {'certainty': +0.3, 'commitment':        +0.3},
    'EMOTIONAL':  {'emotional_signal': +0.4, 'importance': +0.3},
}

# Flat set of all filler surface forms for fast membership testing
ALL_FILLERS: set[str] = {f for forms in FILLER_MAP.values() for f in forms}

# Multi-word fillers sorted longest-first for greedy matching
MULTI_WORD_FILLERS: list[tuple[str, str]] = sorted(
    [(f, cls) for cls, forms in FILLER_MAP.items() for f in forms if ' ' in f],
    key=lambda x: -len(x[0].split()),
)

# Single-word fillers: surface → class
SINGLE_WORD_FILLERS: dict[str, str] = {
    f: cls
    for cls, forms in FILLER_MAP.items()
    for f in forms
    if ' ' not in f
}


# ---------------------------------------------------------------------------
# Compression modes
# ---------------------------------------------------------------------------

COMPRESSION_MODES = {
    'STABLE': {
        'filler_handling':  'word_id_only',
        'phrase_detection': 'exact_match',
        'word_lookup':      'surface_direct',
        'output_format':    'flat_stream',
        'preserve_case':    True,
        'preserve_whitespace': True,
    },
    'FLEX': {
        'filler_handling':  'word_id_plus_class',
        'phrase_detection': 'fuzzy_match',
        'word_lookup':      'surface_direct',
        'output_format':    'attributed_stream',
        'preserve_case':    True,
        'preserve_whitespace': True,
    },
}


# ---------------------------------------------------------------------------
# Paths and model constants
# ---------------------------------------------------------------------------

DB_PATH    = 'semantic_compression/db/canonical.db'
FAISS_PATH = 'semantic_compression/db/faiss.index'

TRANSCRIPT_DIR = 'Resources/transcripts'
COMPRESSED_DIR = 'semantic_compression/data/compressed'

EMBEDDING_MODEL = 'all-mpnet-base-v2'
EMBEDDING_DIM   = 768

SPACY_MODEL = 'en_core_web_sm'
