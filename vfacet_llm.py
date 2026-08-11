"""
vfacet_llm.py
EloAI — Vfacet Agency + Directionality Fill

Patches the two UNKNOWN fields in b'vfacets':
  - agency       (SELF / OTHER / SYSTEM / UNKNOWN)
  - directionality (TOWARD / AWAY / STABLE / REVERSAL / NEUTRAL / UNKNOWN)

b'vfacets' is built first by vfacet_builder.py (polarity, temporality, domain).
This script reads those records, derives agency + direction, and writes them back
without touching the other three fields.

Pass 1 — Deterministic (default):
  direction  ← EPA A+E axes (activity + evaluation → movement vector)
  agency     ← curated word lists + morphological heuristics + domain signal
  Covers all EPA-rated entries reliably; partial heuristic coverage for the rest.

Pass 2 — LLM (--llm flag):
  Sends entries still UNKNOWN after pass 1 to claude-haiku in batches.
  Requires ANTHROPIC_API_KEY in environment.
  Install: pip install anthropic

Direction algorithm (deterministic):
  REVERSAL word list checked first (overrides EPA).
  if has EPA (A = activity, E = evaluation):
    A >= 1.2, E >= 0.8  → TOWARD
    A >= 1.2, E <= -0.8 → AWAY
    A <= 0.4            → STABLE (state, not moving)
    else                → NEUTRAL
  else (no EPA):
    prefix {un-,dis-,de-,anti-} → AWAY
    suffix {-tion,-ment,-ness,-ity,-ism,-ship} → STABLE
    default → UNKNOWN

Agency algorithm (deterministic):
  SELF_WORDS  — introspective / agentive-self verbs and concepts
  SYSTEM_WORDS — computational / institutional processes
  OTHER_WORDS  — transitive social/directive actions
  if domain = CODING (bit from existing vfacet) → SYSTEM
  morphological: -er/-or/-ist → OTHER (agentive noun)
  default → UNKNOWN

Usage:
    python vfacet_llm.py                       # deterministic pass, full corpus
    python vfacet_llm.py --dry-run             # count only, no writes
    python vfacet_llm.py --llm                 # deterministic + LLM for UNKNOWNs
    python vfacet_llm.py --llm --max-llm 5000  # cap LLM calls at N words
    python vfacet_llm.py --stats               # show current field distributions
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from collections import Counter
from pathlib import Path

import lmdb

# ---------------------------------------------------------------------------
# Load .env from R-D-concepts root (one level up from semantic_compression/)
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env from the R-D-concepts root if present."""
    try:
        from dotenv import load_dotenv
        _env = Path(__file__).parent.parent / '.env'
        if _env.exists():
            load_dotenv(_env)
    except ImportError:
        pass

_load_dotenv()


# ---------------------------------------------------------------------------
# Re-use vfacet_builder constants + pack/unpack
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from vfacet_builder import (           # noqa: E402
    AGENCY, DIRECTION, TEMPORAL, DOMAIN, POLARITY,
    AGENCY_SHIFT, AGENCY_MASK,
    DIRECTION_SHIFT, DIRECTION_MASK,
    TEMPORAL_SHIFT, TEMPORAL_MASK,
    DOMAIN_SHIFT, DOMAIN_MASK,
    POLARITY_SHIFT, POLARITY_MASK,
    VFACETS_DB, pack_vfacet, unpack_vfacet,
)

_EPA_STRUCT = struct.Struct('<fff')

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_DB     = _HERE / 'db' / 'dictionary.lmdb'
DEFAULT_EPA_DB = _HERE / '..' / 'Memory' / 'data' / 'epa_substrate.lmdb'

# ---------------------------------------------------------------------------
# Reversal word list — checked before EPA (explicit semantic reversal)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Apostrophe normaliser -- LLM may return curly quotes; canonicalise before
# building result_map keys so word-key lookup never fails on encoding alone.
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Collapse all apostrophe/quote variants to ASCII straight forms."""
    return (
        s
        .replace('\u2019', "'")   # RIGHT SINGLE QUOTATION MARK
        .replace('\u2018', "'")   # LEFT SINGLE QUOTATION MARK
        .replace('\u02bc', "'")   # MODIFIER LETTER APOSTROPHE
        .replace('\u0060', "'")   # GRAVE ACCENT (backtick)
        .replace('\u00b4', "'")   # ACUTE ACCENT
        .lower()
    )


REVERSAL_WORDS: frozenset[str] = frozenset({
    'reverse', 'reversal', 'revert', 'undo', 'redo', 'untangle', 'unravel',
    'invert', 'inversion', 'flip', 'flipped', 'pivot', 'pivoting', 'pivoted',
    'transform', 'transformation', 'switch', 'switching', 'alter', 'alteration',
    'shift', 'shifting', 'convert', 'conversion', 'change', 'changed', 'changing',
    'modify', 'modification', 'restructure', 'restructuring', 'reconfigure',
    'override', 'overrule', 'overthrow', 'subvert', 'subversion', 'repudiate',
    'recant', 'retract', 'retraction', 'rescind', 'repeal',
    'turnaround', 'turnabout', 'about-face', 'u-turn',
    # negation of a prior state
    'desalinate', 'decompose', 'deconstruct', 'deactivate', 'decommission',
    'dismantle', 'disassemble', 'disrupt', 'disruption',
    'unwind', 'unwrap', 'unlock', 'unfold', 'unpack',
})

# Reversal prefix check (short surface, no EPA, prefix signals "undo")
_REVERSAL_PREFIXES = ('un', 'de', 'dis', 're')   # 're-' is context-dependent but good signal

# ---------------------------------------------------------------------------
# AWAY morphological prefixes (without EPA)
# ---------------------------------------------------------------------------

_AWAY_PREFIXES = ('anti', 'counter', 'non')

# ---------------------------------------------------------------------------
# STABLE morphological suffixes (without EPA)
# ---------------------------------------------------------------------------

_STABLE_SUFFIXES = (
    'tion', 'sion', 'ment', 'ness', 'ity', 'ism', 'ship', 'hood',
    'dom', 'ance', 'ence', 'ure', 'age', 'al', 'ial',
)

# ---------------------------------------------------------------------------
# Phrase-direction keyword sets — scanned token-by-token in multi-word entries
# ---------------------------------------------------------------------------

# Stopwords for phrase content-word extraction (mirrors epa_phrase_composer.py)
_PHRASE_STOPWORDS: frozenset[str] = frozenset({
    'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or',
    'but', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'that', 'this',
    'it', 'its', 'with', 'by', 'as', 'from', 'into', 'through', 'not',
    'no', 'so', 'yet', 'each', 'few', 'more', 'most', 'some', 'than',
    'very', 'my', 'your', 'his', 'her', 'our', 'their', 'who', 'what',
    'all', 'any', 'about', 'just', 'even', 'only', 'still', 'then', 'well',
    'now', 'here', 'there', 'do', 'did', 'does', 'have', 'has', 'had',
    'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can',
    'get', 'got', 'let', 'put', 'go', 'going', 'come', 'came',
})

_PHRASE_TOWARD_TOKENS: frozenset[str] = frozenset({
    'toward', 'towards', 'forward', 'ahead', 'onward', 'upward', 'upwards',
    'progress', 'advance', 'approach', 'gain', 'build', 'grow', 'improve',
    'better', 'reach', 'achieve', 'attain', 'acquire', 'pursue', 'seek',
    'engage', 'embrace', 'accept', 'welcome', 'open',
})
_PHRASE_AWAY_TOKENS: frozenset[str] = frozenset({
    'away', 'apart', 'aside', 'back', 'backward', 'backwards', 'off', 'out',
    'against', 'oppose', 'avoid', 'escape', 'flee', 'reject', 'resist',
    'deny', 'refuse', 'lose', 'lack', 'without', 'abandon', 'quit', 'leave',
    'withdraw', 'retreat', 'distance',
})
_PHRASE_STABLE_TOKENS: frozenset[str] = frozenset({
    'remain', 'stay', 'keep', 'hold', 'maintain', 'preserve', 'retain',
    'continue', 'persist', 'endure', 'sustain', 'stable', 'steady',
    'constant', 'unchanged', 'fixed', 'settled',
})
_PHRASE_REVERSAL_TOKENS: frozenset[str] = frozenset({
    'undo', 'reverse', 'revert', 'overturn', 'switch', 'convert', 'transform',
    'instead', 'otherwise', 'turn', 'reconsider', 'rethink', 'redo',
})


def _compose_phrase_epa_inline(
    tokens: list[str],
    epa_map: dict[str, tuple[float, float, float]],
) -> tuple[float, float, float] | None:
    """
    Compute mean EPA for a tokenised phrase from the single-word EPA map.
    Same logic as epa_phrase_composer._compose_epa but inline, no write.
    Returns None if fewer than 1 rated content token.
    """
    content = [t for t in tokens if t.isalpha() and t not in _PHRASE_STOPWORDS]
    rated = [epa_map[f'en|{t}'] for t in content if f'en|{t}' in epa_map]
    if not rated:
        return None
    e = sum(v[0] for v in rated) / len(rated)
    p = sum(v[1] for v in rated) / len(rated)
    a = sum(v[2] for v in rated) / len(rated)
    return e, p, a


# ---------------------------------------------------------------------------
# Agency word lists
# ---------------------------------------------------------------------------

SELF_WORDS: frozenset[str] = frozenset({
    # introspective / volitional
    'decide', 'decision', 'choose', 'choice', 'prefer', 'preference',
    'want', 'desire', 'wish', 'hope', 'intend', 'intention',
    'plan', 'planning', 'resolve', 'determination',
    # cognition
    'think', 'thinking', 'thought', 'believe', 'belief', 'assume', 'assumption',
    'understand', 'understanding', 'realize', 'realize', 'recognize', 'recognition',
    'perceive', 'perception', 'notice', 'observe', 'observation',
    'learn', 'learning', 'know', 'knowledge', 'remember', 'memory', 'recall',
    'wonder', 'imagine', 'imagination', 'reflect', 'reflection',
    # emotion / feeling
    'feel', 'feeling', 'emotion', 'sense', 'sensed', 'sensing',
    'love', 'hate', 'fear', 'anxiety', 'trust', 'doubt',
    'accept', 'acceptance', 'reject',   # internal stance
    # growth / self-development
    'grow', 'growth', 'improve', 'improvement', 'create', 'creation',
    'achieve', 'achievement', 'aspire', 'aspiration', 'strive',
    'forgive', 'forgiveness', 'heal', 'healing', 'recover', 'recovery',
    # self-expression
    'express', 'expression', 'confess', 'confide', 'share',
})

SYSTEM_WORDS: frozenset[str] = frozenset({
    # computing / processes
    'compile', 'compilation', 'execute', 'execution', 'deploy', 'deployment',
    'allocate', 'allocation', 'schedule', 'scheduling',
    'process', 'processing', 'compute', 'computation',
    'calculate', 'calculation', 'generate', 'generation',
    'initialize', 'initialization', 'configure', 'configuration',
    'authenticate', 'authentication', 'authorize', 'authorization',
    'render', 'rendering', 'serialize', 'serialization',
    'deserialize', 'deserialization', 'cache', 'caching',
    'query', 'querying', 'index', 'indexing', 'parse', 'parsing',
    'encode', 'encoding', 'decode', 'decoding',
    'validate', 'validation', 'stream', 'streaming',
    'route', 'routing', 'dispatch', 'dispatching',
    'replicate', 'replication', 'synchronize', 'synchronization',
    'migrate', 'migration', 'provision', 'provisioning',
    # institutional / systemic
    'regulate', 'regulation', 'govern', 'governance',
    'administrate', 'administration', 'mandate', 'mandating',
    'enforce', 'enforcement', 'legislate', 'legislation',
    'systematize', 'standardize', 'standardization',
    'institutionalize', 'operationalize', 'automate', 'automation',
    # biological / physical systems
    'metabolize', 'metabolize', 'catalyze', 'catalysis',
    'oxidize', 'oxidation', 'photosynthesize', 'photosynthesis',
    'propagate', 'propagation', 'replicate',  # bio
})

OTHER_WORDS: frozenset[str] = frozenset({
    # communication → others
    'tell', 'say', 'speak', 'inform', 'notify', 'announce',
    'ask', 'request', 'demand', 'inquire', 'question',
    'advise', 'recommend', 'suggest', 'propose',
    'warn', 'caution', 'alert', 'remind',
    # directive / social
    'lead', 'leadership', 'command', 'order', 'direct', 'instruct',
    'teach', 'educate', 'train', 'mentor', 'coach',
    'help', 'assist', 'aid', 'support', 'serve',
    'guide', 'facilitate', 'enable', 'empower',
    # give / take
    'give', 'offer', 'provide', 'supply', 'donate',
    'take', 'receive', 'accept',  # from others
    'transfer', 'share', 'distribute',
    # control
    'manage', 'supervise', 'oversee', 'coordinate',
    'control', 'monitor', 'evaluate', 'assess',
    'hire', 'fire', 'appoint', 'assign', 'delegate',
    # protect / enforce (toward others)
    'protect', 'defend', 'guard', 'secure',
    'allow', 'permit', 'approve', 'deny', 'prevent', 'block',
    'punish', 'reward', 'incentivize',
    # persuade
    'persuade', 'convince', 'motivate', 'inspire', 'influence',
    'negotiate', 'collaborate', 'cooperate',
})

# Agentive person-noun suffixes → OTHER
_AGENTIVE_SUFFIXES = ('er', 'or', 'ist', 'ian', 'ant', 'ent', 'eur')

# ---------------------------------------------------------------------------
# Direction classifier
# ---------------------------------------------------------------------------

# EPA thresholds
_A_ACTIVE  = 1.2   # clearly in motion
_A_PASSIVE = 0.4   # clearly static
_E_POS     = 0.8   # clearly positive / approaching good
_E_NEG     = -0.8  # clearly negative / approaching bad


def _epa_to_direction(e: float, p: float, a: float) -> int:
    """Map EPA triple to DIRECTION code (shared by single-word and phrase paths)."""
    if a >= _A_ACTIVE:
        if e >= _E_POS:   return DIRECTION['TOWARD']
        if e <= _E_NEG:   return DIRECTION['AWAY']
        return DIRECTION['NEUTRAL']
    if a <= _A_PASSIVE:
        return DIRECTION['STABLE']
    if e >= _E_POS + 0.2: return DIRECTION['TOWARD']
    if e <= _E_NEG - 0.2: return DIRECTION['AWAY']
    return DIRECTION['NEUTRAL']


def _classify_direction(
    surface: str,
    epa: tuple | None,
    epa_map: dict | None = None,
) -> int:
    """Return a DIRECTION code for *surface*.

    epa:     pre-loaded stored EPA for this surface (or None).
    epa_map: full EPA lookup dict; used for on-the-fly phrase composition.
    """
    s = surface.lower().strip()
    is_phrase = ' ' in s or '-' in s

    # 1. Explicit reversal surface
    if s in REVERSAL_WORDS:
        return DIRECTION['REVERSAL']

    # 2. Phrase-specific path
    if is_phrase:
        import re as _re
        tokens = _re.split(r'[\s\-]+', s)

        # 2a. Scan tokens for strong directional keywords
        for t in tokens:
            if t in REVERSAL_WORDS or t in _PHRASE_REVERSAL_TOKENS:
                return DIRECTION['REVERSAL']
        for t in tokens:
            if t in _PHRASE_TOWARD_TOKENS:
                return DIRECTION['TOWARD']
        for t in tokens:
            if t in _PHRASE_AWAY_TOKENS:
                return DIRECTION['AWAY']
        for t in tokens:
            if t in _PHRASE_STABLE_TOKENS:
                return DIRECTION['STABLE']

        # 2b. If stored EPA present, use it
        if epa is not None:
            return _epa_to_direction(*epa)

        # 2c. Compose EPA on the fly from constituent words
        if epa_map is not None:
            composed = _compose_phrase_epa_inline(tokens, epa_map)
            if composed is not None:
                return _epa_to_direction(*composed)

        # 2d. Prefix/suffix heuristics still apply to phrases
        for pfx in ('un', 'de', 'dis', 'anti', 'counter', 'non'):
            if s.startswith(pfx) and len(s) > len(pfx) + 2:
                return DIRECTION['AWAY']

        return DIRECTION['UNKNOWN']

    # 3. Single-word path — no EPA, no special affix
    if epa is None:
        for pfx in ('un', 'de', 'dis'):
            if s.startswith(pfx) and len(s) > len(pfx) + 2:
                return DIRECTION['AWAY']
        for pfx in _AWAY_PREFIXES:
            if s.startswith(pfx) and len(s) > len(pfx) + 2:
                return DIRECTION['AWAY']
        for sfx in _STABLE_SUFFIXES:
            if s.endswith(sfx) and len(s) > len(sfx) + 2:
                return DIRECTION['STABLE']
        # Single nouns/adjectives with no directional signal → NEUTRAL
        return DIRECTION['NEUTRAL']

    # 4. Single-word with stored EPA
    return _epa_to_direction(*epa)


# ---------------------------------------------------------------------------
# Agency classifier
# ---------------------------------------------------------------------------

def _classify_agency(surface: str, epa: tuple | None, domain_code: int) -> int:
    """Return an AGENCY code for *surface*."""
    import re as _re
    s = surface.lower().strip()
    is_phrase = ' ' in s or '-' in s

    if is_phrase:
        tokens = _re.split(r'[\s\-]+', s)
        # First content word often carries the verb (e.g. "make a decision" → SELF)
        first = tokens[0] if tokens else ''
        for check in (first, s):
            if check in SELF_WORDS:   return AGENCY['SELF']
            if check in SYSTEM_WORDS: return AGENCY['SYSTEM']
            if check in OTHER_WORDS:  return AGENCY['OTHER']
        # Scan all content tokens
        content = [t for t in tokens if t not in _PHRASE_STOPWORDS and t.isalpha()]
        for t in content:
            if t in SELF_WORDS:   return AGENCY['SELF']
            if t in SYSTEM_WORDS: return AGENCY['SYSTEM']
            if t in OTHER_WORDS:  return AGENCY['OTHER']
        # Domain + agentive suffix on last meaningful token
        key = content[-1] if content else tokens[-1]
        if domain_code == DOMAIN['CODING']:
            return AGENCY['SYSTEM']
        for sfx in _AGENTIVE_SUFFIXES:
            if key.endswith(sfx) and len(key) > len(sfx) + 2:
                return AGENCY['OTHER']
        return AGENCY['UNKNOWN']

    # Single-word path (unchanged)
    key = s
    if key in SELF_WORDS:   return AGENCY['SELF']
    if key in SYSTEM_WORDS: return AGENCY['SYSTEM']
    if key in OTHER_WORDS:  return AGENCY['OTHER']
    if domain_code == DOMAIN['CODING']:
        return AGENCY['SYSTEM']
    for sfx in _AGENTIVE_SUFFIXES:
        if key.endswith(sfx) and len(key) > len(sfx) + 2:
            return AGENCY['OTHER']
    if epa is not None:
        e, p, a = epa
        if p >= 1.5 and a >= 1.0:
            return AGENCY['SELF'] if e >= 0.5 else AGENCY['OTHER']
    return AGENCY['UNKNOWN']


# ---------------------------------------------------------------------------
# EPA loader
# ---------------------------------------------------------------------------

def _load_epa_lookup(epa_lmdb_path: Path) -> dict[str, tuple[float, float, float]]:
    lookup: dict[str, tuple[float, float, float]] = {}
    env = lmdb.open(str(epa_lmdb_path), max_dbs=5, readonly=True)
    with env.begin() as txn:
        db = env.open_db(b'epa', txn=txn)
        cur = txn.cursor(db=db)
        for k, v in cur.iternext():
            try:
                raw = k.decode('utf-8')
                surface = raw[3:] if raw.startswith('en|') else raw
                if len(v) == 12:
                    lookup[surface] = _EPA_STRUCT.unpack(v)
            except Exception:
                continue
    env.close()
    return lookup


# ---------------------------------------------------------------------------
# Main deterministic patch pass
# ---------------------------------------------------------------------------

def patch_vfacets_deterministic(
    lmdb_path: Path,
    epa_lmdb_path: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """
    Pass 1: read all vfacet records, derive agency + direction, write back.

    Only the agency and direction bits are changed; temporality, domain,
    and polarity are preserved exactly.
    """
    lmdb_path = Path(lmdb_path)
    epa_lmdb_path = Path(epa_lmdb_path)

    # Load EPA lookup
    epa_lookup: dict[str, tuple] = {}
    if epa_lmdb_path.exists():
        print(f'  Loading EPA from {epa_lmdb_path}...')
        epa_lookup = _load_epa_lookup(epa_lmdb_path)
        print(f'  EPA entries loaded: {len(epa_lookup):,}')

    env = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=8,
                    readonly=False)

    # Collect (id_bytes, surface) from forward db
    surfaces: list[tuple[bytes, str]] = []
    with env.begin() as txn:
        db_fwd = env.open_db(b'forward', txn=txn)
        cur = txn.cursor(db=db_fwd)
        for surface_bytes, id_bytes in cur.iternext():
            surfaces.append((id_bytes, surface_bytes.decode('utf-8', errors='replace')))

    stats: Counter = Counter()
    stats['total'] = len(surfaces)
    t0 = time.perf_counter()

    # Track entries still UNKNOWN (for optional LLM pass)
    still_unknown: list[tuple[bytes, str]] = []

    if not dry_run:
        with env.begin(write=True) as txn:
            db_vf = env.open_db(VFACETS_DB, txn=txn, create=False)
            for id_bytes, surface in surfaces:
                # Read existing record
                existing = txn.get(id_bytes, db=db_vf)
                if existing is None:
                    # vfacet_builder not run yet; skip
                    stats['skipped_no_record'] += 1
                    continue

                old = unpack_vfacet(existing)
                temporal  = old['temporal']
                domain    = old['domain']
                polarity  = old['polarity']

                # Preserve LLM-written values from previous runs
                prev_agency    = old['agency']
                prev_direction = old['direction']
                already_classified = (
                    prev_agency    != AGENCY['UNKNOWN'] and
                    prev_direction != DIRECTION['UNKNOWN']
                )
                if already_classified:
                    stats['preserved'] += 1
                    stats[f'dir_{prev_direction}'] += 1
                    stats[f'age_{prev_agency}'] += 1
                    continue

                epa = epa_lookup.get(surface.lower())
                direction = _classify_direction(surface, epa, epa_map=epa_lookup)
                agency    = _classify_agency(surface, epa, domain)

                # Per-field preservation: never overwrite a field already classified
                # (by LLM or a previous deterministic pass)
                if prev_agency != AGENCY['UNKNOWN']:
                    agency = prev_agency
                if prev_direction != DIRECTION['UNKNOWN']:
                    direction = prev_direction

                new_bytes = pack_vfacet(agency, direction, temporal, domain, polarity)
                txn.put(id_bytes, new_bytes, db=db_vf)

                stats['written'] += 1
                stats[f'dir_{direction}'] += 1
                stats[f'age_{agency}'] += 1

                if direction == DIRECTION['UNKNOWN'] or agency == AGENCY['UNKNOWN']:
                    still_unknown.append((id_bytes, surface))

                if verbose and stats['written'] % 10_000 == 0:
                    print(f'  {stats["written"]:,}/{stats["total"]:,}...', end='\r')
    else:
        # Dry run: classify without writing
        db_vf = env.open_db(VFACETS_DB, create=False)
        with env.begin() as txn:
            for id_bytes, surface in surfaces:
                existing = txn.get(id_bytes, db=db_vf)
                if existing is None:
                    stats['skipped_no_record'] += 1
                    continue
                old = unpack_vfacet(existing)
                domain = old['domain']
                epa = epa_lookup.get(surface.lower())
                direction = _classify_direction(surface, epa, epa_map=epa_lookup)
                agency    = _classify_agency(surface, epa, domain)
                stats[f'dir_{direction}'] += 1
                stats[f'age_{agency}'] += 1
                if direction == DIRECTION['UNKNOWN'] or agency == AGENCY['UNKNOWN']:
                    still_unknown.append((id_bytes, surface))

    env.close()
    stats['elapsed_s'] = round(time.perf_counter() - t0, 3)
    stats['still_unknown'] = len(still_unknown)
    return dict(stats), still_unknown


# ---------------------------------------------------------------------------
# LLM patch pass (requires anthropic package + ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

_LLM_PROMPT_TEMPLATE = """\
Classify each phrase for AGENCY and DIRECTION. These are multi-word fragments from conversational transcripts — apply pragmatic (discourse) reading, not just semantic.

AGENCY: SELF=acts on self | OTHER=acts on others | SYSTEM=automated/institutional | UNKNOWN=indeterminate
DIRECTION: TOWARD=approach/offer/build | AWAY=avoid/reject/retreat | STABLE=persist/anchor | REVERSAL=undo/rethink | NEUTRAL=filler/reference/no stance | UNKNOWN=indeterminate

Return a JSON array in SAME ORDER as input, one object per phrase:
[{{"word": "...exact phrase...", "agency": "SELF", "direction": "TOWARD"}}, ...]

Phrases:
{words}
"""

_LLM_AGENCY_MAP  = {v: k for k, v in AGENCY.items()}
_LLM_DIR_MAP     = {v: k for k, v in DIRECTION.items()}


def _llm_batch(words: list[str], client, model: str) -> list[dict]:
    """Call the LLM for one batch; returns list of {word, agency, direction}."""
    import json as _json
    prompt = _LLM_PROMPT_TEMPLATE.format(words='\n'.join(f'- {w}' for w in words))
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{'role': 'user', 'content': prompt}],
    )
    text = resp.content[0].text.strip()
    # Extract JSON array from response (may be wrapped in markdown)
    start = text.find('[')
    end   = text.rfind(']') + 1
    if start == -1 or end == 0:
        return []
    return _json.loads(text[start:end])



def _llm_batch_api(words: list[str], url: str, api_key: str = '', model: str = 'local') -> tuple[list, str]:
    """Call any OpenAI-compatible /v1/chat/completions endpoint (local or cloud)."""
    import json as _json
    import urllib.request as _req
    import urllib.error as _uerr
    prompt = _LLM_PROMPT_TEMPLATE.format(words='\n'.join(f'- {w}' for w in words))
    payload = _json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_completion_tokens': 8192,
        'temperature': 0.0,
    }).encode()
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    request = _req.Request(
        f'{url.rstrip("/")}/v1/chat/completions',
        data=payload,
        headers=headers,
        method='POST',
    )
    try:
        with _req.urlopen(request, timeout=120) as resp:
            body = _json.loads(resp.read())
    except _uerr.HTTPError as _he:
        body_text = ''
        try:
            body_text = _he.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        if _he.code >= 500:
            raise RuntimeError(f'SERVICE_UNAVAILABLE:{_he.code}') from _he
        raise RuntimeError(f'API_ERROR:{_he.code} {body_text}') from _he
    except _uerr.URLError as _ue:
        raise RuntimeError(f'SERVICE_UNAVAILABLE:connection_refused') from _ue
    text = body['choices'][0]['message']['content'].strip()
    start = text.find('[')
    end   = text.rfind(']') + 1
    if start == -1 or end == 0:
        return [], text
    try:
        return _json.loads(text[start:end]), text
    except Exception as _ex:
        return [], text + f"\n[PARSE_ERR: {_ex}]"


def patch_vfacets_llm(
    lmdb_path: Path,
    still_unknown: list[tuple[bytes, str]],
    max_llm: int = 0,
    batch_size: int = 50,
    model: str = 'claude-haiku-4-5-20251001',
    dry_run: bool = False,
    api_url: str = 'http://localhost:8080',
    api_key: str = '',
    api_model: str = 'local',
    time_limit_s: float = 0,
    miss_log: Path | None = None,
    parallel: int = 1,
) -> dict:
    """
    Pass 2: call LLM for entries still UNKNOWN after the deterministic pass.

    Requires: pip install anthropic  +  ANTHROPIC_API_KEY in environment.
    """
    use_local = bool(api_url)
    client = None
    if use_local:
        tag = 'OpenAI API' if 'openai.com' in api_url else f'local llama.cpp @ {api_url}'
        print(f'  LLM backend   : {tag} (model={api_model})')
    else:
        try:
            import anthropic
        except ImportError:
            print('  ERROR: anthropic not installed. Run: pip install anthropic')
            return {'llm_attempted': 0}
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            print('  ERROR: ANTHROPIC_API_KEY not set in environment.')
            return {'llm_attempted': 0}
        client = anthropic.Anthropic(api_key=api_key)
        print(f'  LLM backend   : Anthropic {model}')

    _miss_fh = open(miss_log, 'a', encoding='utf-8') if miss_log else None
    # Only send phrases (multi-word) to the LLM — single words without
    # EPA or word-list hits are genuinely UNKNOWN; the LLM agrees and
    # wastes compute confirming it.
    targets = list(still_unknown)

    # --- Pre-filter 2: numeric artifacts (timestamp fragments) ---
    numeric_filtered = [(k, s) for k, s in targets if s and s[0].isdigit()]
    targets = [(k, s) for k, s in targets if not (s and s[0].isdigit())]
    if numeric_filtered:
        print(f'  Skipping {len(numeric_filtered):,} numeric-fragment entries (transcript artifacts)')

    # --- Pre-filter 3: pure stopword n-grams → write NEUTRAL directly ---
    stopword_neutral = [(k, s) for k, s in targets
                        if all(t.lower() in _PHRASE_STOPWORDS for t in s.split())]
    targets = [(k, s) for k, s in targets
               if not all(t.lower() in _PHRASE_STOPWORDS for t in s.split())]
    if stopword_neutral and not dry_run:
        try:
            _env2 = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=8, readonly=False)
            with _env2.begin(write=True) as _txn:
                _vf2 = _env2.open_db(VFACETS_DB, txn=_txn)
                for _kb, _surf in stopword_neutral:
                    _existing = _txn.get(_kb, db=_vf2)
                    if _existing:
                        _old = unpack_vfacet(_existing)
                        _txn.put(_kb, pack_vfacet(
                            _old['agency'], DIRECTION['NEUTRAL'],
                            _old['temporal'], _old['domain'], _old['polarity']
                        ), db=_vf2)
            _env2.close()
            print(f'  Set {len(stopword_neutral):,} pure-stopword n-grams → NEUTRAL (no LLM needed)')
        except Exception as _sw_err:
            print(f'  [WARN] stopword NEUTRAL write failed: {_sw_err}')

    if max_llm and len(targets) > max_llm:
        targets = targets[:max_llm]

    display_model = api_model if use_local else model
    print(f'  LLM pass: {len(targets):,} entries '
          f'(batches of {batch_size}, model={display_model})')

    env = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=8,
                    readonly=False)
    db_vf = env.open_db(VFACETS_DB)

    stats: Counter = Counter()
    t0 = time.perf_counter()

    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    def _run_batch(batch_args):
        idx, batch = batch_args
        words = [surface for _, surface in batch]
        if use_local:
            return idx, batch, *_llm_batch_api(words, api_url, api_key=api_key, model=api_model)
        else:
            return idx, batch, _llm_batch(words, client, model), ''

    batch_list = [(i, targets[i:i + batch_size])
                  for i in range(0, len(targets), batch_size)]

    from concurrent.futures import ThreadPoolExecutor, as_completed as _asc

    def _run_batch(args):
        idx, batch = args
        words = [s for _, s in batch]
        if use_local:
            return idx, batch, *_llm_batch_api(words, api_url, api_key=api_key, model=api_model)
        else:
            return idx, batch, _llm_batch(words, client, model), ''

    batch_list = [(i, targets[i:i + batch_size])
                  for i in range(0, len(targets), batch_size)]
    total_batches = len(batch_list)

    with ThreadPoolExecutor(max_workers=parallel) as _pool:
        for chunk_start in range(0, total_batches, parallel):
            chunk = batch_list[chunk_start:chunk_start + parallel]
            futures = {_pool.submit(_run_batch, b): b for b in chunk}
            for fut in _asc(futures):
                try:
                    i, batch, results, _raw = fut.result()
                except Exception as e:
                    _, batch = futures[fut]
                    if 'SERVICE_UNAVAILABLE' in str(e):
                        print(f'\n  [ABORT] LLM server unavailable ({e}) — stopping run.')
                        env.close()
                        if _miss_fh:
                            _miss_fh.close()
                        stats['llm_elapsed_s'] = round(time.perf_counter() - t0, 3)
                        return dict(stats)
                    print(f'  LLM batch error: {e}')
                    stats['llm_errors'] += len(batch)
                    continue

                if not results:
                    bn = i // batch_size
                    _dbg = Path(__file__).parent / f"debug_batch_{bn}.txt"
                    _dbg.write_text(_raw, encoding="utf-8")
                    print(f'  [DEBUG] batch {bn} empty — raw written to {_dbg.name}')

                # Build word→result map; also keep positional list as fallback
                result_map  = {_norm(r['word']): r for r in results if isinstance(r, dict)}
                index_list  = [r for r in results if isinstance(r, dict)]

                if not dry_run:
                    with env.begin(write=True) as txn:
                        for j, (id_bytes, surface) in enumerate(batch):
                            r = result_map.get(_norm(surface))
                            if r is None and j < len(index_list):
                                # positional fallback — verify word is close enough
                                candidate = index_list[j]
                                returned  = _norm(candidate.get('word', ''))
                                if returned and returned != _norm(surface):
                                    stats['llm_hallucinated'] += 1
                                    # still use it positionally but log the mismatch
                                    if _miss_fh:
                                        _miss_fh.write(
                                            f'[MISMATCH] sent={surface!r} got={candidate.get("word")!r}\n'
                                        )
                                r = candidate
                            if r is None:
                                stats['llm_miss'] += 1
                                if _miss_fh:
                                    _miss_fh.write(surface + '\n')
                                continue

                            agency_str    = r.get('agency',    'UNKNOWN').upper()
                            direction_str = r.get('direction', 'UNKNOWN').upper()
                            agency_code    = AGENCY.get(agency_str,    AGENCY['UNKNOWN'])
                            direction_code = DIRECTION.get(direction_str, DIRECTION['UNKNOWN'])

                            existing = txn.get(id_bytes, db=db_vf)
                            if existing is None:
                                continue
                            old = unpack_vfacet(existing)

                            # Per-field preservation: never overwrite a non-UNKNOWN
                            # value with UNKNOWN — keep the best classification seen
                            if old['agency'] != AGENCY['UNKNOWN']:
                                agency_code = old['agency']
                            if old['direction'] != DIRECTION['UNKNOWN']:
                                direction_code = old['direction']

                            new_bytes = pack_vfacet(
                                agency_code, direction_code,
                                old['temporal'], old['domain'], old['polarity'],
                            )
                            txn.put(id_bytes, new_bytes, db=db_vf)
                            stats['llm_written'] += 1
                            stats[f'llm_dir_{direction_code}'] += 1
                            stats[f'llm_age_{agency_code}'] += 1
                else:
                    stats['llm_dry_count'] += len(batch)

                stats['llm_attempted'] += len(batch)
                done = stats['llm_attempted']
                if done % (batch_size * parallel) == 0:
                    print(f'  LLM: {done:,}/{len(targets):,}...', end='\r')

            # time-limit check between chunks (results already committed)
            if time_limit_s > 0 and (time.perf_counter() - t0) >= time_limit_s:
                elapsed_min = (time.perf_counter() - t0) / 60
                print(f'\n  [TIME LIMIT] {elapsed_min:.1f} min elapsed — stopping. Results saved.')
                break

    env.close()
    stats['llm_elapsed_s'] = round(time.perf_counter() - t0, 3)
    if _miss_fh:
        _miss_fh.close()
    return dict(stats)


# ---------------------------------------------------------------------------
# Temporal LLM pass
# ---------------------------------------------------------------------------

_LLM_TEMPORAL_PROMPT = """Classify each word or phrase for TEMPORAL TYPE — what kind of thing-in-time it describes.

STATE     = something that exists or persists (nouns, qualities, ongoing conditions)
            e.g. sadness, the relationship, silence, tired, awareness
PROCESS   = something that unfolds over time (activities, procedures, ongoing actions)
            e.g. learning, negotiating, the recovery, running, building
EVENT     = a discrete occurrence with a start and end
            e.g. the crash, graduation, a meeting, the attack, a decision
OUTCOME   = a result, consequence, or product of something
            e.g. success, the answer, damage, the solution, a conclusion
CONDITION = something situational or contingent (if/when/assuming constructs)
            e.g. if possible, when ready, in that case, assuming that, provided that
UNKNOWN   = genuinely indeterminate

Return a JSON array in SAME ORDER as input, one object per entry:
[{{"word": "...exact word/phrase...", "temporal": "STATE"}}, ...]

Entries:
{words}
"""

_LLM_TEMPORAL_MAP = {v: k for k, v in TEMPORAL.items()}


def patch_vfacets_temporal(
    lmdb_path: Path,
    max_llm: int = 0,
    batch_size: int = 50,
    dry_run: bool = False,
    api_url: str = 'http://localhost:8080',
    api_key: str = '',
    api_model: str = 'local',
    time_limit_s: float = 0,
    parallel: int = 1,
    miss_log: Path | None = None,
    force: bool = False,
) -> dict:
    """LLM pass for temporal classification.

    By default targets only temporal=UNKNOWN entries (safe to re-run).
    With force=True, re-classifies ALL entries regardless of current value —
    use this to overwrite corrupt data from a bad previous run.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed as _asc

    env = lmdb.open(str(lmdb_path), map_size=2 * 1024**3, max_dbs=8, readonly=False)
    db_vf = env.open_db(VFACETS_DB)

    # Collect targets from b'forward' (surface -> id).
    # b'vfacets' keys are Base64 IDs, NOT surface words.
    targets: list[tuple[bytes, str]] = []
    with env.begin() as txn:
        db_fwd = env.open_db(b'forward', txn=txn)
        cur = txn.cursor(db=db_fwd)
        for surface_bytes, id_bytes in cur.iternext():
            v = txn.get(bytes(id_bytes), db=db_vf)
            if v is None:
                continue
            f = unpack_vfacet(bytes(v))
            if force or f['temporal'] == TEMPORAL['UNKNOWN']:
                try:
                    surface = surface_bytes.decode('utf-8', errors='replace')
                except Exception:
                    continue
                targets.append((bytes(id_bytes), surface))

    if max_llm:
        targets = targets[:max_llm]

    tag = 'OpenAI API' if 'openai.com' in api_url else f'local llama.cpp @ {api_url}'
    print(f'  Temporal LLM backend: {tag} (model={api_model})')
    print(f'  Temporal UNKNOWN entries: {len(targets):,}')

    # Pre-filter: numeric fragments
    numeric = [(k, s) for k, s in targets if s and s[0].isdigit()]
    targets = [(k, s) for k, s in targets if not (s and s[0].isdigit())]
    if numeric:
        print(f'  Skipping {len(numeric):,} numeric-fragment entries')

    display_model = api_model
    print(f'  Temporal LLM pass: {len(targets):,} entries '
          f'(batches of {batch_size}, model={display_model})')

    _miss_fh = None
    if miss_log:
        _miss_fh = open(miss_log, 'a', encoding='utf-8')

    stats: Counter = Counter()
    t0 = time.perf_counter()

    def _run_batch(args):
        idx, batch = args
        words = [s for _, s in batch]
        prompt = _LLM_TEMPORAL_PROMPT.format(words='\n'.join(f'- {w}' for w in words))
        import json as _json, urllib.request as _req, urllib.error as _uerr
        payload = _json.dumps({
            'model': api_model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_completion_tokens': 4096,
            'temperature': 0.0,
        }).encode()
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        request = _req.Request(
            f'{api_url.rstrip("/")}/v1/chat/completions',
            data=payload, headers=headers, method='POST',
        )
        try:
            with _req.urlopen(request, timeout=120) as resp:
                body = _json.loads(resp.read())
        except _uerr.HTTPError as _he:
            body_text = ''
            try:
                body_text = _he.read().decode('utf-8', errors='replace')[:300]
            except Exception:
                pass
            if _he.code >= 500:
                raise RuntimeError(f'SERVICE_UNAVAILABLE:{_he.code}') from _he
            raise RuntimeError(f'API_ERROR:{_he.code} {body_text}') from _he
        except _uerr.URLError as _ue:
            raise RuntimeError(f'SERVICE_UNAVAILABLE:connection_refused') from _ue
        text = body['choices'][0]['message']['content'].strip()
        start = text.find('[')
        end   = text.rfind(']') + 1
        if start == -1 or end == 0:
            return idx, batch, [], text
        try:
            return idx, batch, _json.loads(text[start:end]), text
        except Exception as ex:
            return idx, batch, [], text + f'\n[PARSE_ERR: {ex}]'

    batch_list = [(i, targets[i:i + batch_size])
                  for i in range(0, len(targets), batch_size)]

    with ThreadPoolExecutor(max_workers=parallel) as _pool:
        for chunk_start in range(0, len(batch_list), parallel):
            chunk = batch_list[chunk_start:chunk_start + parallel]
            futures = {_pool.submit(_run_batch, b): b for b in chunk}
            for fut in _asc(futures):
                try:
                    i, batch, results, _raw = fut.result()
                except Exception as e:
                    _, batch = futures[fut]
                    if 'SERVICE_UNAVAILABLE' in str(e):
                        print(f'\n  [ABORT] LLM server unavailable ({e}) — stopping.')
                        env.close()
                        if _miss_fh:
                            _miss_fh.close()
                        stats['llm_elapsed_s'] = round(time.perf_counter() - t0, 3)
                        return dict(stats)
                    print(f'  Temporal batch error: {e}')
                    stats['errors'] += len(batch)
                    continue

                if not results:
                    bn = i // batch_size
                    _dbg = Path(__file__).parent / f'debug_temporal_{bn}.txt'
                    _dbg.write_text(_raw, encoding='utf-8')
                    print(f'  [DEBUG] temporal batch {bn} empty — raw written to {_dbg.name}')

                result_map = {_norm(r['word']): r for r in results if isinstance(r, dict)}
                index_list = [r for r in results if isinstance(r, dict)]

                if not dry_run:
                    with env.begin(write=True) as txn:
                        for j, (id_bytes, surface) in enumerate(batch):
                            r = result_map.get(_norm(surface))
                            if r is None and j < len(index_list):
                                candidate = index_list[j]
                                returned  = _norm(candidate.get('word', ''))
                                if returned and returned != _norm(surface):
                                    stats['hallucinated'] += 1
                                    if _miss_fh:
                                        _miss_fh.write(
                                            f'[MISMATCH] sent={surface!r} got={candidate.get("word")!r}\n'
                                        )
                                r = candidate
                            if r is None:
                                stats['miss'] += 1
                                if _miss_fh:
                                    _miss_fh.write(surface + '\n')
                                continue

                            temporal_str  = r.get('temporal', 'UNKNOWN').upper()
                            temporal_code = TEMPORAL.get(temporal_str, TEMPORAL['UNKNOWN'])

                            # Skip no-op writes (LLM returned UNKNOWN for an already-UNKNOWN entry)
                            if temporal_code == TEMPORAL['UNKNOWN']:
                                stats['skipped_unknown'] = stats.get('skipped_unknown', 0) + 1
                                continue

                            existing = txn.get(id_bytes, db=db_vf)
                            if existing is None:
                                continue
                            old = unpack_vfacet(existing)
                            txn.put(id_bytes, pack_vfacet(
                                old['agency'], old['direction'],
                                temporal_code,
                                old['domain'], old['polarity'],
                            ), db=db_vf)
                            stats['written'] += 1
                            stats[f'temporal_{temporal_code}'] += 1
                else:
                    stats['dry_count'] += len(batch)

                stats['attempted'] += len(batch)
                done = stats['attempted']
                if done % (batch_size * parallel) == 0:
                    print(f'  Temporal: {done:,}/{len(targets):,}...', end='\r')

            if time_limit_s > 0 and (time.perf_counter() - t0) >= time_limit_s:
                elapsed_min = (time.perf_counter() - t0) / 60
                print(f'\n  [TIME LIMIT] {elapsed_min:.1f} min elapsed — stopping. Results saved.')
                break

    env.close()
    stats['elapsed_s'] = round(time.perf_counter() - t0, 3)
    if _miss_fh:
        _miss_fh.close()
    return dict(stats)


# ---------------------------------------------------------------------------
# Stats reporter
# ---------------------------------------------------------------------------

def print_stats(db_path: Path) -> None:
    """Print distribution of all five vfacet fields from the live database."""
    env = lmdb.open(str(db_path), max_dbs=8, readonly=True)
    counts: dict[str, Counter] = {
        'agency': Counter(), 'direction': Counter(),
        'temporal': Counter(), 'domain': Counter(), 'polarity': Counter(),
    }
    n = 0
    with env.begin() as txn:
        db_vf = env.open_db(VFACETS_DB, txn=txn)
        cur = txn.cursor(db=db_vf)
        for _, v in cur.iternext():
            f = unpack_vfacet(bytes(v))
            counts['agency'][f['agency']]    += 1
            counts['direction'][f['direction']] += 1
            counts['temporal'][f['temporal']]  += 1
            counts['domain'][f['domain']]      += 1
            counts['polarity'][f['polarity']]  += 1
            n += 1
    env.close()

    print(f'\n  Total vfacet records: {n:,}')

    inv_agency    = {v: k for k, v in AGENCY.items()}
    inv_direction = {v: k for k, v in DIRECTION.items()}
    inv_temporal  = {v: k for k, v in TEMPORAL.items()}
    inv_domain    = {v: k for k, v in DOMAIN.items()}
    inv_polarity  = {v: k for k, v in POLARITY.items()}

    for field, inv in [
        ('agency',    inv_agency),
        ('direction', inv_direction),
        ('temporal',  inv_temporal),
        ('polarity',  inv_polarity),
    ]:
        print(f'\n  {field.capitalize()}:')
        for code, name in sorted(inv.items()):
            n_ = counts[field].get(code, 0)
            pct = 100 * n_ / max(n, 1)
            print(f'    {name:<12} {n_:>8,}  ({pct:.1f}%)')

    print('\n  Domain (non-GENERAL):')
    shown = False
    for code, name in sorted({v: k for k, v in DOMAIN.items()}.items()):
        if name == 'GENERAL': continue
        n_ = counts['domain'].get(code, 0)
        if n_:
            print(f'    {name:<12} {n_:>8,}')
            shown = True
    if not shown:
        print('    (none detected)')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description='Patch vfacets agency + directionality fields'
    )
    ap.add_argument('--db',       default=str(DEFAULT_DB),
                    help='Path to dictionary.lmdb')
    ap.add_argument('--epa-db',   default=str(DEFAULT_EPA_DB),
                    help='Path to epa_substrate.lmdb')
    ap.add_argument('--dry-run',  action='store_true',
                    help='Classify but do not write')
    ap.add_argument('--llm',      action='store_true',
                    help='Run LLM pass on agency+direction UNKNOWNs')
    ap.add_argument('--temporal',  action='store_true',
                    help='Run LLM pass on temporal UNKNOWNs (independent of --llm)')
    ap.add_argument('--force-temporal', action='store_true',
                    help='Re-classify ALL entries for temporal (overwrites existing values — use to fix corrupt data)')
    ap.add_argument('--max-llm',  type=int, default=0,
                    help='Cap LLM entries (0 = all UNKNOWNs)')
    ap.add_argument('--batch-size', type=int, default=50,
                    help='Words per LLM prompt (default 100)')
    ap.add_argument('--model',    default='claude-haiku-4-5-20251001',
                    help='LLM model for --llm pass')
    ap.add_argument('--local',      action='store_true',
                    help='Use local llama.cpp server instead of Anthropic')
    ap.add_argument('--local-url',  default='http://localhost:8080',
                    help='llama.cpp server base URL (default: http://localhost:8080)')
    ap.add_argument('--openai', action='store_true',
                    help='Use OpenAI API (reads OPENAI_API_KEY from env)')
    ap.add_argument('--openai-model', default='gpt-4o-mini',
                    help='OpenAI model (default: gpt-4o-mini)')
    ap.add_argument('--miss-log', default='llm_misses.txt',
                    help='Append missed surfaces to this file (default: llm_misses.txt)')
    ap.add_argument('--parallel', type=int, default=1,
                    help='Concurrent LLM batches — match server --parallel (default: 1)')
    ap.add_argument('--time-limit', default='',
                    help='Stop after this duration, e.g. 2h, 90m, 3600 (seconds). Results already written are kept.')
    ap.add_argument('--stats',    action='store_true',
                    help='Print current field distributions and exit')
    ap.add_argument('--verbose',  '-v', action='store_true')
    args = ap.parse_args()

    # Parse --time-limit into seconds
    def _parse_time(s: str) -> float:
        if not s:
            return 0.0
        s = s.strip().lower()
        if s.endswith('h'):
            return float(s[:-1]) * 3600
        if s.endswith('m'):
            return float(s[:-1]) * 60
        return float(s)
    time_limit_s = _parse_time(args.time_limit)

    db_path  = Path(args.db)
    epa_path = Path(args.epa_db)

    if args.stats:
        print(f'[vfacet_llm] stats for {db_path}')
        print_stats(db_path)
        return

    tag = '[DRY RUN] ' if args.dry_run else ''
    print(f'[vfacet_llm] {tag}db={db_path}')

    # Pass 1 -- deterministic
    print('\nPass 1 -- deterministic:')
    stats, still_unknown = patch_vfacets_deterministic(
        db_path, epa_path,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    dir_inv = {v: k for k, v in DIRECTION.items()}
    age_inv = {v: k for k, v in AGENCY.items()}

    total = stats.get('total', 0)
    print(f'\n  Total entries : {total:,}')
    if not args.dry_run:
        print(f'  Written       : {stats.get("written", 0):,}')
        if stats.get('skipped_no_record'):
            print(f'  Skipped (no vfacet record) : {stats["skipped_no_record"]:,}  '
                  f'-- run vfacet_builder.py first')

    print(f'\n  Direction:')
    for code, name in sorted(dir_inv.items()):
        n = stats.get(f'dir_{code}', 0)
        pct = 100 * n / max(total, 1)
        print(f'    {name:<12} {n:>8,}  ({pct:.1f}%)')

    print(f'\n  Agency:')
    for code, name in sorted(age_inv.items()):
        n = stats.get(f'age_{code}', 0)
        pct = 100 * n / max(total, 1)
        print(f'    {name:<12} {n:>8,}  ({pct:.1f}%)')

    print(f'\n  Still UNKNOWN (either field): {stats.get("still_unknown", 0):,}')
    print(f'  Elapsed       : {stats.get("elapsed_s", 0):.1f}s')

    # Pass 2 -- LLM (optional)
    if args.llm and still_unknown:
        print(f'\nPass 2 -- LLM ({len(still_unknown):,} UNKNOWN entries):')
        llm_stats = patch_vfacets_llm(
            db_path, still_unknown,
            max_llm=args.max_llm,
            batch_size=args.batch_size,
            model=args.model,
            dry_run=args.dry_run,
            api_url=(  'https://api.openai.com' if args.openai
                       else args.local_url if args.local
                       else '' ),
            api_key=os.environ.get('OPENAI_API_KEY', '') if args.openai else '',
            api_model=args.openai_model if args.openai else 'local',
            miss_log=Path(args.miss_log) if args.miss_log else None,
            parallel=args.parallel,
               )
        attempted    = llm_stats.get('llm_attempted', 0)
        written      = llm_stats.get('llm_written', 0)
        errors       = llm_stats.get('llm_errors', 0)
        misses       = llm_stats.get('llm_miss', 0)
        hallucinated = llm_stats.get('llm_hallucinated', 0)
        elapsed      = llm_stats.get('llm_elapsed_s', 0)
        print(f'\n  LLM attempted : {attempted:,}')
        if not args.dry_run:
            print(f'  LLM written   : {written:,}')
        if errors:
            print(f'  LLM errors    : {errors:,}')
        if misses:
            print(f'  LLM misses    : {misses:,}')
        if hallucinated:
            print(f'  LLM hallucinated: {hallucinated:,} (word mismatch, positional fallback used)')
        print(f'  LLM elapsed   : {elapsed}s')

    if args.temporal or args.force_temporal:
        force = args.force_temporal
        print(f'\nPass 3 -- Temporal LLM{"  [FORCE — re-classifying all entries]" if force else ""}:')
        t_stats = patch_vfacets_temporal(
            db_path,
            max_llm=args.max_llm,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            api_url=('https://api.openai.com' if args.openai
                     else args.local_url if args.local
                     else 'http://localhost:8080'),
            api_key=os.environ.get('OPENAI_API_KEY', '') if args.openai else '',
            api_model=args.openai_model if args.openai else 'local',
            time_limit_s=time_limit_s,
            parallel=args.parallel,
            miss_log=Path(args.miss_log) if args.miss_log else None,
            force=force,
        )
        print(f'\n  Temporal attempted : {t_stats.get("attempted", 0):,}')
        if not args.dry_run:
            print(f'  Temporal classified: {t_stats.get("written", 0):,}')
            if t_stats.get('skipped_unknown'):
                print(f'  Temporal LLM->UNKNOWN (skipped): {t_stats["skipped_unknown"]:,}')
        if t_stats.get('errors'):
            print(f'  Temporal errors    : {t_stats["errors"]:,}')
        if t_stats.get('miss'):
            print(f'  Temporal misses    : {t_stats["miss"]:,}')
        if t_stats.get('hallucinated'):
            print(f'  Temporal hallucinated: {t_stats["hallucinated"]:,}')
        print(f'  Temporal elapsed   : {t_stats.get("elapsed_s", 0)}s')

    if args.stats:
        print_stats(db_path)

    # Record that enrichment ran (2026-08-10 review §5.2): flip llm_enriched in the
    # build's vfacets_stats.json, so a deterministic-only build is distinguishable
    # from one this pass touched. Both show agency=UNKNOWN on unenriched ids; only
    # the record says which pass produced the state.
    if not args.dry_run and (args.llm or args.temporal):
        _sp = Path(db_path).parent / 'vfacets_stats.json'
        if _sp.exists():
            try:
                _s = json.loads(_sp.read_text(encoding='utf-8'))
                _s['llm_enriched'] = True
                _s['llm_enriched_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                _sp.write_text(json.dumps(_s, indent=2), encoding='utf-8')
                print(f'  vfacets_stats.json: llm_enriched=true')
            except Exception as e:
                print(f'  [warn] could not update vfacets_stats.json: {e}')


if __name__ == '__main__':
    main()
