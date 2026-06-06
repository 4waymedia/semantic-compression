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
# Design principle: IDs are lossless variable pointers to words.
# No semantic annotations in the stream — those live in the DB.
# Every single-char ID decodes back to exactly one word or system signal.
#
# Slot allocation (64 total):
#   A-Z  (26) — Essential 26 word IDs (100% lossless word pointers)
#   0-2   (3) — System stream markers
#   - _   (2) — Stream format separators
#   a-f   (6) — RESERVED: System 2 process stages (do not assign)
#   g-z  (20) — OPEN: future Tier 0 expansion
#   3-9   (7) — OPEN: future Tier 0 expansion
# ---------------------------------------------------------------------------

# System markers
SYSTEM_IDS = {
    '0': 'STREAM_START',
    '1': 'STREAM_END',
    '2': 'CHUNK_BOUNDARY',
    '-': 'ATTR_DELIMITER',    # separates word ID from attribute list in FLEX mode
    '_': 'CONTINUATION',      # token spans next unit
}

# Essential 26 — the highest-frequency words in English, one ID each.
# a/an share one ID (same lemma). All others are distinct lemmas.
WORD_IDS = {
    'A': 'a',      # a / an     — general pointer (article)
    'B': 'be',     # be         — existence / state (is, was, am, are, been)
    'C': 'we',     # we         — collective perspective
    'D': 'do',     # do         — primary action verb (does, did)
    'E': 'he',     # he         — third person masculine
    'F': 'of',     # of         — belonging / composition / origin
    'G': 'to',     # to         — direction / intention / infinitive marker
    'H': 'have',   # have       — possession / past timeline (has, had)
    'I': 'in',     # in         — spatial anchor (interior)
    'J': 'on',     # on         — spatial anchor (surface)
    'K': 'for',    # for        — purpose / reason / benefit
    'L': 'they',   # they       — third person plural
    'M': 'i',      # I          — self / speaker (first person)
    'N': 'and',    # and        — pure addition / bridge
    'O': 'or',     # or         — choice / alternative
    'P': 'not',    # not        — negation
    'Q': 'all',    # all        — maximum scale / entire set
    'R': 'she',    # she        — third person feminine
    'S': 'this',   # this       — near demonstrative pointer
    'T': 'the',    # the        — specific definite pointer (most common word)
    'U': 'it',     # it         — non-human / abstract entity
    'V': 'with',   # with       — accompaniment / tool / connection
    'W': 'will',   # will       — future gateway
    'X': 'but',    # but        — contrast / conflict
    'Y': 'you',    # you        — audience / listener
    'Z': 'that',   # that       — far demonstrative pointer / subordinator
}

# Reserved — labeled but NOT active. Do not use in compression until promoted.
RESERVED_IDS = {
    # System 2 — process stages (Surov 2022). Claim these 6 slots when ready.
    'a': 'RESERVED_STAGE_PERCEPTION',
    'b': 'RESERVED_STAGE_NOVELTY',
    'c': 'RESERVED_STAGE_GOAL_PLAN',
    'd': 'RESERVED_STAGE_ACTION',
    'e': 'RESERVED_STAGE_PROGRESS',
    'f': 'RESERVED_STAGE_RESULT',
    # Open — g-z (20 slots), 3-9 (7 slots): unclaimed
}

# All active single-char IDs (system + words). Reserved are NOT included.
PRIMITIVES = {**SYSTEM_IDS, **WORD_IDS}

# Reverse lookup: word/signal → char ID
PRIMITIVES_REVERSE = {v: k for k, v in PRIMITIVES.items()}

# Fast lookup: lemma string → single-char ID (for the compressor)
WORD_TO_ID = {v: k for k, v in WORD_IDS.items()}

# Validate: no collisions, all chars are valid Base64
assert len(PRIMITIVES) == 31, f"Expected 31 active primitives, got {len(PRIMITIVES)}"
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

# First chars for Tier 1/2/3 word IDs — lowercase g-z only.
# Skips a-f (reserved for System 2 Tier 0 promotion).
TIER_WORD_FIRST_CHARS = 'ghijklmnopqrstuvwxyz'  # 20 chars

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
        # Fast, deterministic — IDs only, no attributes
        'filler_handling':  'word_id_only',    # fillers get their Tier 1/2/3 word ID
        'phrase_detection': 'exact_match',
        'word_lookup':      'lemma_direct',
        'output_format':    'flat_stream',     # "0|T|N|gA|gB|1"
    },
    'FLEX': {
        # Richer — word IDs + inline attributes for System 2
        'filler_handling':  'word_id_plus_class',  # word ID + filler class attribute
        'phrase_detection': 'fuzzy_match',
        'word_lookup':      'semantic_nearest',
        'output_format':    'attributed_stream',   # "0|T|gA-filler:COGNITIVE|1"
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
