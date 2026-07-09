from __future__ import annotations

import config as cfg
from config import (
    ACTION_LEXICON, ALL_FILLERS, BUCKET, FILLER_MAP, FLAG, FUNCTION_WORDS,
    LOGIC_CUE, LOGIC_SEED_LISTS, RELATION_CUES, STRUCTURAL_CUE_MAP,
    STRUCTURAL_IDS, UTILITY, set_utility,
)
from normalize import normalize_surface

__all__ = ['assign_facet', 'load_overrides', 'OverrideError']

_STRUCTURAL_SURFACES = frozenset(STRUCTURAL_IDS.values())
_NORM_FILLERS = frozenset(normalize_surface(f) for f in ALL_FILLERS)
_NORM_DISCOURSE = frozenset(normalize_surface(f) for f in FILLER_MAP['DISCOURSE'])
_NORM_ACTION = frozenset(normalize_surface(w) for w in ACTION_LEXICON)
_NORM_FUNCTION = frozenset(normalize_surface(w) for w in FUNCTION_WORDS)

_SEED_CUE_BY_KEY: dict[str, int] = {}
_RELATION_KEYS: set[str] = set()
for _cue_name, _words in LOGIC_SEED_LISTS:
    _bit = LOGIC_CUE[_cue_name]
    _is_relation = _cue_name in RELATION_CUES
    for _w in _words:
        _k = normalize_surface(_w)
        _SEED_CUE_BY_KEY[_k] = _SEED_CUE_BY_KEY.get(_k, 0) | _bit
        if _is_relation:
            _RELATION_KEYS.add(_k)


class OverrideError(ValueError):
    pass


def load_overrides(path: str) -> dict:
    import os
    out = {'exact': {}, 'normalized': {}}
    if not path or not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8') as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip('\n')
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                _check_version_comment(stripped, lineno, path)
                continue
            body = line.split('#', 1)[0].rstrip()
            parts = [p for p in body.split('\t') if p != '']
            if len(parts) < 2:
                raise OverrideError(f'{path}:{lineno}: expected mode:key<TAB>BUCKET[<TAB>CUE]')
            mode_key, bucket_name = parts[0], parts[1].strip()
            cue_field = parts[2].strip() if len(parts) >= 3 else '-'
            if ':' not in mode_key:
                raise OverrideError(f'{path}:{lineno}: key must be exact:/normalized:')
            mode, key = mode_key.split(':', 1)
            mode = mode.strip()
            if mode not in ('exact', 'normalized'):
                raise OverrideError(f'{path}:{lineno}: unknown mode {mode!r}')
            if bucket_name not in BUCKET:
                raise OverrideError(f'{path}:{lineno}: unknown bucket {bucket_name!r}')
            bucket = BUCKET[bucket_name]
            cue_mask = 0
            if cue_field and cue_field != '-':
                for c in cue_field.split(','):
                    c = c.strip()
                    if not c:
                        continue
                    if c not in LOGIC_CUE:
                        raise OverrideError(f'{path}:{lineno}: unknown cue {c!r}')
                    cue_mask |= LOGIC_CUE[c]
            store_key = key if mode == 'exact' else normalize_surface(key)
            out[mode][store_key] = (bucket, cue_mask)
    return out


def _check_version_comment(comment: str, lineno: int, path: str) -> None:
    low = comment.lower()
    for token in low.replace('#', ' ').split():
        if token.startswith('facets_format_version='):
            v = token.split('=', 1)[1]
            if v.isdigit() and int(v) != cfg.FACETS_FORMAT_VERSION:
                raise OverrideError(f'{path}:{lineno}: facets_format_version mismatch')
        elif token.startswith('normalization_version='):
            v = token.split('=', 1)[1]
            if v.isdigit() and int(v) != cfg.NORMALIZATION_VERSION:
                raise OverrideError(f'{path}:{lineno}: normalization_version mismatch')


def assign_facet(surface: str, overrides: dict | None = None) -> tuple[int, int, int]:
    overrides = overrides or {'exact': {}, 'normalized': {}}
    key = normalize_surface(surface)

    is_multiword = ' ' in key
    is_structural = surface in _STRUCTURAL_SURFACES
    is_filler = key in _NORM_FILLERS
    matched_cue = _SEED_CUE_BY_KEY.get(key, 0)
    is_closed_class = matched_cue != 0
    # A function word need not carry a cue. Orthogonal to is_closed_class, which
    # drives the RELATION bucket + CLOSED_CLASS flag; this only drives utility.
    is_function_word = key in _NORM_FUNCTION

    if is_structural:
        utility = UTILITY['STRUCTURAL']
    elif is_filler:
        utility = UTILITY['FILLER']
    elif is_closed_class or is_function_word:
        utility = UTILITY['FUNCTION']
    else:
        utility = UTILITY['CONTENT']

    flags = 0
    if is_multiword:
        flags |= FLAG['MULTIWORD']

    bucket = None
    cue_mask = 0

    ov = overrides['exact'].get(surface)
    if ov is None:
        ov = overrides['normalized'].get(key)
    if ov is not None:
        bucket, cue_mask = ov
        flags |= FLAG['MANUAL']
        if bucket == BUCKET['RELATION'] and utility == UTILITY['CONTENT']:
            utility = UTILITY['FUNCTION']
        if bucket == BUCKET['STRUCTURAL']:
            utility = UTILITY['STRUCTURAL']
        flags = set_utility(flags, utility)
        return bucket, cue_mask, flags

    if is_structural:
        bucket = BUCKET['STRUCTURAL']
        cue_mask = STRUCTURAL_CUE_MAP.get(surface, 0)
        flags = set_utility(flags, utility)
        return bucket, cue_mask, flags

    if is_closed_class:
        cue_mask |= matched_cue
        flags |= FLAG['CLOSED_CLASS']
        # Any closed-class connective/operator is a RELATION (rubric: connective /
        # logical operator / discourse link) -- not only the RELATION_CUES subset.
        bucket = BUCKET['RELATION']

    if is_filler:
        if key in _NORM_DISCOURSE:
            cue_mask |= LOGIC_CUE['CONJUNCTION']
        if bucket is None:
            bucket = BUCKET['UNKNOWN']

    if bucket is None and key in _NORM_ACTION:
        bucket = BUCKET['METHOD']

    if (bucket is None and is_multiword and not is_closed_class
            and not is_filler and not is_structural):
        bucket = BUCKET['CONCEPT']

    if bucket is None:
        bucket = BUCKET['TOPIC']
        flags |= FLAG['HEURISTIC']

    # Rubric tie-breaker: an abstract single-word content noun is a CONCEPT, not a
    # TOPIC (concrete->TOPIC, abstract->CONCEPT). Concrete detection is S2/EPA.
    if (bucket == BUCKET['TOPIC'] and not is_multiword
            and utility == UTILITY['CONTENT'] and cfg.is_abstract(key)):
        bucket = BUCKET['CONCEPT']

    if bucket == BUCKET['RELATION'] and utility == UTILITY['CONTENT']:
        utility = UTILITY['FUNCTION']

    flags = set_utility(flags, utility)
    return bucket, cue_mask, flags
