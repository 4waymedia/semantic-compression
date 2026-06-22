"""
normalize.py -- Surface normalization contract for the facets database (T2).

Facet keys are dictionary surface forms, so normalization must match what
tokenizer.py actually emits. This is the ONLY normalization applied to facet
matching and override-key resolution. The `forward` / `reverse` surfaces in
the dictionary stay byte-exact and untouched.

Contract (spec-facets-db.md v2, "Surface Normalization Contract"):

    Unicode:     NFC
    Case:        str.casefold()
    Whitespace:  collapse internal runs to a single space; strip ends
    Apostrophes: preserve as emitted by tokenizer.py (do NOT strip)
    Hyphens:     preserve (do NOT split "due-to" into "due to")
    Punctuation: preserve (key on the token as tokenized, not de-punctuated)
    normalization_version = 1   (stored in meta; bump on any change)

Pure function: input string -> output string. No side effects, no state.
Direct C translation target.
"""

from __future__ import annotations

import unicodedata

from config import NORMALIZATION_VERSION

__all__ = ['normalize_surface', 'NORMALIZATION_VERSION']


def normalize_surface(surface: str) -> str:
    """
    Normalize a surface form into a facet-matching key.

    Deterministic and idempotent: normalize_surface(normalize_surface(s))
    == normalize_surface(s) for all s.
    """
    # 1. Unicode NFC (canonical composition)
    s = unicodedata.normalize('NFC', surface)
    # 2. Caseless comparison form (handles ß, Σ, etc. better than .lower())
    s = s.casefold()
    # 3. Collapse internal whitespace runs to a single ASCII space; strip ends.
    #    .split() splits on any Unicode whitespace run and drops empties, so
    #    ' '.join(s.split()) collapses + strips in one pass.
    s = ' '.join(s.split())
    # Apostrophes, hyphens, and punctuation are intentionally preserved.
    return s
