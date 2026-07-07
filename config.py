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
        'uhh', 'umm', 'uhm', 'um', 'uh', 'er', 'erm', 'hmm',
        'ah', 'oh', 'huh',   # 2026-07-05 B1: vocal reactions
    ],
    'DISCOURSE': [    # like, so, right — turn management / floor-holding
        'alright', 'anyway', 'okay', 'right', 'well', 'like', 'so',
        'whatever', 'blah', 'blah blah',   # 2026-07-05 B1: dismissive/placeholder discourse
        # 'now' removed 2026-07-05: corpus-measured temporal ~6:1 (triage);
        # resumptive discourse sense deferred to instance level (stance layer)
    ],
    'VALIDATION': [   # you know — seeking listener confirmation
        'you know what i mean', 'know what i mean', 'know what im saying',
        'you feel me', 'you see what i mean', 'you know',
        "y'know", 'you see',   # 2026-07-05 B1
    ],
    'HEDGE': [        # kind of, sort of — softening a claim
        'more or less', 'something like', 'pretty much', 'kind of',
        'sort of', 'basically', 'in a way', 'almost',
    ],
    'EMPHASIS': [     # literally, honestly — amplifying a claim
        'i mean it', 'absolutely', 'definitely', 'seriously', 'genuinely',
        'literally', 'honestly', 'actually', 'truly',
    ],
    'EMOTIONAL': [  # 'listen' removed 2026-07-05: corpus verb ~5:1; stance layer recovers the marker    # i mean, look — signalling emotional/important content ahead
        'hey', 'frankly',   # 2026-07-05 B1: attention-getter / honesty emphasis
        'here is the thing', 'let me tell you', 'i will say this',
        'hear me out', 'the thing is', 'i mean', 'look',
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
# ===========================================================================
# Semantic Facets (spec-facets-db.md v2) — System 1 static annotation
#
# A 4-byte, C-readable facet record keyed by the same id_bytes as forward/reverse:
#     byte 0      semantic_bucket   (uint8)
#     bytes 1-2   logic_cue_mask    (uint16 little-endian, composable)
#     byte 3      flags             (properties + utility)
# Read by byte offset; pack/unpack with struct '<BHB'. No model inference.
# ===========================================================================

import struct  # C-readable fixed-width packing for facet records + meta ints

FACETS_FORMAT_VERSION   = 1
NORMALIZATION_VERSION = 1
FACET_RECORD_WIDTH      = 4            # bytes; stored in meta as record_width

# LMDB sub-database names (max_dbs raised 2 -> 4 when facets present).
# NOTE: this `facets` DB is unrelated to the transcript JSON `tags` field (transcript metadata).
FORWARD_DB_NAME = b'forward'
REVERSE_DB_NAME = b'reverse'
FACETS_DB_NAME    = b'facets'
META_DB_NAME    = b'meta'

# --- Byte 0: semantic bucket -----------------------------------------------
BUCKET = {
    'UNKNOWN':    0x00,   # unclassified (safe default; never silently dropped)
    'TOPIC':      0x01,   # noun-class thing / subject
    'METHOD':     0x02,   # action / process (assigned conservatively)
    'CONCEPT':    0x03,   # multi-word DOMAIN concept
    'RELATION':   0x04,   # relational / connective unit
    'STRUCTURAL': 0x05,   # whitespace, punctuation, sentinels
    # 0x06-0x0F reserved (MODIFIER, NAMED_ENTITY, EVENT, ...)
}
BUCKET_NAME = {v: k for k, v in BUCKET.items()}

# --- Bytes 1-2: logic-cue mask (uint16 LE, composable affordances) ---------
LOGIC_CUE = {
    'CLAIM_CUE':      0x0001,
    'EVIDENCE_CUE':   0x0002,
    'INFERENCE':      0x0004,
    'CONTRAST':       0x0008,
    'CAUSE':          0x0010,
    'CONDITION':      0x0020,
    'QUANTIFIER':     0x0040,
    'NEGATION':       0x0080,
    'CONJUNCTION':    0x0100,
    'QUESTION':       0x0200,
    'MODAL':          0x0400,
    'DEFINITION_CUE': 0x0800,
    'CONCESSION':     0x1000,
    'COMPARISON':     0x2000,
    'TEMPORAL':       0x4000,
    # 0x8000 reserved
}
LOGIC_CUE_NAME = {v: k for k, v in LOGIC_CUE.items()}

# --- Byte 3: flags (properties + utility) ----------------------------------
FLAG = {
    'MULTIWORD':    0x01,   # surface contains a space (phrase atom)
    'CLOSED_CLASS': 0x02,   # matched a closed-class connective/function list
    'MANUAL':       0x04,   # assigned by override file (authoritative)
    'HEURISTIC':    0x08,   # assigned by default guess (MANUAL XOR HEURISTIC)
    'AMBIGUOUS':    0x10,   # assignment itself is unreliable
    # 0x20 reserved
}
FLAG_NAME = {v: k for k, v in FLAG.items()}

# UTILITY occupies the top two flag bits (0xC0): the "meaningfulness" axis
# that replaces the old SKIP bucket.
UTILITY = {
    'CONTENT':    0b00,
    'FUNCTION':   0b01,
    'STRUCTURAL': 0b10,
    'FILLER':     0b11,
}
UTILITY_NAME  = {v: k for k, v in UTILITY.items()}
UTILITY_SHIFT = 6
UTILITY_MASK  = 0xC0

# --- Logic seed lists (closed-class, frozen, FIXED ORDER) ------------------
# Ordered so overlap resolution is deterministic. Cue bits compose (OR);
# overlap across lists is composition, NOT ambiguity. Content words are never
# seeded. Surfaces are lowercased through normalize_surface() at load.
LOGIC_SEED_LISTS = [
    ('INFERENCE',      ['therefore', 'thus', 'hence', 'so', 'consequently', 'accordingly', 'for example']),
    ('EVIDENCE_CUE',   ['because', 'since', 'given', 'shows', 'demonstrates', 'indicates', 'according', 'for example']),
    ('CONTRAST',       ['but', 'however', 'although', 'though', 'yet', 'whereas', 'nonetheless', 'nevertheless', 'on the other hand']),
    ('CAUSE',          ['because', 'since', 'due', 'cause', 'causes', 'caused', 'owing', 'so']),
    ('CONDITION',      ['if', 'unless', 'when', 'whenever', 'provided', 'assuming']),
    ('QUANTIFIER',     ['all', 'some', 'most', 'many', 'few', 'none', 'every', 'each', 'any', 'both']),
    ('NEGATION',       ['not', 'no', 'never', 'none', 'cannot', 'nor']),
    ('CONJUNCTION',    ['and', 'or', 'nor', 'also', 'then', 'furthermore', 'moreover', 'additionally', 'plus', 'besides']),
    ('QUESTION',       ['what', 'why', 'how', 'when', 'where', 'who', 'which', 'whom', 'whose']),
    ('MODAL',          ['can', 'could', 'must', 'might', 'may', 'shall', 'should', 'would', 'will', 'ought']),
    ('DEFINITION_CUE', ['is', 'are', 'means', 'refers', 'denotes', 'defined', 'constitutes']),
    ('CONCESSION',     ['admittedly', 'granted', 'regardless', 'despite', 'notwithstanding', 'although', 'while']),
    ('COMPARISON',     ['like', 'than', 'as', 'similarly', 'likewise', 'versus', 'compared']),
    ('TEMPORAL',       ['when', 'while', 'before', 'after', 'during', 'until', 'then']),
]

# Cue categories that make a token a pure connective -> bucket = RELATION.
RELATION_CUES = frozenset({
    'INFERENCE', 'EVIDENCE_CUE', 'CONTRAST', 'CAUSE', 'CONDITION',
    'CONJUNCTION', 'DEFINITION_CUE', 'CONCESSION', 'COMPARISON',
})

# --- Abstraction tells (System-1 PARTIAL; full concrete/abstract is S2/EPA) --
# Shared by facets (abstract single-word noun -> CONCEPT) and meta_fields (the
# abstraction dimension) so bucket and abstraction agree. Concrete detection is
# intentionally deferred to the S2 EPA layer -> non-abstract surfaces stay an
# honest None, never guessed 'concrete'.
ABSTRACT_SUFFIXES = (
    'tion', 'sion', 'ment', 'ness', 'ity', 'ship', 'dom', 'hood', 'ism',
    'ance', 'ence', 'cy', 'acy', 'ology', 'graphy', 'logy',
)
# Small curated lexicon of common abstract nouns that lack an abstract suffix.
# Seed list -- grow deliberately; superseded by the S2/EPA abstraction signal.
ABSTRACT_LEXICON = frozenset({
    'love', 'justice', 'marriage', 'profit', 'freedom', 'liberty', 'truth',
    'hope', 'fear', 'faith', 'peace', 'idea', 'honor', 'honour', 'pride',
    'courage', 'wisdom', 'virtue',
    # 2026-07-05 gold-v2 triage F1: abstract nouns the suffix tell misses
    'beauty', 'culture', 'ethics', 'knowledge', 'poverty', 'power',
    'strategy', 'state',
    # 2026-07-05 review-queue B2: norms-OOV abstract nouns (single-model gold)
    'algorithm', 'data', 'framework', 'learning', 'matter', 'milestone',
    'model', 'portfolio',
})

# Suffix false-positives: words ending in an abstract suffix that denote
# concrete referents (triage F6: 'city' fired on '-ity').
CONCRETE_SUFFIX_EXCEPTIONS = frozenset({'city', 'university'})

# --- Norms-informed abstraction tell (2026-07-05, review-queue B2) ----------
# Brysbaert et al. (2014) single-word ratings, generated deterministic file
# (data/concreteness_s1_v1.tsv). A rated word's verdict TRUMPS the suffix
# guess in both directions (kills city-class false positives AND catches
# suffix-less abstracts like 'belief'). Unrated words fall through to the
# curated lexicon + suffixes. Lazy-loaded once; still S1: static file, no
# inference; C port reads the same TSV.
_NORMS_ABSTRACT: frozenset | None = None
_NORMS_CONCRETE: frozenset | None = None

def _load_concreteness_s1():
    global _NORMS_ABSTRACT, _NORMS_CONCRETE
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'data', 'concreteness_s1_v1.tsv')
    ab, co = set(), set()
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.startswith('#'):
                    continue
                parts = line.rstrip('\n').split('\t')
                if len(parts) != 2:
                    continue
                (ab if float(parts[1]) < 3.0 else co).add(parts[0])
    except OSError:
        pass  # file absent -> lexicon+suffix behavior unchanged
    _NORMS_ABSTRACT, _NORMS_CONCRETE = frozenset(ab), frozenset(co)


def is_abstract(surface: str) -> bool:
    """System-1 abstraction tell: curated lexicon or abstract suffix. Multiword is
    deferred to S2 (False). Non-abstract is an honest 'unknown', not 'concrete'."""
    s = surface.lower()
    if ' ' in s:
        return False
    if s in ABSTRACT_LEXICON:          # curated judgment wins over bulk norms
        return True
    if _NORMS_ABSTRACT is None:
        _load_concreteness_s1()
    if s in _NORMS_ABSTRACT:
        return True
    if s in _NORMS_CONCRETE:
        return False
    if s in CONCRETE_SUFFIX_EXCEPTIONS:
        return False
    return s.endswith(ABSTRACT_SUFFIXES)

# --- Curated METHOD lexicon (conservative; NO bare-suffix rule) ------------
# A word becomes METHOD only via MANUAL override or membership here. Keeps
# METHOD sparse-but-trustworthy (a false TOPIC is far cheaper than false METHOD).
ACTION_LEXICON = frozenset({
    'analyze', 'analyse', 'measure', 'compute', 'calculate', 'estimate',
    'build', 'construct', 'assemble', 'design', 'implement', 'configure',
    'install', 'deploy', 'optimize', 'optimise', 'process', 'transform',
    'convert', 'extract', 'filter', 'sort', 'search', 'classify', 'cluster',
    'train', 'evaluate', 'validate', 'verify', 'test', 'debug', 'refactor',
    'cook', 'bake', 'roast', 'fry', 'boil', 'simmer', 'saute', 'grill',
    'chop', 'slice', 'dice', 'mince', 'mix', 'stir', 'whisk', 'knead',
    'write', 'read', 'edit', 'compile', 'encode', 'decode', 'compress',
    'navigate', 'calibrate', 'diagnose', 'troubleshoot', 'execute', 'render',
    # 2026-07-05 gold-v2 triage F2 (verb-side gap, pays down L9)
    'create', 'generate', 'organize', 'organise', 'run', 'running', 'speak',
    'listen',
})

# --- Structural-token cue map (surface -> cue mask) ------------------------
# Most structural tokens carry no cue; '?' affords QUESTION.
STRUCTURAL_CUE_MAP = {
    '?': LOGIC_CUE['QUESTION'],
}


def pack_facet(bucket: int, cue_mask: int, flags: int) -> bytes:
    """Pack a 4-byte facet record. C reads bytes by offset; '<BHB' little-endian."""
    return struct.pack('<BHB', bucket, cue_mask, flags)


def unpack_facet(value: bytes) -> tuple[int, int, int]:
    """Unpack a 4-byte facet record -> (bucket, cue_mask, flags)."""
    return struct.unpack('<BHB', value)


def utility_of(flags: int) -> int:
    """Extract the 2-bit UTILITY class from a flags byte."""
    return (flags & UTILITY_MASK) >> UTILITY_SHIFT


def set_utility(flags: int, utility: int) -> int:
    """Return flags with UTILITY bits replaced."""
    return (flags & ~UTILITY_MASK) | ((utility << UTILITY_SHIFT) & UTILITY_MASK)


# --- T1 self-checks (collision-free; name<->byte bijective) ----------------
assert len(BUCKET) == len(BUCKET_NAME), 'BUCKET names not bijective with bytes'
assert len(set(BUCKET.values())) == len(BUCKET), 'Duplicate bucket byte values'
assert len(LOGIC_CUE) == len(LOGIC_CUE_NAME), 'LOGIC_CUE not bijective'
assert len(set(LOGIC_CUE.values())) == len(LOGIC_CUE), 'Duplicate logic-cue bits'
assert all(bin(v).count('1') == 1 for v in LOGIC_CUE.values()), 'Cue value not a single bit'
assert all(v < 0x8000 for v in LOGIC_CUE.values()), 'Cue bit 15 is reserved'
assert len(set(FLAG.values())) == len(FLAG), 'Duplicate flag bits'
assert not (FLAG['MANUAL'] & FLAG['HEURISTIC']), 'MANUAL/HEURISTIC share a bit'
assert all(v & UTILITY_MASK == 0 for v in FLAG.values()), 'A flag bit overlaps UTILITY bits'
assert len(UTILITY) == 4 and max(UTILITY.values()) <= 0b11, 'UTILITY must fit 2 bits'
assert all(c in LOGIC_CUE for c, _ in LOGIC_SEED_LISTS), 'Seed list cue name not in LOGIC_CUE'
assert RELATION_CUES <= set(LOGIC_CUE), 'RELATION_CUES references unknown cue'
# Seed list order is fixed and documented; duplicate cue categories are not allowed.
assert len([c for c, _ in LOGIC_SEED_LISTS]) == len({c for c, _ in LOGIC_SEED_LISTS}), \
    'A cue category appears twice in LOGIC_SEED_LISTS'
