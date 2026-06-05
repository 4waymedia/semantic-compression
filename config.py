# URL-safe Base64 charset (no +, no /)
BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

# Reverse lookup: char → index
BASE64_INDEX = {ch: i for i, ch in enumerate(BASE64_CHARS)}

# ---------------------------------------------------------------------------
# Tier system
# ---------------------------------------------------------------------------

TIER_BOUNDARIES = {
    0: (1, 64),          # 1-char: 64 reserved primitives
    1: (2, 4096),        # 2-char: top ~1,000 by frequency
    2: (3, 262144),      # 3-char: mid-frequency words
    3: (4, 16777216),    # 4-char: low-freq / high-meaning words
    4: (4, None),        # 4-char, '-' prefix: phrases
    5: (4, None),        # 4-char, '-' prefix: collocations
    6: (4, None),        # 4-char, '-' prefix: pragmatic formulas
}

TIER_FREQ_RANK = {
    1: (1, 1000),
    2: (1001, 10000),
    3: (10001, None),    # None = unbounded
}

# First character encodes tier for fast detection without DB lookup
TIER_FIRST_CHARS = {
    1: set(BASE64_CHARS[0:10]),    # A-J        → 2-char IDs (Tier 1)
    2: set(BASE64_CHARS[10:36]),   # K-Z + a-j  → 3-char IDs (Tier 2)
    3: set(BASE64_CHARS[36:62]),   # k-z + 0-9  → 4-char IDs (Tier 3)
    4: {'-'},                      # '-' → phrase IDs (tiers 4, 5, 6)
}
# Tier 0: the ID IS a single char (no first-char prefix needed)

def detect_tier(token_id: str) -> int:
    """Return tier of a token ID without a DB lookup."""
    if len(token_id) == 1:
        return 0
    first = token_id[0]
    if first == '-':
        return 4  # phrases/collocations/pragmatic — subtype stored in DB
    if first in TIER_FIRST_CHARS[1]:
        return 1
    if first in TIER_FIRST_CHARS[2]:
        return 2
    if first in TIER_FIRST_CHARS[3]:
        return 3
    raise ValueError(f"Unknown tier for ID: {token_id!r}")


# ---------------------------------------------------------------------------
# Tier 0 — 64 primitive slots
# ---------------------------------------------------------------------------

PRIMITIVES = {
    # SYSTEM / RESERVED (4)
    'A': 'NULL',
    'B': 'STREAM_START',
    'C': 'STREAM_END',
    'D': 'CHUNK_BOUNDARY',

    # PROCESS STAGES — Surov (2022) (6)
    'E': 'STAGE_PERCEPTION',
    'F': 'STAGE_NOVELTY',
    'G': 'STAGE_GOAL_PLAN',
    'H': 'STAGE_ACTION',
    'I': 'STAGE_PROGRESS',
    'J': 'STAGE_RESULT',

    # EPA POLES (6)
    'K': 'EPA_EVAL_POS',       # +Evaluation
    'L': 'EPA_EVAL_NEG',       # -Evaluation
    'M': 'EPA_POTENCY_POS',    # +Potency (strong)
    'N': 'EPA_POTENCY_NEG',    # -Potency (weak)
    'O': 'EPA_ACTIVITY_POS',   # +Activity (active)
    'P': 'EPA_ACTIVITY_NEG',   # -Activity (passive)

    # FILLER CLASSES — always preserved, never stripped (6)
    'Q': 'FILLER_COGNITIVE',   # um, uh, er, hmm       → cognitive load / processing
    'R': 'FILLER_DISCOURSE',   # like, so, right, okay → turn management
    'S': 'FILLER_VALIDATION',  # you know, know what i mean → seeking confirmation
    'T': 'FILLER_HEDGE',       # kind of, sort of, basically → softening claim
    'U': 'FILLER_EMPHASIS',    # literally, honestly, actually → amplifying claim
    'V': 'FILLER_EMOTIONAL',   # i mean, look, listen  → emotional state shift incoming

    # TENSE (3)
    'W': 'TENSE_PAST',
    'X': 'TENSE_PRESENT',
    'Y': 'TENSE_FUTURE',

    # LOGIC / BOOLEAN (4)
    'Z': 'AFFIRM',             # true / yes / agree
    '0': 'DENY',               # false / no / disagree
    '1': 'QUESTION',
    '2': 'CONTRAST',           # but / however / although

    # POLARITY (2)
    '3': 'POLARITY_POS',
    '4': 'POLARITY_NEG',

    # CERTAINTY (2)
    '5': 'CERTAIN',
    '6': 'UNCERTAIN',

    # STRUCTURAL (3)
    '7': 'SENTENCE_BOUNDARY',
    '8': 'TOPIC_SHIFT',
    '9': 'REPETITION',         # this concept was stated before

    # SEPARATORS (2)
    '-': 'ATTR_DELIMITER',     # separates ID from attribute list
    '_': 'CONTINUATION',       # token continues on next unit

    # OPEN RESERVED (14) — do not assign until System 2
    # a b c d e f g h i j k l m n
}

# Reverse lookup: semantic name → primitive char
PRIMITIVE_REVERSE = {v: k for k, v in PRIMITIVES.items()}

# Filler primitive chars (always preserved in compression output)
FILLER_PRIMITIVES = {'Q', 'R', 'S', 'T', 'U', 'V'}

assert len(PRIMITIVES) == 38, f"Expected 38 defined primitives, got {len(PRIMITIVES)}"
assert len(set(PRIMITIVES.keys())) == len(PRIMITIVES), "Duplicate primitive keys"


# ---------------------------------------------------------------------------
# Filler classification
# ---------------------------------------------------------------------------

# Ordered longest-match first within each class to prevent partial matches
FILLER_MAP = {
    'Q': [  # Cognitive load — processing delay
        'uhh', 'umm', 'uhm', 'um', 'uh', 'er', 'hmm',
    ],
    'R': [  # Discourse management — turn-holding
        'alright', 'okay', 'right', 'well', 'like', 'now', 'and', 'so',
    ],
    'S': [  # Validation seeking
        'you know what i mean', 'know what i mean', 'know what im saying',
        'you feel me', 'you see what i mean', 'you know',
    ],
    'T': [  # Hedging — epistemic uncertainty
        'more or less', 'something like', 'pretty much', 'kind of',
        'sort of', 'basically', 'in a way', 'almost',
    ],
    'U': [  # Emphasis / certainty amplifier
        'i mean it', 'absolutely', 'definitely', 'seriously', 'genuinely',
        'literally', 'honestly', 'actually', 'truly',
    ],
    'V': [  # Emotional marker — high-valence content incoming
        'here is the thing', 'let me tell you', 'i will say this',
        'hear me out', 'the thing is', 'i mean', 'listen', 'look',
    ],
}

# Probability weight deltas applied to adjacent Tier 3 tokens
FILLER_WEIGHT_DELTA = {
    'Q': {'certainty': -0.2, 'cognitive_load': +0.3},
    'R': {'certainty':  0.0, 'transition':     +0.2},
    'S': {'certainty': -0.1, 'validation_need': +0.3},
    'T': {'certainty': -0.3, 'commitment':     -0.2},
    'U': {'certainty': +0.3, 'commitment':     +0.3},
    'V': {'emotional_signal': +0.4, 'importance': +0.3},
}

# Flat set of all filler surface forms for fast membership testing
ALL_FILLERS: set[str] = {form for forms in FILLER_MAP.values() for form in forms}

# Sorted list of multi-word fillers (longest first) for greedy matching
MULTI_WORD_FILLERS: list[tuple[str, str]] = sorted(
    [(form, cls) for cls, forms in FILLER_MAP.items() for form in forms if ' ' in form],
    key=lambda x: -len(x[0].split()),
)

# Single-word fillers as a dict: surface → class
SINGLE_WORD_FILLERS: dict[str, str] = {
    form: cls
    for cls, forms in FILLER_MAP.items()
    for form in forms
    if ' ' not in form
}


# ---------------------------------------------------------------------------
# Compression modes
# ---------------------------------------------------------------------------

COMPRESSION_MODES = {
    'STABLE': {
        'filler_handling':  'collapse_to_primitive',  # "um um um" → "Q3"
        'phrase_detection': 'exact_match',
        'word_lookup':      'lemma_direct',
        'attributes':       'base_only',
        'output_format':    'flat_stream',
    },
    'FLEX': {
        'filler_handling':  'preserve_full',           # primitive + count + position + next
        'phrase_detection': 'fuzzy_match',
        'word_lookup':      'semantic_nearest',
        'attributes':       'full_dynamic',
        'output_format':    'attributed_stream',
    },
}


# ---------------------------------------------------------------------------
# Database paths (relative to project root)
# ---------------------------------------------------------------------------

DB_PATH    = 'semantic_compression/db/canonical.db'
FAISS_PATH = 'semantic_compression/db/faiss.index'

TRANSCRIPT_DIR  = 'Resources/transcripts'
COMPRESSED_DIR  = 'semantic_compression/data/compressed'

# Embedding model (sentence-transformers)
EMBEDDING_MODEL = 'all-mpnet-base-v2'
EMBEDDING_DIM   = 768

# spaCy model
SPACY_MODEL = 'en_core_web_sm'
